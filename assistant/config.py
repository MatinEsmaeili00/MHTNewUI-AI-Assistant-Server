"""
All settings come from environment variables and are read once, when the server starts.

Other modules read them as `config.settings.<name>` at call time, so a test can change one
with `monkeypatch.setattr(config.settings, "api_key", "secret")`.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent


def _text(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _number(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return float(default)


def _flag(name: str, default: bool) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value in ("1", "true", "yes", "on")


class Settings:
    def __init__(self) -> None:
        # Security. An empty key means no authentication.
        self.api_key = _text("ASSISTANT_API_KEY", "")

        # Language model (Ollama).
        self.ollama_url = _text("OLLAMA_URL", "http://localhost:11434").rstrip("/")
        self.llm_model = _text("LLM_MODEL", "gemma4:e4b")
        # The client waits 30 s for the whole answer, so one LLM call must stay below that.
        self.llm_timeout_s = _number("LLM_TIMEOUT_S", 25.0)
        self.llm_keep_alive = _text("LLM_KEEP_ALIVE", "1h")
        self.llm_warmup = _flag("LLM_WARMUP", True)
        self.fake_llm = _flag("FAKE_LLM", False)

        # Speech to text (faster-whisper).
        self.stt_model = _text("STT_MODEL", "base.en")
        self.stt_device = _text("STT_DEVICE", "cpu")
        self.stt_compute = _text("STT_COMPUTE", "int8")
        # Empty = the Hugging Face cache in the home folder (faster-whisper's default).
        self.stt_model_dir = _text("STT_MODEL_DIR", "")
        # A few words to bias Whisper towards this app's vocabulary. Empty = no prompt.
        self.stt_prompt = _text("STT_PROMPT", "")
        self.save_last_audio = _flag("SAVE_LAST_AUDIO", True)
        self.last_audio_path = REPO_DIR / "last_voice.wav"

        # Catalog cache on disk (one small JSON file per catalog_id).
        self.catalog_cache_dir = Path(_text("CATALOG_CACHE_DIR", str(REPO_DIR / ".cache" / "catalogs")))

        # Limits.
        self.max_audio_bytes = 20 * 1024 * 1024
        self.max_json_bytes = 2 * 1024 * 1024
        self.max_text_chars = 2000
        self.max_catalog_parts = 2000
        self.max_actions = 8
        self.session_turns = int(_number("SESSION_TURNS", 6))
        self.max_sessions = 200


settings = Settings()
