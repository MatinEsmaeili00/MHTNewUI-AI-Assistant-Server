"""Shared fixtures. Nothing here needs Ollama or faster-whisper."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR))

from assistant import config, llm  # noqa: E402
from assistant.catalog import Catalog, store  # noqa: E402
from assistant.sessions import sessions  # noqa: E402

CATALOG_RAW = json.loads((REPO_DIR / "tests" / "fixtures" / "catalog.json").read_text(encoding="utf-8"))

# The example context of docs/PROTOCOL.md (pose estimation running, three angles visible).
SAMPLE_CONTEXT = {
    "avatar": {"name": "BP_Abby8", "index": 0, "count": 3},
    "layer": "body",
    "timeline": {
        "playing": True, "speed": 1.0, "speed_steps": [0.25, 0.5, 1, 1.5, 2, 3],
        "time": 0.42, "cycle_seconds": 1.1, "recording": False,
    },
    "action": {"playing": "", "available": ["Idle", "Running", "Jogging", "TennisForehand"]},
    "panel": "none",
    "camera": {"distance": 300.0, "min": 50.0, "max": 1200.0, "moved": False},
    "pose": {
        "status": "running", "overlay": True, "panel_open": True, "recording": False, "fps": 11.8,
        "person": True, "visible_side": "right",
        "angles": {"right knee": 142.3, "left knee": 95.0, "trunk": 84.1},
    },
    "selected_part": {"kind": "muscle", "name": "Biceps brachii", "side": "left"},
    "chat_open": True,
    "last_results": [{"type": "set_layer", "ok": True, "message": ""}],
}


@pytest.fixture(scope="session")
def catalog_raw() -> dict:
    return CATALOG_RAW


@pytest.fixture(scope="session")
def catalog() -> Catalog:
    built = Catalog.from_raw(CATALOG_RAW)
    assert built is not None
    return built


@pytest.fixture
def context() -> dict:
    return copy.deepcopy(SAMPLE_CONTEXT)


@pytest.fixture
def context_pose_off(context) -> dict:
    context["pose"] = {"status": "off", "overlay": False, "panel_open": False, "recording": False}
    return context


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """Every test starts with an empty catalog store and session store, no API key and no real LLM."""
    settings = config.settings
    monkeypatch.setattr(settings, "catalog_cache_dir", tmp_path / "catalogs")
    monkeypatch.setattr(settings, "api_key", "")
    monkeypatch.setattr(settings, "fake_llm", False)
    monkeypatch.setattr(settings, "save_last_audio", False)
    monkeypatch.setattr(settings, "last_audio_path", tmp_path / "last_voice.wav")
    monkeypatch.setattr(llm, "_ready", False)
    store._catalogs.clear()
    store._asked.clear()
    sessions.clear()

    unexpected_calls = []

    def no_network(*args, **kwargs):
        unexpected_calls.append(args)
        raise AssertionError("a test tried to reach Ollama; monkeypatch llm.requests.post in that test")

    monkeypatch.setattr(llm.requests, "post", no_network)
    yield
    assert not unexpected_calls, "this test reached for Ollama although it should be answered by the rules"
