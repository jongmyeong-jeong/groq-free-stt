"""Chunked recording upload session management.

Flow:
  1. start_session(title?) → returns {id, uploadUrl}.
  2. POST chunks with (seq, blob); append_chunk stores seq→bytes mapping.
     On seq==0, create a Note row with status='recording' (N1).
     Duplicate seq → ChunkSeqConflict → HTTP 409.
  3. finalize(session_id, title?) → validates contiguous seq 0..N-1;
     missing → ChunkGapError → HTTP 400.
     Writes concatenated .webm into audio_dir(), updates Note to 'pending',
     returns {audioPath, noteId}. Total duration <1s → RecordingTooShortError.

Notes:
  - Duration <1s check requires probing the audio file (ffprobe) in practice.
    For the MVP/testable contract, callers (server.py) may supply
    duration_sec_override; otherwise we approximate by byte-count of opus
    payload (>= 10 bytes per chunk = non-empty).
"""
from __future__ import annotations

import sqlite3
import shutil
import threading
import time
import uuid
from io import BytesIO
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from app.db import create_note, update_note
from app.paths import app_home, audio_basename, audio_dir


class ChunkSeqConflict(Exception):
    """Raised when a duplicate seq is appended."""

    def __init__(self, seq: int):
        super().__init__(f"duplicate seq: {seq}")
        self.seq = seq


class ChunkGapError(Exception):
    """Raised at finalize when the seq list has gaps."""

    def __init__(self, missing: list[int]):
        super().__init__(f"missing chunks: {missing}")
        self.missing = missing


class RecordingTooShortError(Exception):
    """Raised when total recording duration is under 1 second."""

    def __init__(self, min_sec: int = 1):
        super().__init__("recording too short")
        self.min_sec = min_sec


@dataclass
class RecordingSession:
    id: str
    note_id: str | None = None
    title: str | None = None
    started_at: float = field(default_factory=time.time)
    # seq → bytes; kept in memory and also flushed to disk by append_chunk.
    seen_seqs: set[int] = field(default_factory=set)
    chunk_count: int = 0
    max_seq: int = -1


_SESSIONS: dict[str, RecordingSession] = {}
_LOCK = threading.Lock()


def _recordings_root() -> Path:
    root = app_home() / "tmp" / "recordings"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _session_tmp_path(session_id: str) -> Path:
    """Return the session's working directory (one dir per session).

    Each uploaded chunk is stored as its own ``<seq:06d>.part`` file inside this
    directory; finalize concatenates them in seq order. Storing per-seq files
    (rather than appending to a single shared file in arrival order) removes the
    out-of-order / concurrent-append corruption window.
    """
    return _recordings_root() / session_id


def _chunk_part_path(session_id: str, seq: int) -> Path:
    """Return the per-chunk part-file path: ``<session_dir>/<seq:06d>.part``."""
    return _session_tmp_path(session_id) / f"{seq:06d}.part"


def start_session(title: str | None = None) -> dict:
    """Create a new recording session; return {id, uploadUrl}."""
    session_id = str(uuid.uuid4())
    with _LOCK:
        _SESSIONS[session_id] = RecordingSession(id=session_id, title=title)
    # Create the per-session directory that will hold the chunk part files.
    _session_tmp_path(session_id).mkdir(parents=True, exist_ok=True)
    return {
        "id": session_id,
        "uploadUrl": f"/api/recordings/{session_id}/chunk",
    }


def try_start_session(title: str | None = None) -> dict | None:
    """Create a new session only when no other session is active."""
    session_id = str(uuid.uuid4())
    with _LOCK:
        if _SESSIONS:
            return None
        _SESSIONS[session_id] = RecordingSession(id=session_id, title=title)
    try:
        _session_tmp_path(session_id).mkdir(parents=True, exist_ok=True)
    except Exception:
        with _LOCK:
            _SESSIONS.pop(session_id, None)
        raise
    return {
        "id": session_id,
        "uploadUrl": f"/api/recordings/{session_id}/chunk",
    }


