"""FastAPI app for lonta.

Serves the static SPA (built into app/static/) and the /api/* routes listed in
plan §4.2. Same-origin; no CORS; all SSE emitters add:
  - Content-Type: text/event-stream
  - X-Accel-Buffering: no
  - Cache-Control: no-cache
and intersperse 1s `: keep-alive\\n\\n` comments to defeat intermediate
buffering.

SSE event wire format:
    event: <name>\\n
    data: <one JSON object>\\n
    \\n
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

import platform

from app import logger
from app import batch_transcribe
from app import db as db_mod
from app import paths
from app import prompt as prompt_mod
from app import recording_chunks
from app import recordings as recordings_mod
from app import server_jobs

# ---------------------------------------------------------------------------
# File upload whitelist (plan §5 AC-5 "drag-drop whitelist").
# ---------------------------------------------------------------------------
_ALLOWED_EXT = {"m4a", "wav", "mp3", "webm", "aac", "ogg", "flac", "m4r"}

# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}
_SYSTEM_INFO_CACHE_TTL_SEC = 5.0
_SYSTEM_INFO_CACHE: dict[str, float | dict | None] = {
    "expires_at": 0.0,
    "value": None,
}
_SYSTEM_INFO_LOCK = threading.Lock()

# ---------------------------------------------------------------------------
# Task registry — tracks in-flight finalize producers for graceful shutdown
# ---------------------------------------------------------------------------
_FINALIZE_TASKS: set[asyncio.Task] = set()


def _sidecar_segments_path(note_id: str, seq: int) -> Path:
    """Return the path for the per-batch segments sidecar JSON file."""
    return paths.audio_dir() / f"segments-{note_id}-{seq:03d}.json"


def _groq_api_key_set() -> bool:
    """Return True when GROQ_API_KEY is present in the environment."""
    import os
    return bool(os.environ.get("GROQ_API_KEY"))


def _sse_event(name: str, payload: dict) -> bytes:
    """Format an SSE event: `event:` + `data:` + blank line."""
    body = json.dumps(payload, ensure_ascii=False)
    return f"event: {name}\ndata: {body}\n\n".encode("utf-8")


def _sse_keepalive() -> bytes:
    return b": keep-alive\n\n"


async def _merge_with_keepalive(
    queue: asyncio.Queue[bytes | None],
    keepalive_interval: float = 1.0,
) -> AsyncIterator[bytes]:
    """Yield items from `queue` and emit a `: keep-alive\n\n` comment whenever
    the producer is silent for `keepalive_interval` seconds. A sentinel `None`
    terminates the stream."""
    while True:
        try:
            item = await asyncio.wait_for(queue.get(), timeout=keepalive_interval)
        except asyncio.TimeoutError:
            yield _sse_keepalive()
            continue
        if item is None:
            return
        yield item


async def _write_upload_file(
    upload: UploadFile,
    destination: Path,
    *,
    chunk_size: int = 1024 * 1024,
) -> None:
    await upload.seek(0)
    await asyncio.to_thread(
        _copy_fileobj_to_path,
        upload.file,
        destination,
        chunk_size=chunk_size,
    )


def _atomic_write_text(destination: Path, payload: str) -> None:
    """Write `payload` to `destination` atomically via a sibling .tmp file.

    Combines write + rename into a single sync call so the async caller pays
    for one thread switch instead of two.
    """
    tmp = destination.with_suffix(".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, destination)


def _copy_fileobj_to_path(source, destination: Path, *, chunk_size: int = 1024 * 1024) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with destination.open("wb") as output:
        while True:
            chunk = source.read(chunk_size)
            if not chunk:
                return written
            output.write(chunk)
            written += len(chunk)


def _append_recording_chunk_sync(
    session_id: str,
    stream,
    seq: int,
    *,
    needs_note: bool,
) -> dict:
    if needs_note:
        with db_mod.open_db() as conn:
            return recordings_mod.append_chunk_stream(conn, session_id, stream, seq)
    return recordings_mod.append_chunk_stream(None, session_id, stream, seq)


def invalidate_system_info_cache() -> None:
    with _SYSTEM_INFO_LOCK:
        _SYSTEM_INFO_CACHE["expires_at"] = 0.0
        _SYSTEM_INFO_CACHE["value"] = None


def _build_system_info() -> dict:
    import os
    return {
        "os": platform.system().lower(),
        "arch": platform.machine().lower(),
        "ffmpegAvailable": shutil.which("ffmpeg") is not None,
        "groqConfigured": bool(os.environ.get("GROQ_API_KEY")),
    }


def get_cached_system_info() -> dict:
    now = time.monotonic()
    cached = _SYSTEM_INFO_CACHE.get("value")
    expires_at = _SYSTEM_INFO_CACHE.get("expires_at", 0.0)
    if isinstance(cached, dict) and isinstance(expires_at, float) and expires_at > now:
        return cached
    with _SYSTEM_INFO_LOCK:
        cached = _SYSTEM_INFO_CACHE.get("value")
        expires_at = _SYSTEM_INFO_CACHE.get("expires_at", 0.0)
        if isinstance(cached, dict) and isinstance(expires_at, float) and expires_at > now:
            return cached
        payload = _build_system_info()
        _SYSTEM_INFO_CACHE["value"] = payload
        _SYSTEM_INFO_CACHE["expires_at"] = now + _SYSTEM_INFO_CACHE_TTL_SEC
        return payload


def _get_note_or_404(conn, note_id: str) -> dict:
    note = db_mod.get_note(conn, note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="note not found")
    return note



# ---------------------------------------------------------------------------
# Pydantic request bodies
# ---------------------------------------------------------------------------


class PatchNoteJSON(BaseModel):
    title: str | None = None
    status: str | None = None



class RecordingStartJSON(BaseModel):
    title: str | None = None


class FinalizeRequest(BaseModel):
    title: str | None = None
    duration_sec: float | None = Field(default=None, alias="durationSec")

    model_config = ConfigDict(populate_by_name=True)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ARG001
        # Startup: recover notes stuck in 'finalizing' (crashed mid-transcription).
        with db_mod.open_db() as conn:
            rows = conn.execute(
                "SELECT id FROM notes WHERE status = 'finalizing'"
            ).fetchall()
            for row in rows:
                nid = row["id"]
                db_mod.update_note(conn, nid, status="transcription_failed")
                logger.info("startup_recovered_finalizing_note", {"note_id": nid})
            # Also recover generic stuck rows (recording/pending).
            db_mod.migrate_stuck_recordings(conn)

        # One-shot relocation of legacy transcripts directory.
        moved = paths.migrate_legacy_transcripts_dir()
        if moved:
            logger.info("transcripts_dir_migrated", {"moved_files": moved})

        # Remove per-session recording temp dirs left by crashed sessions
        # (the DB recovery above only touches rows, not the filesystem).
        swept = recordings_mod.sweep_orphan_session_dirs()
        if swept:
            logger.info("orphan_recording_dirs_swept", {"count": swept})

        yield

        # Shutdown: wait for in-flight finalize tasks (max 120 s).
        if _FINALIZE_TASKS:
            logger.info(
                "shutdown_awaiting_finalize_tasks",
                {"count": len(_FINALIZE_TASKS)},
            )
            await asyncio.wait(list(_FINALIZE_TASKS), timeout=120)

    app = FastAPI(title="lonta", version="0.1.0", lifespan=lifespan)

    # Request-logging middleware (JSONL logger configured in app/__init__.py).
    @app.middleware("http")
    async def _access_log(request: Request, call_next):  # type: ignore[no-redef]
        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.error(
                "http_request_error",
                {
                    "method": request.method,
                    "path": request.url.path,
                    "elapsed_ms": elapsed_ms,
                },
            )
            raise
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "http_request",
            {
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "elapsed_ms": elapsed_ms,
            },
        )
        return response

    # ------------------------------------------------------------------
    # System info
    # ------------------------------------------------------------------
    @app.get("/api/system/info")
    def get_system_info() -> dict:
        return get_cached_system_info()

    # ------------------------------------------------------------------
    # Notes CRUD
    # ------------------------------------------------------------------
    @app.get("/api/notes")
    def list_notes() -> list[dict]:
        with db_mod.open_db() as conn:
            return db_mod.list_notes(conn)

    @app.post("/api/notes", status_code=status.HTTP_201_CREATED)
    async def create_note_endpoint(
        request: Request,
        file: UploadFile | None = File(default=None),
        title: str | None = Form(default=None),
    ) -> JSONResponse:
        """Multipart OR JSON.

        Title precedence (plan §4.2 / B3): multipart `title` > JSON `title` >
        literal `"untitled"`.
        """
        ctype = request.headers.get("content-type", "")
        json_title: str | None = None
        json_audio_path: str | None = None

        if ctype.startswith("application/json"):
            try:
                raw = await request.body()
                if raw:
                    payload = json.loads(raw.decode("utf-8"))
                    if isinstance(payload, dict):
                        json_title = payload.get("title") if isinstance(
                            payload.get("title"), str
                        ) else None
                        json_audio_path = payload.get("audioPath") if isinstance(
                            payload.get("audioPath"), str
                        ) else None
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

        effective_title = title if title else json_title

        audio_path: str | None = json_audio_path
        if file is not None and file.filename:
            ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
            if ext not in _ALLOWED_EXT:
                return JSONResponse(
                    status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    content={"error": "지원하지 않는 파일 형식", "ext": ext},
                )
            dest_dir = paths.audio_dir()
            dest_name = f"{uuid.uuid4()}.{ext}"
            dest = dest_dir / dest_name
            await _write_upload_file(file, dest)
            audio_path = str(dest)

        def _create_note_sync() -> dict:
            with db_mod.open_db() as conn:
                return db_mod.create_note(
                    conn, title=effective_title, audio_path=audio_path
                )

        note = await asyncio.to_thread(_create_note_sync)
        return JSONResponse(status_code=status.HTTP_201_CREATED, content=note)

    @app.get("/api/notes/{note_id}")
    def get_note(note_id: str) -> dict:
        with db_mod.open_db() as conn:
            note = _get_note_or_404(conn, note_id)
        return note

    @app.patch("/api/notes/{note_id}")
    def patch_note(note_id: str, body: PatchNoteJSON) -> dict:
        fields = body.model_dump(exclude_none=True)
        with db_mod.open_db() as conn:
            note = db_mod.update_note(conn, note_id, **fields)
        if note is None:
            raise HTTPException(status_code=404, detail="note not found")
        return note

    @app.delete(
        "/api/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT
    )
    def delete_note(note_id: str, deleteAudio: bool = False) -> Response:
        with db_mod.open_db() as conn:
            db_mod.delete_note(conn, note_id, delete_audio=deleteAudio)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------
    @app.post(
        "/api/notes/{note_id}/cancel",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def cancel_job(note_id: str) -> Response:
        await server_jobs.cancel(note_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------
    # Transcript fetch
    # ------------------------------------------------------------------
    @app.get("/api/notes/{note_id}/transcript")
    def get_transcript(note_id: str) -> dict:
        with db_mod.open_db() as conn:
            note = _get_note_or_404(conn, note_id)
        if not note.get("transcriptPath"):
            raise HTTPException(status_code=404, detail="transcript not found")
        try:
            content = Path(note["transcriptPath"]).read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            raise HTTPException(status_code=404, detail="transcript file missing")
        return {"content": content, "segments": []}

    # ------------------------------------------------------------------
    # Transcript download (.md)
    # ------------------------------------------------------------------
    @app.get("/api/notes/{note_id}/download")
    async def download_transcript(note_id: str) -> FileResponse:
        """Generate and return a .md transcript file for download.

        Segment data is read from per-batch sidecar JSON files written during
        transcription (option B — no schema change). Failed ranges are derived
        from recording_chunks rows with status='failed'.
        """
        import glob as glob_mod
        import re as re_mod
        import unicodedata
        from datetime import timezone

        from app import markdown_writer

        def _load_note_and_chunks() -> tuple[dict | None, list[dict]]:
            with db_mod.open_db() as conn:
                note_local = db_mod.get_note(conn, note_id)
                chunks_local = (
                    recording_chunks.get_chunks(conn, note_id)
                    if note_local is not None
                    else []
                )
            return note_local, chunks_local

        note, chunks = await asyncio.to_thread(_load_note_and_chunks)
        if note is None:
            raise HTTPException(status_code=404, detail="note not found")
        if not chunks:
            raise HTTPException(status_code=404, detail="no recording chunks found")

        # 3. Collect segments from sidecar files, sorted by seq.
        _seq_re = re_mod.compile(r"-(\d+)\.json$")

        def _read_sidecars() -> list[dict]:
            pattern = str(paths.audio_dir() / f"segments-{note_id}-*.json")
            files = sorted(
                glob_mod.glob(pattern),
                key=lambda p: int(m.group(1)) if (m := _seq_re.search(p)) else 0,
            )
            collected: list[dict] = []
            for sf in files:
                try:
                    data = json.loads(Path(sf).read_text(encoding="utf-8"))
                    collected.extend(data.get("segments", []))
                except (OSError, json.JSONDecodeError) as exc:
                    logger.warning(
                        "segments_sidecar_read_error",
                        {"note_id": note_id, "file": sf, "error": str(exc)},
                    )
            return collected

        all_segments = await asyncio.to_thread(_read_sidecars)

        # 4. Build failed_ranges from chunks with status='failed'.
        failed_ranges = [
            {"start_ms": c["start_ms"], "end_ms": c["end_ms"]}
            for c in chunks
            if c.get("status") == "failed"
        ]

        # 5. Parse recorded_at from note's createdAt ISO string.
        try:
            recorded_at = datetime.fromisoformat(note["createdAt"])
        except (KeyError, ValueError):
            recorded_at = datetime.now(timezone.utc)

        note_title: str = note.get("title") or "untitled"

        # 6. Write .md to a temp file.
        def _write_md() -> Path:
            with tempfile.NamedTemporaryFile(
                suffix=".md", delete=False, mode="w", encoding="utf-8"
            ) as tmp:
                path = Path(tmp.name)
            markdown_writer.write_transcript_md(
                title=note_title,
                recorded_at=recorded_at,
                segments=all_segments,
                failed_ranges=failed_ranges,
                output_path=path,
            )
            return path

        tmp_path = await asyncio.to_thread(_write_md)

        # 7. Build a safe filename (ASCII + RFC 5987 UTF-8 fallback).
        # Local-time stamp down to seconds so every recording downloads under a
        # unique name instead of piling up "name (1).md" browser dedupe suffixes.
        # The 'untitled' placeholder is omitted — it adds no information.
        stamp = recorded_at.astimezone().strftime("%Y-%m-%d-%H%M%S")
        has_real_title = note_title != "untitled"
        # ASCII-safe: strip non-ASCII for the plain filename= token.
        ascii_title = (
            unicodedata.normalize("NFKD", note_title)
            .encode("ascii", "ignore")
            .decode("ascii")
            .strip()
        )
        ascii_filename = (
            f"{stamp}-{ascii_title}.md"
            if has_real_title and ascii_title
            else f"{stamp}.md"
        )
        # Percent-encode the UTF-8 version for filename*= (RFC 5987).
        from urllib.parse import quote as _url_quote
        utf8_filename = (
            f"{stamp}-{note_title}.md" if has_real_title else f"{stamp}.md"
        )
        encoded_filename = _url_quote(utf8_filename, safe="")
        content_disposition = (
            f"attachment; filename=\"{ascii_filename}\"; "
            f"filename*=UTF-8''{encoded_filename}"
        )

        return FileResponse(
            path=str(tmp_path),
            media_type="text/markdown",
            headers={"Content-Disposition": content_disposition},
        )

    # ------------------------------------------------------------------
    # Events proxy (plan extension): re-emits the most recent cancel signal.
    # Minimal implementation: confirm there is an active job and stream
    # keep-alives until it completes or is cancelled.
    # ------------------------------------------------------------------
    @app.get("/api/notes/{note_id}/events")
    async def note_events(note_id: str, request: Request) -> StreamingResponse:
        async def producer(queue: asyncio.Queue[bytes | None]) -> None:
            while True:
                handle = await server_jobs.get(note_id)
                if handle is None or handle.status != "running":
                    break
                if await request.is_disconnected():
                    break
                await asyncio.sleep(1.0)
            final_status = "completed"
            h = await server_jobs.get(note_id)
            if h is not None:
                final_status = h.status
            await queue.put(
                _sse_event("complete", {"status": final_status})
            )
            await queue.put(None)

        return _sse_stream(producer)

    # ------------------------------------------------------------------
    # Recordings
    # ------------------------------------------------------------------
    @app.post("/api/recordings", status_code=status.HTTP_201_CREATED)
    async def create_recording(body: RecordingStartJSON | None = None) -> JSONResponse:
        # 1. groq API key check — return 503 when GROQ_API_KEY is missing.
        if not _groq_api_key_set():
            return JSONResponse(
                status_code=503,
                content={"error": "groq_api_key_missing"},
            )

        # 2. Concurrent-recording guard + session creation are atomic.
        session = recordings_mod.try_start_session(
            title=(body.title if body is not None else None)
        )
        if session is None:
            return JSONResponse(
                status_code=409,
                content={"error": "concurrent_recording"},
            )

        return JSONResponse(
            status_code=status.HTTP_201_CREATED, content=session
        )

    @app.post("/api/recordings/{session_id}/chunk")
    async def append_recording_chunk(
        session_id: str,
        chunk: UploadFile = File(...),
        seq: int = Form(...),
    ) -> dict:
        if seq < 0:
            raise HTTPException(status_code=400, detail="seq must be >= 0")
        await chunk.seek(0)
        session = recordings_mod.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="recording session not found")

        try:
            return await asyncio.to_thread(
                _append_recording_chunk_sync,
                session_id,
                chunk.file,
                int(seq),
                needs_note=session.note_id is None,
            )
        except recordings_mod.ChunkSeqConflict as exc:
            return JSONResponse(  # type: ignore[return-value]
                status_code=status.HTTP_409_CONFLICT,
                content={"error": "duplicate seq", "seq": exc.seq},
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="recording session not found")

    @app.post("/api/recordings/{session_id}/finalize")
    async def finalize_recording(
        session_id: str,
        request: Request,
        body: FinalizeRequest | None = None,
    ) -> StreamingResponse:
        title = body.title if body is not None else None
        duration = body.duration_sec if body is not None else None

        # Validate session exists before entering SSE producer.
        session_check = recordings_mod.get_session(session_id)
        if session_check is None:
            raise HTTPException(status_code=404, detail="recording session not found")

        async def producer(queue: asyncio.Queue[bytes | None]) -> None:
            # Intentionally does NOT set cancel_event — finalize work must
            # complete regardless of client connection state (only logs
            # disconnect for observability).
            note_id_ref: list[str | None] = [None]

            async def _disconnect_logger() -> None:
                while True:
                    if await request.is_disconnected():
                        logger.info(
                            "finalize_client_disconnected",
                            {"session_id": session_id, "note_id": note_id_ref[0]},
                        )
                        return
                    await asyncio.sleep(1.0)

            disconnect_task = asyncio.create_task(_disconnect_logger())
            try:
                # 1. Finalize session — move webm to audio_dir, set status='finalizing'.
                def _finalize_sync() -> dict:
                    with db_mod.open_db() as conn:
                        return recordings_mod.finalize(
                            conn,
                            session_id,
                            title=title,
                            duration_sec=duration,
                            live=True,
                        )

                try:
                    result = await asyncio.to_thread(_finalize_sync)
                except recordings_mod.ChunkGapError as exc:
                    await queue.put(
                        _sse_event(
                            "error",
                            {
                                "error": "missing chunks",
                                "missing": exc.missing,
                                "canRetry": False,
                            },
                        )
                    )
                    return
                except recordings_mod.RecordingTooShortError as exc:
                    # AC6(A): preserve h14 behavior — set transcription_failed.
                    session_obj = recordings_mod.get_session(session_id)
                    note_id_short = (
                        session_obj.note_id if session_obj is not None else None
                    )
                    if note_id_short:
                        def _mark_failed() -> None:
                            with db_mod.open_db() as conn:
                                db_mod.update_note(
                                    conn, note_id_short, status="transcription_failed"
                                )
                        await asyncio.to_thread(_mark_failed)
                    await queue.put(
                        _sse_event(
                            "error",
                            {
                                "error": "recording too short",
                                "min_sec": exc.min_sec,
                                "canRetry": False,
                            },
                        )
                    )
                    return
                except KeyError:
                    await queue.put(
                        _sse_event(
                            "error",
                            {"error": "session not found", "canRetry": False},
                        )
                    )
                    return

                note_id: str = result["noteId"]
                note_id_ref[0] = note_id
                session_audio_path: str = result["audioPath"]

                # 2. Run batch transcription in a worker thread.
                prompt = prompt_mod.load()
                workdir = Path(tempfile.mkdtemp(prefix="lonta_batch_"))
                try:
                    batch_result = await asyncio.to_thread(
                        batch_transcribe.run_batch_transcription,
                        Path(session_audio_path),
                        workdir=workdir,
                        prompt=prompt,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "batch_transcription_error",
                        {"note_id": note_id, "error": str(exc)},
                    )
                    def _mark_failed_bt() -> None:
                        with db_mod.open_db() as conn:
                            db_mod.update_note(
                                conn, note_id, status="transcription_failed"
                            )
                    await asyncio.to_thread(_mark_failed_bt)
                    await queue.put(
                        _sse_event(
                            "error",
                            {"error": "transcription_failed", "canRetry": False},
                        )
                    )
                    return
                finally:
                    # The batch runner deletes its piece files; remove the
                    # workdir itself too — on success AND the exception path
                    # (a leaked dir can hold a ~45MB re-encoded file).
                    await asyncio.to_thread(
                        shutil.rmtree, workdir, ignore_errors=True
                    )

                # 3. Persist chunk rows, sidecar files, and .md in one worker thread.
                def _persist_batch_result() -> Path | None:
                    with db_mod.open_db() as conn:
                        for piece in batch_result.pieces:
                            chunk_id = recording_chunks.insert_chunk(
                                conn, note_id, piece.seq, piece.start_ms, piece.end_ms
                            )
                            if piece.ok:
                                recording_chunks.update_chunk_status(
                                    conn, chunk_id, "success", piece.text
                                )
                                # Sidecar JSON for segments (only when segments exist).
                                if piece.segments:
                                    sidecar = _sidecar_segments_path(note_id, piece.seq)
                                    sidecar_payload = json.dumps(
                                        {"seq": piece.seq, "segments": piece.segments},
                                        ensure_ascii=False,
                                    )
                                    _atomic_write_text(sidecar, sidecar_payload)
                            else:
                                recording_chunks.update_chunk_status(
                                    conn, chunk_id, "failed"
                                )

                        if batch_result.all_failed:
                            db_mod.update_note(
                                conn, note_id, status="transcription_failed"
                            )
                            return None

                        # Write auto-save .md (pure text — no markdown_writer).
                        note_row = db_mod.get_note(conn, note_id)
                        title_local = note_row["title"] if note_row else title
                        basename_local = paths.audio_basename(
                            title_local, datetime.now(), note_id=note_id
                        )
                        out_path_local = paths.transcripts_dir() / f"{basename_local}.md"
                        out_path_local.parent.mkdir(parents=True, exist_ok=True)
                        text_body = batch_result.merged_text_with_failure_markers()
                        _atomic_write_text(out_path_local, text_body)
                        db_mod.update_note(
                            conn,
                            note_id,
                            status="transcribed",
                            transcript_path=str(out_path_local),
                        )
                        return out_path_local

                out_path = await asyncio.to_thread(_persist_batch_result)

                # 4. Emit SSE event.
                if batch_result.all_failed:
                    await queue.put(
                        _sse_event(
                            "error",
                            {"error": "transcription_failed", "canRetry": False},
                        )
                    )
                else:
                    await queue.put(
                        _sse_event(
                            "complete",
                            {
                                "status": "completed",
                                "noteId": note_id,
                                "audioPath": session_audio_path,
                                "transcriptPath": str(out_path),
                                "partialFailure": batch_result.partial_failure,
                                "failedRanges": batch_result.failed_ranges,
                            },
                        )
                    )

            finally:
                # 7. Clean up session state regardless of outcome.
                recordings_mod.close_session(session_id)
                await queue.put(None)
                disconnect_task.cancel()
                try:
                    await disconnect_task
                except asyncio.CancelledError:
                    pass

        return _sse_stream(producer, track=True)

    # ------------------------------------------------------------------
    # Jobs list
    # ------------------------------------------------------------------
    @app.get("/api/jobs")
    async def get_jobs() -> list[dict]:
        return await server_jobs.list_jobs()

    # ------------------------------------------------------------------
    # Static SPA — mounted LAST so /api/* wins.
    # ------------------------------------------------------------------
    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        static_root = static_dir.resolve()

        @app.get("/{full_path:path}", include_in_schema=False)
        async def _spa_fallback(full_path: str) -> FileResponse:
            candidate = (static_dir / full_path).resolve()
            # Containment guard: never serve a file resolved outside static_dir
            # (defends against `../` path-traversal in the URL).
            if candidate.is_relative_to(static_root) and candidate.is_file():
                return FileResponse(candidate)
            # Hashed build assets must 404 when missing; otherwise the SPA
            # fallback would serve index.html (text/html) for a stale chunk
            # URL, triggering "Failed to load module script ... MIME type
            # text/html" in browsers with a cached index.html.
            if full_path.startswith("assets/"):
                raise HTTPException(status_code=404, detail="asset not found")
            return FileResponse(static_dir / "index.html")

        app.mount(
            "/", StaticFiles(directory=str(static_dir), html=True), name="static"
        )

    return app


def _sse_stream(producer, *, track: bool = False) -> StreamingResponse:
    """Wrap a producer coroutine in an SSE StreamingResponse.

    `producer(queue)` must push encoded events into the queue and finally push
    a `None` sentinel.

    When ``track=True`` the created task is registered in ``_FINALIZE_TASKS``
    so the lifespan shutdown handler can await its completion.  Only finalize
    producers should set this flag — other endpoints share this helper and
    must not pollute the registry.
    """
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def _run() -> None:
        try:
            await producer(queue)
        except Exception as exc:  # noqa: BLE001 — ensure stream closes
            logger.error("sse_producer_crash", {"error": str(exc)})
            # Detail stays in the server log — clients get a generic code
            # (internal paths/stack fragments must not reach the browser).
            await queue.put(
                _sse_event("error", {"message": "internal_error", "canRetry": True})
            )
            await queue.put(None)

    task = asyncio.create_task(_run())
    if track:
        _FINALIZE_TASKS.add(task)
        task.add_done_callback(_FINALIZE_TASKS.discard)
    return StreamingResponse(
        _merge_with_keepalive(queue),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


# Module-level app instance for `uvicorn app.server:app`.
app = create_app()
