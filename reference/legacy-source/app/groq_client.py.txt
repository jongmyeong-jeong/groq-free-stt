"""groq SDK wrapper for audio transcription.

Single public function: transcribe_audio().

Reads GROQ_API_KEY and TRANSCRIPTION_LANG from env at call time (not import time).
Loads Whisper prompt from ~/.lonta/data/prompt.json if present.

Schema of prompt.json (single string):
    {"prompt": "..."}

Exceptions:
    GroqTranscriptionError  — base class for all errors raised here
    GroqApiKeyMissing       — GROQ_API_KEY not set
    GroqRateLimitError      — HTTP 429 from groq
    GroqServerError         — HTTP 5xx from groq
    GroqNetworkError        — connection / timeout failures
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import TypedDict

import httpx

import groq as _groq_module

from app.paths import prompt_path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class GroqTranscriptionError(Exception):
    """Base class for all groq_client errors."""


class GroqApiKeyMissing(GroqTranscriptionError):
    """GROQ_API_KEY environment variable is not set."""


class GroqRateLimitError(GroqTranscriptionError):
    """groq API returned HTTP 429 (rate limit exceeded)."""


class GroqServerError(GroqTranscriptionError):
    """groq API returned an HTTP 5xx error."""


class GroqNetworkError(GroqTranscriptionError):
    """Network-level failure (connection error, timeout, etc.)."""


class GroqClientError(GroqTranscriptionError):
    """groq API returned an unexpected 4xx error (not 401 or 429)."""


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class TranscribeSegment(TypedDict):
    start: float
    end: float
    text: str


class TranscribeResult(TypedDict):
    text: str
    segments: list[TranscribeSegment]


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------


def _load_prompt() -> str | None:
    """Load whisper hint from prompt.json; return None if absent or empty."""
    path = prompt_path()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    text = raw.get("prompt", "")
    if not isinstance(text, str) or not text.strip():
        return None
    return text.strip()


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------


def transcribe_audio(
    audio_path: Path | str,
    *,
    language: str | None = None,
    prompt: str | None = None,
) -> TranscribeResult:
    """Call groq Whisper API for a single audio file.

    Parameters
    ----------
    audio_path:
        Path to the WAV (or other audio) file to transcribe.
    language:
        BCP-47 language code. If None, falls back to TRANSCRIPTION_LANG env var
        (default: "ko").
    prompt:
        Optional Whisper conditioning prompt. If None, the module loads
        ~/.lonta/data/prompt.json automatically.

    Returns
    -------
    TranscribeResult with ``text`` (full transcript) and ``segments``
    (list of {start, end, text} dicts).

    Raises
    ------
    GroqApiKeyMissing       — GROQ_API_KEY is not set.
    GroqRateLimitError      — API returned 429.
    GroqServerError         — API returned 5xx.
    GroqNetworkError        — Connection or timeout failure.
    """
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise GroqApiKeyMissing(
            "GROQ_API_KEY environment variable is not set. "
            "Set it before starting the server."
        )

    lang = language or os.environ.get("TRANSCRIPTION_LANG", "ko")

    # Resolve prompt: explicit argument takes precedence over file.
    if prompt is None:
        prompt = _load_prompt()
    has_prompt = bool(prompt)

    audio_path = Path(audio_path)

    t_start = time.monotonic()
    try:
        client = _groq_module.Groq(api_key=api_key)

        call_kwargs: dict = {
            "model": "whisper-large-v3-turbo",
            "language": lang,
            "response_format": "verbose_json",
            "timestamp_granularities": ["segment"],
            "temperature": 0.0,
        }
        if has_prompt:
            call_kwargs["prompt"] = prompt

        with audio_path.open("rb") as f:
            call_kwargs["file"] = f
            resp = client.audio.transcriptions.create(**call_kwargs)

    except _groq_module.RateLimitError as exc:
        raise GroqRateLimitError(f"groq rate limit exceeded: {exc}") from exc
    except _groq_module.APIStatusError as exc:
        if exc.status_code >= 500:
            raise GroqServerError(
                f"groq server error {exc.status_code}: {exc}"
            ) from exc
        if exc.status_code == 401:
            raise GroqApiKeyMissing(
                f"groq API key is invalid or missing (HTTP 401): {exc}"
            ) from exc
        raise GroqClientError(
            f"groq client error {exc.status_code}: {exc}"
        ) from exc
    except (
        httpx.ConnectError,
        httpx.TimeoutException,
        _groq_module.APIConnectionError,
    ) as exc:
        raise GroqNetworkError(f"groq network error: {exc}") from exc

    duration = time.monotonic() - t_start

    # Compute audio duration for WAV files (skip silently for other formats).
    audio_sec: float | None = None
    try:
        import wave  # stdlib

        with wave.open(str(audio_path), "rb") as wf:
            audio_sec = wf.getnframes() / wf.getframerate()
    except Exception:  # noqa: BLE001 — non-WAV or unreadable; skip field
        pass

    log_extra: dict = {
        "audio_path": str(audio_path),
        "lang": lang,
        "has_prompt": has_prompt,
        "duration_ms": round(duration * 1000),
    }
    if audio_sec is not None:
        log_extra["audio_sec"] = round(audio_sec, 3)

    logger.info(
        "groq_transcribe",
        extra=log_extra,
    )

    segments: list[TranscribeSegment] = []
    raw_segments = getattr(resp, "segments", None)
    if raw_segments:
        for seg in raw_segments:
            if isinstance(seg, dict):
                start = seg.get("start")
                end = seg.get("end")
                text = seg.get("text", "")
            else:
                start = getattr(seg, "start", None)
                end = getattr(seg, "end", None)
                text = getattr(seg, "text", "")
            if start is None or end is None:
                continue
            segments.append(
                TranscribeSegment(
                    start=float(start),
                    end=float(end),
                    text=str(text),
                )
            )

    return TranscribeResult(
        text=resp.text,
        segments=segments,
    )