def get_session(session_id: str) -> RecordingSession | None:
    with _LOCK:
        return _SESSIONS.get(session_id)


def close_session(session_id: str) -> None:
    """Drop in-memory session state and best-effort remove the temp session dir."""
    with _LOCK:
        _SESSIONS.pop(session_id, None)
    shutil.rmtree(_session_tmp_path(session_id), ignore_errors=True)


def sweep_orphan_session_dirs() -> int:
    """Remove per-session temp dirs that have no live in-memory session.

    Run once at server startup: ``_SESSIONS`` is empty there, so every leftover
    directory belongs to a session whose process died without finalizing or
    closing (the DB-level recovery in the lifespan handler does not touch the
    filesystem). The live-session check guards against deleting an active
    recording if this is ever called mid-run. Returns the number removed.
    """
    root = app_home() / "tmp" / "recordings"
    if not root.exists():
        return 0
    with _LOCK:
        live = set(_SESSIONS)
    removed = 0
    for child in root.iterdir():
        if child.is_dir() and child.name not in live:
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed


def append_chunk(
    conn: sqlite3.Connection | None,
    session_id: str,
    chunk_bytes: bytes,
    seq: int,
) -> dict:
    """Append one chunk to the session; validate seq uniqueness.

    N1: on seq==0, create a Note row with status='recording' so the
    session shows up in GET /api/notes for recovery.
    Returns {noteId, bytes_written}.
    """
    return append_chunk_stream(conn, session_id, BytesIO(chunk_bytes), seq)


def append_chunk_stream(
    conn: sqlite3.Connection | None,
    session_id: str,
    stream: BinaryIO,
    seq: int,
    *,
    chunk_size: int = 1024 * 1024,
) -> dict:
    """Persist an uploaded chunk from a file-like object without buffering it.

    Keeps the upload hot path on a streaming write path so large chunks do not
    require an additional in-memory bytes copy at the HTTP layer.
    """
    session_title, note_id, needs_note = _reserve_chunk(session_id, seq)
    part_path = _chunk_part_path(session_id, seq)
    part_path.parent.mkdir(parents=True, exist_ok=True)
    bytes_written = 0
    try:
        # Each seq writes its own file ("wb"), so concurrent uploads for
        # different seqs never share a file handle and cannot interleave.
        with part_path.open("wb") as handle:
            while True:
                part = stream.read(chunk_size)
                if not part:
                    break
                handle.write(part)
                bytes_written += len(part)
        if needs_note:
            if conn is None:
                raise RuntimeError("append_chunk requires conn when creating a note")
            # Create the note first so its id can suffix the audio basename —
            # without the suffix, same-title recordings collide on the same
            # .webm and finalize silently overwrites the previous audio.
            note = create_note(
                conn,
                title=session_title,
                status="recording",
            )
            basename = audio_basename(
                session_title, datetime.now(), note_id=note["id"]
            )
            update_note(
                conn,
                note["id"],
                audio_path=str(audio_dir() / f"{basename}.webm"),
            )
            note_id = _attach_note_id(session_id, note["id"])
        return {"noteId": note_id, "bytes_written": bytes_written}
    except Exception:
        _rollback_reserved_chunk(session_id, seq)
        raise


def _reserve_chunk(session_id: str, seq: int) -> tuple[str | None, str | None, bool]:
    if seq < 0:
        raise ValueError("seq must be >= 0")
    with _LOCK:
        session = _SESSIONS.get(session_id)
        if session is None:
            raise KeyError(f"unknown recording session: {session_id}")
        if seq in session.seen_seqs:
            raise ChunkSeqConflict(seq)
        session.seen_seqs.add(seq)
        session.chunk_count += 1
        if seq > session.max_seq:
            session.max_seq = seq
        return session.title, session.note_id, seq == 0 and session.note_id is None


def _attach_note_id(session_id: str, note_id: str) -> str:
    with _LOCK:
        session = _SESSIONS.get(session_id)
        if session is None:
            raise KeyError(f"unknown recording session: {session_id}")
        session.note_id = note_id
        return session.note_id


