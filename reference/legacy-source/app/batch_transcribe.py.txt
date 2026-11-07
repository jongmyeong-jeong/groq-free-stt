"""Batch transcription orchestrator for post-recording Groq upload.

Entry point: :func:`run_batch_transcription` — takes a webm path, re-encodes
to ogg/flac, optionally splits on silence boundaries, transcribes each piece
sequentially with retry, and returns a :class:`BatchResult`.

This module is synchronous; callers in the async server use
``asyncio.to_thread(run_batch_transcription, ...)``.

Constants (spec defaults from groq-batch-transcribe-plan.md §Phase 1):
  SPLIT_THRESHOLD_BYTES    — 20 MB; split only when re-encoded file exceeds this
  BOUNDARY_SEARCH_WINDOW_SEC — ±30 s search window around each candidate cut
  PIECE_RETRY_SLEEP_SEC    — 5 s sleep between network-error retries per piece
  PIECE_MAX_RETRIES        — 5 attempts total per piece (1 initial + 4 retries)
"""
from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from app.audio_io import AudioIOError, load_pcm_16k_mono_window, reencode_for_upload
from app.groq_client import (
    GroqApiKeyMissing,
    GroqClientError,
    GroqNetworkError,
    GroqRateLimitError,
    GroqServerError,
    TranscribeResult,
    transcribe_audio,
)

# Re-use VAD constants so silence detection is consistent with the rest of the
# pipeline.  We deliberately do NOT import detect_speech functions — only the
# frame-level constants.
from app.vad import FRAME_LEN, HOP_LEN, THRESHOLD_FLOOR

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Spec constants
# ---------------------------------------------------------------------------

SPLIT_THRESHOLD_BYTES: int = 20 * 1024 * 1024      # 20 MB
BOUNDARY_SEARCH_WINDOW_SEC: float = 30.0            # ±30 s around each candidate cut
PIECE_RETRY_SLEEP_SEC: float = 5.0                  # sleep between network retries
PIECE_MAX_RETRIES: int = 5                          # total attempts (1 initial + 4 retries)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SplitRange:
    """One contiguous piece of the audio, identified by sequence number and time bounds."""
    seq: int
    start_sec: float
    end_sec: float


@dataclass
class PieceResult:
    """Transcription outcome for a single :class:`SplitRange`."""
    seq: int
    start_ms: int
    end_ms: int
    ok: bool
    text: str | None
    # Segments in sidecar format: {"start_ms": int, "end_ms": int, "text": str}
    segments: list[dict]
    error_type: str | None


@dataclass
class BatchResult:
    """Aggregated result of all pieces.

    Properties
    ----------
    all_failed       — every piece failed (no text recovered)
    partial_failure  — at least one piece failed but not all
    """
    pieces: list[PieceResult]
    failed_ranges: list[dict]  # [{start_ms: int, end_ms: int}]

    @property
    def all_failed(self) -> bool:
        return bool(self.pieces) and all(not p.ok for p in self.pieces)

    @property
    def partial_failure(self) -> bool:
        oks = [p.ok for p in self.pieces]
        return any(oks) and not all(oks)

    def merged_text_with_failure_markers(self) -> str:
        """Text join with ``[hh:mm:ss–hh:mm:ss 전사 실패 구간]`` markers at failure positions.

        Marker uses an en-dash (–, U+2013) as specified.
        """
        parts: list[str] = []
        for p in self.pieces:
            if p.ok and p.text:
                parts.append(p.text)
            else:
                start_fmt = _fmt_hhmmss(p.start_ms / 1000.0)
                end_fmt = _fmt_hhmmss(p.end_ms / 1000.0)
                parts.append(f"[{start_fmt}–{end_fmt} 전사 실패 구간]")
        return " ".join(parts)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _fmt_hhmmss(sec: float) -> str:
    """Format seconds as ``hh:mm:ss`` (zero-padded, no sub-seconds)."""
    total = int(sec)
    hh = total // 3600
    mm = (total % 3600) // 60
    ss = total % 60
    return f"{hh:02d}:{mm:02d}:{ss:02d}"


def _error_type_for(exc: Exception) -> str:
    """Map a Groq exception to the taxonomy string used in PieceResult."""
    if isinstance(exc, GroqRateLimitError):
        return "rate_limit"
    if isinstance(exc, GroqServerError):
        return "server_error"
    if isinstance(exc, GroqClientError):
        return "client_error"
    if isinstance(exc, GroqApiKeyMissing):
        return "api_key_missing"
    if isinstance(exc, GroqNetworkError):
        return "network_failed_max_retries"
    return "unknown"


