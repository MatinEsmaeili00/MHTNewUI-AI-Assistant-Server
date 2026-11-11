"""
Speech to text with faster-whisper.

faster-whisper is imported only when it is first needed, so the server starts, and all text
features and tests work, on a machine that does not have it. In that case `installed()` is False,
`/health` says `stt_ready: false` and `/v1/voice` answers 503.
"""

from __future__ import annotations

import importlib.util
import io
import threading

from . import config, log

_model = None
_lock = threading.Lock()
_load_error = ""


class STTUnavailable(Exception):
    """faster-whisper is missing or the model could not be loaded."""


def installed() -> bool:
    try:
        return importlib.util.find_spec("faster_whisper") is not None
    except (ImportError, ValueError):
        return False


def ready() -> bool:
    """What /health reports: installed, and the last attempt to load the model did not fail."""
    return installed() and not _load_error


def load() -> None:
    """Loads the Whisper model once (downloads it the first time). Raises STTUnavailable."""
    global _model, _load_error
    if _model is not None:
        return
    if not installed():
        raise STTUnavailable("faster-whisper is not installed on this server.")
    with _lock:
        if _model is not None:
            return
        settings = config.settings
        try:
            from faster_whisper import WhisperModel

            log.info("Loading Whisper %s (%s, %s) ...", settings.stt_model, settings.stt_device, settings.stt_compute)
            _model = WhisperModel(
                settings.stt_model,
                device=settings.stt_device,
                compute_type=settings.stt_compute,
                download_root=settings.stt_model_dir or None,
            )
            _load_error = ""
            log.info("Whisper %s ready.", settings.stt_model)
        except Exception as error:
            _load_error = repr(error)
            log.warning("could not load Whisper %s: %r", settings.stt_model, error)
            raise STTUnavailable(f"The speech model {settings.stt_model} could not be loaded.") from error


def warm_up() -> None:
    """Called in a background thread at startup so the first voice command is not slow."""
    try:
        load()
    except STTUnavailable:
        pass


def transcribe(audio_bytes: bytes) -> str:
    """WAV (or anything PyAV can decode) -> text. Decoded from memory, no temporary file."""
    load()
    with _lock:                       # one transcription at a time
        segments, _info = _model.transcribe(
            io.BytesIO(audio_bytes),
            language="en",
            beam_size=5,
            vad_filter=True,
            initial_prompt=config.settings.stt_prompt or None,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()