def _rollback_reserved_chunk(session_id: str, seq: int) -> None:
    with _LOCK:
        session = _SESSIONS.get(session_id)
        if session is None:
            return
        if seq not in session.seen_seqs:
            return
        session.seen_seqs.remove(seq)
        session.chunk_count = max(0, session.chunk_count - 1)
        session.max_seq = max(session.seen_seqs, default=-1)


def get_active_session_count() -> int:
    """Return the number of currently active recording sessions."""
    with _LOCK:
        return len(_SESSIONS)


def finalize(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    title: str | None = None,
    duration_sec: float | None = None,
    live: bool = False,
) -> dict:
    """Validate contiguous seqs, move file to audio_dir, update note.

    If any seq in [0, max(seen)) is missing → ChunkGapError.
    If no chunks seen → ChunkGapError(missing=[0]).
    If duration_sec is provided and < 1.0 → RecordingTooShortError.
    Returns {'audioPath', 'noteId'}.
    """
    with _LOCK:
        session = _SESSIONS.get(session_id)
        if session is None:
            raise KeyError(f"unknown recording session: {session_id}")

    if not session.seen_seqs:
        raise ChunkGapError(missing=[0])

    expected_count = session.max_seq + 1
    if session.chunk_count != expected_count:
        missing = _find_missing_seqs(session.seen_seqs, session.max_seq)
        raise ChunkGapError(missing=missing)

    if duration_sec is not None and duration_sec < 1.0:
        raise RecordingTooShortError(min_sec=1)

    if session.note_id is None:
        # seq=0 must have been appended to create the note, but be defensive.
        # Note first, path second — the note id suffix keeps same-title
        # recordings from colliding (audio_path is set by update_note below).
        note = create_note(conn, title=title or session.title)
        session.note_id = note["id"]
        basename = audio_basename(
            title or session.title, datetime.now(), note_id=note["id"]
        )
        dest = audio_dir() / f"{basename}.webm"
    else:
        note = _fetch_note(conn, session.note_id)
        if note is None:
            raise KeyError(f"note not found: {session.note_id}")
        dest = Path(note["audioPath"]) if note["audioPath"] else (
            audio_dir()
            / (
                audio_basename(
                    title or session.title,
                    datetime.now(),
                    note_id=session.note_id,
                )
                + ".webm"
            )
        )

    session_dir = _session_tmp_path(session_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Concatenate the per-seq part files in ascending seq order (NOT arrival
    # order), so out-of-order uploads still produce a correctly-ordered stream.
    part_files = sorted(session_dir.glob("*.part"), key=lambda p: int(p.stem))
    # Write to a sibling temp file first, then rename atomically — an
    # interrupted concat (disk full / crash) never leaves a corrupt file at the
    # permanent audio path. tmp_dest lives in dest.parent so the rename is
    # always same-filesystem.
    tmp_dest = dest.with_name(dest.name + ".tmp")
    try:
        with tmp_dest.open("wb") as dst:
            for part_file in part_files:
                with part_file.open("rb") as src:
                    shutil.copyfileobj(src, dst)
        tmp_dest.replace(dest)
    finally:
        tmp_dest.unlink(missing_ok=True)
        shutil.rmtree(session_dir, ignore_errors=True)

    # live=True → 'finalizing' (real-time pre-transcription path); default 'pending' for legacy file-upload finalize
    status = "finalizing" if live else "pending"
    update_note(
        conn,
        session.note_id,
        status=status,
        audio_path=str(dest),
    )

    with _LOCK:
        _SESSIONS.pop(session_id, None)

    return {"audioPath": str(dest), "noteId": session.note_id}


def _fetch_note(conn: sqlite3.Connection, note_id: str) -> dict | None:
    # Tiny local wrapper; keeps the import graph flat.
    from app.db import get_note

    return get_note(conn, note_id)


def _find_missing_seqs(seen_seqs: set[int], max_seq: int) -> list[int]:
    return [seq for seq in range(max_seq + 1) if seq not in seen_seqs]