# ---------------------------------------------------------------------------
# Audio duration via ffmpeg -f null
# ---------------------------------------------------------------------------


def get_audio_duration_sec(path: Path) -> float:
    """Return audio duration in seconds by running ``ffmpeg -i src -f null -``.

    ffmpeg writes progress lines containing ``time=HH:MM:SS.cc`` to stderr.
    We parse the last such occurrence as the total duration.

    This approach requires no ffprobe and does a single fast pass
    (no PCM in memory).
    """
    import subprocess as _sp

    from app.audio_io import AudioIOError, _resolve_ffmpeg

    ffmpeg = _resolve_ffmpeg()
    result = _sp.run(
        [ffmpeg, "-nostdin", "-v", "error", "-stats", "-i", str(path), "-f", "null", "-"],
        capture_output=True,
        check=False,
        timeout=120,
    )
    # ffmpeg writes stats to stderr even with -v error
    combined = result.stderr.decode("utf-8", errors="replace")

    # Match last "time=HH:MM:SS.cc" occurrence
    matches = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", combined)
    if matches:
        hh, mm, ss_str = matches[-1]
        return int(hh) * 3600 + int(mm) * 60 + float(ss_str)

    # Fallback: if -f null produced no time output (very short or empty file),
    # attempt a full decode to count samples.
    try:
        ffmpeg2 = _resolve_ffmpeg()
        r2 = _sp.run(
            [
                ffmpeg2, "-nostdin", "-v", "error",
                "-i", str(path),
                "-ac", "1", "-ar", "16000", "-f", "f32le", "-acodec", "pcm_f32le", "-",
            ],
            capture_output=True,
            check=False,
            timeout=120,
        )
        samples = len(r2.stdout) // 4  # float32 = 4 bytes
        return samples / 16000.0
    except Exception as exc:
        raise AudioIOError(
            f"Could not determine audio duration for {path}: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Silence-boundary search
# ---------------------------------------------------------------------------


def _find_silence_boundary(
    audio_path: Path,
    target_sec: float,
    duration_sec: float,
) -> float:
    """Return the lowest-RMS frame center within ±BOUNDARY_SEARCH_WINDOW_SEC of *target_sec*.

    If every frame's RMS >= THRESHOLD_FLOOR (no real silence), returns *target_sec*
    unchanged (forced cut).  Memory cap: ~60 MB for a 60-second window at 16 kHz mono.

    Uses :func:`app.audio_io.load_pcm_16k_mono_window` and the VAD constants
    FRAME_LEN / HOP_LEN / THRESHOLD_FLOOR from :mod:`app.vad`.
    """
    window_start = max(0.0, target_sec - BOUNDARY_SEARCH_WINDOW_SEC)
    window_end = min(duration_sec, target_sec + BOUNDARY_SEARCH_WINDOW_SEC)
    window_dur = window_end - window_start

    if window_dur <= 0.0:
        return target_sec

    try:
        pcm = load_pcm_16k_mono_window(
            str(audio_path),
            start_sec=window_start,
            duration_sec=window_dur,
        )
    except AudioIOError:
        logger.warning("Windowed decode failed at %.1f s; using forced cut", target_sec)
        return target_sec

    if pcm.size < FRAME_LEN:
        return target_sec

    windows = sliding_window_view(pcm, window_shape=FRAME_LEN)[::HOP_LEN]
    rms = np.sqrt(np.mean(windows.astype(np.float32) ** 2, axis=1))

    # Check if any frame is below THRESHOLD_FLOOR (real silence exists)
    if float(rms.min()) >= THRESHOLD_FLOOR:
        return target_sec  # no silence — forced cut

    best_frame = int(np.argmin(rms))
    # Frame center in window-local time
    local_sec = (best_frame * HOP_LEN + FRAME_LEN / 2) / 16000.0
    return window_start + local_sec


# ---------------------------------------------------------------------------
# Public: plan_splits
# ---------------------------------------------------------------------------


def plan_splits(
    audio_path: Path,
    *,
    threshold_bytes: int = SPLIT_THRESHOLD_BYTES,
) -> list[SplitRange]:
    """Determine how to split *audio_path* into pieces for Groq upload.

    - Size ≤ threshold → single full-range piece (seq=0).
    - Size > threshold → n = ceil(size / threshold) pieces; each candidate cut
      is refined to the lowest-RMS frame within ±30 s.

    Returns an ordered list of :class:`SplitRange` objects.
    """
    size = audio_path.stat().st_size
    if size <= threshold_bytes:
        dur = get_audio_duration_sec(audio_path)
        return [SplitRange(seq=0, start_sec=0.0, end_sec=dur)]

    n = math.ceil(size / threshold_bytes)
    dur = get_audio_duration_sec(audio_path)

    boundaries: list[float] = [0.0]
    for i in range(1, n):
        target = i * (dur / n)
        refined = _find_silence_boundary(audio_path, target, dur)
        boundaries.append(refined)
    boundaries.append(dur)

    ranges: list[SplitRange] = []
    for seq, (start, end) in enumerate(zip(boundaries[:-1], boundaries[1:])):
        ranges.append(SplitRange(seq=seq, start_sec=start, end_sec=end))
    return ranges


# ---------------------------------------------------------------------------
# Public: cut_piece
# ---------------------------------------------------------------------------


def cut_piece(src: Path, rng: SplitRange, dest_dir: Path) -> Path:
    """Cut a time range from *src* and re-encode to the same codec probed earlier.

    Uses ``-ss {start} -to {end}`` with PCM-based re-encoding (never ``-c copy``)
    to avoid ogg page boundary errors.

    The output filename encodes the sequence number for uniqueness.
    """
    import subprocess as _sp

    from app.audio_io import (
        OPUS_COMPRESSION_LEVEL,
        REENCODE_TIMEOUT_SEC,
        AudioIOError,
        _resolve_ffmpeg,
        probe_audio_encoders,
    )

    ffmpeg = _resolve_ffmpeg()
    encoders = probe_audio_encoders()

    if "libopus" in encoders:
        suffix = ".ogg"
        codec_args = [
            "-c:a", "libopus",
            "-b:a", "24k",
            "-vbr", "on",
            "-compression_level", OPUS_COMPRESSION_LEVEL,
            "-f", "ogg",
        ]
    elif "flac" in encoders:
        suffix = ".flac"
        codec_args = ["-c:a", "flac", "-f", "flac"]
    else:
        raise AudioIOError(
            "Neither libopus nor flac available; cannot cut piece."
        )

    dest = dest_dir / f"{src.stem}_piece{rng.seq:03d}{suffix}"
    cmd = [
        ffmpeg,
        "-nostdin",
        "-v", "error",
        "-ss", str(rng.start_sec),
        "-to", str(rng.end_sec),
        "-i", str(src),
        "-ar", "16000",
        "-ac", "1",
        "-map", "0:a",
        *codec_args,
        "-y",
        str(dest),
    ]
    result = _sp.run(
        cmd,
        capture_output=True,
        check=False,
        # A single piece can hold up to ~2h of audio (20MB at 24kbps) — the
        # same hour-scale encode budget as reencode_for_upload applies.
        timeout=REENCODE_TIMEOUT_SEC,
    )
    if result.returncode != 0:
        stderr_text = result.stderr.decode("utf-8", errors="replace")[-512:]
        raise AudioIOError(
            f"ffmpeg cut_piece failed (code {result.returncode}): {stderr_text}"
        )
    return dest


# ---------------------------------------------------------------------------
# Public: transcribe_pieces
# ---------------------------------------------------------------------------


def transcribe_pieces(
    piece_paths_with_ranges: list[tuple[Path, SplitRange]],
    *,
    prompt: str | None,
    language: str | None = None,
) -> BatchResult:
    """Transcribe each piece sequentially; retry only GroqNetworkError.

    Retry policy per piece:
      - GroqNetworkError: up to PIECE_MAX_RETRIES total attempts,
        sleeping PIECE_RETRY_SLEEP_SEC between each.
      - All other Groq errors (RateLimit/Server/Client/ApiKeyMissing):
        fail the piece immediately.

    Segments are offset-corrected so timestamps are relative to the start of
    the original audio:  ``seg['start'] += piece.start_sec``.

    Returns a :class:`BatchResult` with per-piece outcomes and failed_ranges.
    """
    pieces: list[PieceResult] = []
    failed_ranges: list[dict] = []

    for piece_path, rng in piece_paths_with_ranges:
        start_ms = round(rng.start_sec * 1000)
        end_ms = round(rng.end_sec * 1000)
        result: TranscribeResult | None = None
        error_type: str | None = None

        for attempt in range(1, PIECE_MAX_RETRIES + 1):
            try:
                result = transcribe_audio(
                    piece_path,
                    prompt=prompt,
                    language=language,
                )
                error_type = None
                break  # success
            except GroqNetworkError as exc:
                error_type = "network_failed_max_retries"
                if attempt < PIECE_MAX_RETRIES:
                    logger.warning(
                        "piece seq=%d network error (attempt %d/%d): %s; retrying in %.0fs",
                        rng.seq, attempt, PIECE_MAX_RETRIES, exc, PIECE_RETRY_SLEEP_SEC,
                    )
                    time.sleep(PIECE_RETRY_SLEEP_SEC)
                else:
                    logger.error(
                        "piece seq=%d exhausted %d retries: %s",
                        rng.seq, PIECE_MAX_RETRIES, exc,
                    )
            except (
                GroqRateLimitError,
                GroqServerError,
                GroqClientError,
                GroqApiKeyMissing,
            ) as exc:
                error_type = _error_type_for(exc)
                logger.error(
                    "piece seq=%d failed immediately (%s): %s",
                    rng.seq, error_type, exc,
                )
                break  # do not retry non-network errors

        if result is not None:
            # Offset-correct segments and convert to ms sidecar format.
            # raw_start/raw_end and rng.start_sec are all SECONDS — sum first,
            # then multiply by 1000 exactly once for the ms payload.
            segments: list[dict] = []
            for seg in result.get("segments", []):
                raw_start = seg.get("start", 0.0)
                raw_end = seg.get("end", 0.0)
                segments.append(
                    {
                        "start_ms": round((raw_start + rng.start_sec) * 1000),
                        "end_ms": round((raw_end + rng.start_sec) * 1000),
                        "text": seg.get("text", ""),
                    }
                )
            pieces.append(
                PieceResult(
                    seq=rng.seq,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    ok=True,
                    text=result.get("text"),
                    segments=segments,
                    error_type=None,
                )
            )
        else:
            pieces.append(
                PieceResult(
                    seq=rng.seq,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    ok=False,
                    text=None,
                    segments=[],
                    error_type=error_type,
                )
            )
            failed_ranges.append({"start_ms": start_ms, "end_ms": end_ms})

    return BatchResult(pieces=pieces, failed_ranges=failed_ranges)


# ---------------------------------------------------------------------------
# Public: run_batch_transcription (single entry point for Phase 2)
# ---------------------------------------------------------------------------


def run_batch_transcription(
    webm_path: Path,
    *,
    workdir: Path,
    prompt: str | None,
    language: str | None = None,
    threshold_bytes: int = SPLIT_THRESHOLD_BYTES,
) -> BatchResult:
    """Full pipeline: re-encode → plan splits → cut pieces → transcribe → cleanup.

    1. Re-encode *webm_path* to ogg/flac in *workdir*.
    2. Determine splits via :func:`plan_splits`.
    3. Cut each piece if there are multiple splits.
    4. Call :func:`transcribe_pieces` sequentially.
    5. Clean up temp files in a ``finally`` block (re-encoded file + piece files).

    This is the single entry point Phase 2 (server.py finalize producer) will call
    via ``asyncio.to_thread``.
    """
    reencoded: Path | None = None
    piece_files: list[Path] = []

    try:
        # 1. Re-encode
        reencoded = reencode_for_upload(webm_path, workdir)
        logger.info("reencoded %s → %s (%d bytes)", webm_path.name, reencoded.name, reencoded.stat().st_size)

        # 2. Plan splits
        ranges = plan_splits(reencoded, threshold_bytes=threshold_bytes)
        logger.info("plan_splits: %d piece(s)", len(ranges))

        # 3. Cut pieces (only if more than one range)
        if len(ranges) == 1:
            pieces_with_ranges: list[tuple[Path, SplitRange]] = [(reencoded, ranges[0])]
        else:
            for rng in ranges:
                piece_path = cut_piece(reencoded, rng, workdir)
                piece_files.append(piece_path)
                logger.info(
                    "cut piece seq=%d [%.1f–%.1f s] → %s",
                    rng.seq, rng.start_sec, rng.end_sec, piece_path.name,
                )
            pieces_with_ranges = list(zip(piece_files, ranges))

        # 4. Transcribe
        return transcribe_pieces(pieces_with_ranges, prompt=prompt, language=language)

    finally:
        # 5. Cleanup temp files (never touch webm_path — the original is preserved)
        for pf in piece_files:
            try:
                pf.unlink(missing_ok=True)
            except OSError:
                pass
        if reencoded is not None and reencoded.exists():
            try:
                reencoded.unlink(missing_ok=True)
            except OSError:
                pass
