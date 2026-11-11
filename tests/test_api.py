"""The HTTP API with FastAPI's TestClient: /health, the key, /v1/chat, the need_catalog flow, /v1/voice."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import server
from assistant import config, llm, stt
from assistant.catalog import store

BICEPS = {"type": "show_part", "kind": "muscle", "name": "Biceps brachii", "side": "left"}
ANSWER_FIELDS = {"transcript", "reply", "actions", "source", "latency_ms", "need_catalog"}


@pytest.fixture
def client():
    return TestClient(server.app)


def chat(client, text, **more):
    body = {"protocol": 1, "session_id": "test-session", "text": text, **more}
    return client.post("/v1/chat", json=body)


def voice(client, request=None, audio=b"RIFF....WAVEfmt fake audio", **more):
    data = {} if request is None else {"request": request if isinstance(request, str) else json.dumps(request)}
    return client.post("/v1/voice", files={"audio": ("speech.wav", audio, "audio/wav")}, data=data, **more)


@pytest.fixture
def heard(monkeypatch):
    """Pretends faster-whisper is installed and always hears what the test sets in heard['text']."""
    state = {"text": "show me the left biceps", "calls": 0, "bytes": b""}

    def fake_transcribe(audio_bytes):
        state["calls"] += 1
        state["bytes"] = audio_bytes
        if isinstance(state["text"], Exception):
            raise state["text"]
        return state["text"]

    monkeypatch.setattr(stt, "installed", lambda: True)
    monkeypatch.setattr(stt, "transcribe", fake_transcribe)
    return state


# ---- /health ------------------------------------------------------------------------------------------

def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "name": "mht-assistant",
        "version": "0.1.0",
        "protocol": 1,
        "auth_required": False,
        "llm_model": "gemma4:e4b",
        "llm_ready": False,
        "stt_model": "base.en",
        "stt_ready": stt.ready(),
    }


def test_health_says_when_a_key_is_needed_and_needs_none_itself(client, monkeypatch):
    monkeypatch.setattr(config.settings, "api_key", "s3cret")
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["auth_required"] is True
    assert "s3cret" not in response.text


def test_health_stt_ready_is_false_without_faster_whisper(client, monkeypatch):
    monkeypatch.setattr(stt, "installed", lambda: False)
    assert client.get("/health").json()["stt_ready"] is False
    monkeypatch.setattr(stt, "installed", lambda: True)
    monkeypatch.setattr(stt, "_load_error", "")
    assert client.get("/health").json()["stt_ready"] is True


def test_health_with_fake_llm(client, monkeypatch):
    monkeypatch.setattr(config.settings, "fake_llm", True)
    body = client.get("/health").json()
    assert body["llm_ready"] is True and body["llm_model"] == "fake"


# ---- the key --------------------------------------------------------------------------------------------

def test_no_key_configured_means_no_authentication(client):
    assert chat(client, "pause").status_code == 200
    assert chat(client, "pause", ).json()["actions"] == [{"type": "timeline", "op": "pause"}]


@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Bearer wrong"},
    {"Authorization": "Bearer s3cre"},
    {"Authorization": "Bearer s3cret2"},
    {"Authorization": "s3cret"},
    {"Authorization": "Basic s3cret"},
    {"Authorization": "Bearer"},
    {"Authorization": ""},
    {"X-Api-Key": "s3cret"},
])
def test_wrong_or_missing_key_is_401(client, monkeypatch, heard, headers):
    monkeypatch.setattr(config.settings, "api_key", "s3cret")
    response = client.post("/v1/chat", json={"text": "pause"}, headers=headers)
    assert response.status_code == 401
    assert response.json() == {"detail": "Missing or wrong API key."}
    response = voice(client, {}, headers=headers)
    assert response.status_code == 401
    assert heard["calls"] == 0                       # nothing was transcribed for a stranger


def test_right_key_is_accepted(client, monkeypatch, heard):
    monkeypatch.setattr(config.settings, "api_key", "s3cret")
    headers = {"Authorization": "Bearer s3cret"}
    response = client.post("/v1/chat", json={"text": "pause"}, headers=headers)
    assert response.status_code == 200 and response.json()["actions"] == [{"type": "timeline", "op": "pause"}]
    assert client.post("/v1/chat", json={"text": "pause"}, headers={"authorization": "bearer s3cret"}).status_code == 200
    heard["text"] = "pause"
    assert voice(client, {}, headers=headers).status_code == 200


def test_unknown_paths_also_need_the_key(client, monkeypatch):
    monkeypatch.setattr(config.settings, "api_key", "s3cret")
    assert client.get("/docs").status_code == 401
    assert client.post("/health").status_code == 401            # only GET /health is open
    assert client.get("/docs", headers={"Authorization": "Bearer s3cret"}).status_code == 404


# ---- /v1/chat -----------------------------------------------------------------------------------------------

def test_chat_answer_has_the_protocol_shape(client, context, catalog_raw):
    response = chat(client, "show me the left biceps", context=context, catalog_id="9b1d5c0e", catalog=catalog_raw)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == ANSWER_FIELDS
    assert body["transcript"] == "show me the left biceps"
    assert body["reply"] == "Here is the left Biceps brachii."
    assert body["actions"] == [BICEPS]
    assert body["source"] == "rules"
    assert body["need_catalog"] is False
    assert isinstance(body["latency_ms"], int) and 0 <= body["latency_ms"] < 1000


@pytest.mark.parametrize("text, actions", [
    ("switch to the muscle layer", [{"type": "set_layer", "layer": "muscles"}]),
    ("play it faster", [{"type": "timeline", "op": "faster"}, {"type": "timeline", "op": "play"}]),
    ("zoom in a lot", [{"type": "camera", "op": "zoom_in", "value": 3}]),
    ("start the pose estimation and read the data",
     [{"type": "pose_estimation", "enabled": True}, {"type": "read_pose_data"}]),
    ("make her run", [{"type": "play_action", "name": "Running"}]),
    ("what is the right knee angle?", []),
])
def test_chat_examples_work_with_ollama_down(client, context, text, actions):
    """The autouse fixture makes any call to Ollama fail the test, so this proves the rules answered."""
    body = chat(client, text, context=context).json()
    assert body["actions"] == actions and body["source"] == "rules" and body["reply"]


def test_chat_with_the_bare_minimum(client):
    body = client.post("/v1/chat", json={"text": "pause"}).json()
    assert body["actions"] == [{"type": "timeline", "op": "pause"}]
    assert body["need_catalog"] is False


@pytest.mark.parametrize("body", [
    {},
    {"text": None},
    {"text": ""},
    {"text": 42},
    {"text": ["pause"]},
    {"context": "nonsense", "catalog": "nonsense", "catalog_id": 12, "session_id": {"a": 1}},
])
def test_chat_without_usable_text_still_answers(client, body):
    response = client.post("/v1/chat", json=body)
    assert response.status_code == 200
    answer = response.json()
    assert set(answer) == ANSWER_FIELDS
    assert answer["actions"] == [] and answer["transcript"] == "" and answer["reply"]


@pytest.mark.parametrize("context", [
    None, {}, [], "x", {"timeline": None, "pose": 3, "action": "run", "avatar": [], "layer": {}},
])
def test_chat_never_fails_on_a_missing_or_odd_context(client, context, catalog_raw):
    for text in ("show me the left biceps", "what is the knee angle", "make her run", "what layer am i on",
                 "twice as fast", "next avatar", "hide the overlay", "the other side"):
        response = chat(client, text, context=context, catalog_id="c1", catalog=catalog_raw)
        assert response.status_code == 200, text
        assert set(response.json()) == ANSWER_FIELDS


def test_chat_text_is_capped(client, monkeypatch):
    monkeypatch.setattr(config.settings, "fake_llm", True)
    body = chat(client, "pause " * 5000).json()               # 30 000 characters
    assert len(body["transcript"]) <= 2000


def test_chat_body_that_is_not_an_object(client):
    response = client.post("/v1/chat", content=b"[1, 2]", headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)
    response = client.post("/v1/chat", content=b"{not json", headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)


def test_chat_too_large(client):
    response = client.post("/v1/chat", content=b" " * (3 * 1024 * 1024), headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    assert "too large" in response.json()["detail"]


def test_chat_too_large_without_a_content_length(client):
    """A chunked upload has no Content-Length header: the bytes are counted while they arrive."""
    def chunks():
        for _ in range(3):
            yield b" " * (1024 * 1024)

    response = client.post("/v1/chat", content=chunks(), headers={"Content-Type": "application/json"})
    assert "content-length" not in response.request.headers
    assert response.status_code == 413
    assert "too large" in response.json()["detail"]


def test_chat_other_protocol_version(client):
    response = chat(client, "pause", protocol=2)
    assert response.status_code == 400
    assert "protocol 1" in response.json()["detail"]
    assert client.post("/v1/chat", json={"text": "pause"}).status_code == 200      # no version given: accepted


def test_errors_always_have_a_detail_sentence(client):
    for response in (client.get("/nope"), client.get("/v1/chat"), client.post("/v1/voice")):
        assert response.status_code in (404, 405, 422)
        assert isinstance(response.json()["detail"], str) and response.json()["detail"]


# ---- need_catalog --------------------------------------------------------------------------------------------

def test_need_catalog_flow(client, context, catalog_raw):
    # 1. unknown catalog_id, no catalog, and the sentence is about a part -> need_catalog
    first = chat(client, "show me the left biceps", context=context, catalog_id="avatar-1").json()
    assert first == {
        "transcript": "show me the left biceps", "reply": "", "actions": [], "source": "rules",
        "need_catalog": True, "latency_ms": first["latency_ms"],
    }
    # 2. the client repeats the request with the catalog -> the action
    second = chat(client, first["transcript"], context=context, catalog_id="avatar-1", catalog=catalog_raw).json()
    assert second["need_catalog"] is False and second["actions"] == [BICEPS]
    # 3. later requests carry only the id
    third = chat(client, "highlight the femur", context=context, catalog_id="avatar-1").json()
    assert third["need_catalog"] is False
    assert third["actions"] == [{"type": "show_part", "kind": "bone", "name": "Femur", "side": ""}]


def test_no_need_catalog_for_a_request_that_is_not_about_a_part(client, context):
    for text in ("pause", "switch to the muscle layer", "zoom in a lot", "make her run", "what layer am i on",
                 "start the pose estimation and read the data"):
        body = chat(client, text, context=context, catalog_id="never-seen").json()
        assert body["need_catalog"] is False, text
        assert body["source"] == "rules" and body["reply"]


def test_need_catalog_is_not_asked_twice_in_a_row(client, context):
    first = chat(client, "show me the left biceps", context=context, catalog_id="avatar-2").json()
    assert first["need_catalog"] is True
    second = chat(client, "show me the left biceps", context=context, catalog_id="avatar-2").json()
    assert second["need_catalog"] is False
    assert second["actions"] == [] and "list of muscles and bones" in second["reply"]


def test_need_catalog_for_a_question_about_a_part(client, context, catalog_raw, monkeypatch):
    """A sentence for the LLM that mentions a part also needs the names (for the candidate list)."""
    monkeypatch.setattr(config.settings, "fake_llm", True)
    first = chat(client, "what does the deltoid do?", context=context, catalog_id="avatar-3").json()
    assert first["need_catalog"] is True
    second = chat(client, "what does the deltoid do?", context=context, catalog_id="avatar-3", catalog=catalog_raw).json()
    assert second["need_catalog"] is False and second["source"] == "llm"
    assert chat(client, "tell me a joke", context=context, catalog_id="avatar-4").json()["need_catalog"] is False


def test_catalog_survives_a_restart_through_the_disk_cache(client, context, catalog_raw):
    chat(client, "pause", context=context, catalog_id="avatar-5", catalog=catalog_raw)
    store._catalogs.clear()                                   # what a restart does to the memory
    body = chat(client, "show me the left biceps", context=context, catalog_id="avatar-5").json()
    assert body["need_catalog"] is False and body["actions"] == [BICEPS]


def test_a_new_catalog_under_the_same_id_replaces_the_old_one(client, context, catalog_raw):
    small = {"parts": [{"kind": "bone", "name": "Femur", "sides": ["left", "right"], "region": "Thigh"}]}
    chat(client, "pause", context=context, catalog_id="avatar-6", catalog=small)
    assert chat(client, "show me the left biceps", context=context, catalog_id="avatar-6").json()["actions"] == []
    chat(client, "pause", context=context, catalog_id="avatar-6", catalog=catalog_raw)
    assert chat(client, "show me the left biceps", context=context, catalog_id="avatar-6").json()["actions"] == [BICEPS]


def test_catalog_id_cannot_write_outside_the_cache_folder(client, context, catalog_raw, tmp_path):
    chat(client, "pause", context=context, catalog_id="../../../evil", catalog=catalog_raw)
    files = [path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file()]
    assert files == ["catalogs/evil.json"]


def test_catalog_is_capped(client, context, monkeypatch):
    monkeypatch.setattr(config.settings, "max_catalog_parts", 3)
    big = {"parts": [{"kind": "bone", "name": f"Bone {n}", "sides": ["center"]} for n in range(100)]}
    chat(client, "pause", context=context, catalog_id="big", catalog=big)
    assert len(store.get("big").parts) == 3


# ---- /v1/voice ---------------------------------------------------------------------------------------------------

def test_voice(client, heard, context, catalog_raw):
    request = {"protocol": 1, "session_id": "s", "context": context, "catalog_id": "v1", "catalog": catalog_raw}
    response = voice(client, request, audio=b"the wav bytes")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == ANSWER_FIELDS
    assert body["transcript"] == "show me the left biceps"
    assert body["actions"] == [BICEPS]
    assert body["source"] == "rules"
    assert heard["bytes"] == b"the wav bytes"


def test_voice_ignores_text_in_the_request_part(client, heard, context):
    heard["text"] = "Pause."
    body = voice(client, {"text": "zoom in", "context": context}).json()
    assert body["transcript"] == "Pause." and body["actions"] == [{"type": "timeline", "op": "pause"}]


def test_voice_need_catalog_then_chat(client, heard, context, catalog_raw):
    """The clip is uploaded once; the repeat is a /v1/chat with the transcript and the catalog."""
    first = voice(client, {"context": context, "catalog_id": "v2"}).json()
    assert first["need_catalog"] is True and first["transcript"] == "show me the left biceps"
    second = chat(client, first["transcript"], context=context, catalog_id="v2", catalog=catalog_raw).json()
    assert second["actions"] == [BICEPS]
    assert heard["calls"] == 1


@pytest.mark.parametrize("transcript", ["", "   ", RuntimeError("could not decode")])
def test_voice_nothing_heard(client, heard, context, transcript):
    heard["text"] = transcript
    response = voice(client, {"context": context})
    assert response.status_code == 200
    body = response.json()
    assert body["transcript"] == ""
    assert body["reply"] == "I did not catch that. Hold the logo while you speak."
    assert body["actions"] == [] and body["need_catalog"] is False


def test_voice_empty_audio(client, heard):
    body = voice(client, {}, audio=b"").json()
    assert body["reply"] == "I did not catch that. Hold the logo while you speak."
    assert body["transcript"] == "" and heard["calls"] == 0


def test_voice_without_faster_whisper_is_503(client, monkeypatch, context):
    monkeypatch.setattr(stt, "installed", lambda: False)
    response = voice(client, {"context": context})
    assert response.status_code == 503
    assert "faster-whisper" in response.json()["detail"]
    assert client.get("/health").json()["stt_ready"] is False


def test_voice_when_the_model_cannot_be_loaded_is_503(client, heard):
    heard["text"] = stt.STTUnavailable("The speech model base.en could not be loaded.")
    response = voice(client, {})
    assert response.status_code == 503
    assert response.json()["detail"] == "The speech model base.en could not be loaded."


def test_voice_without_the_request_part(client, heard):
    heard["text"] = "pause"
    body = voice(client, None).json()
    assert body["actions"] == [{"type": "timeline", "op": "pause"}]


def test_voice_with_a_bad_request_part(client, heard):
    response = voice(client, "{not json")
    assert response.status_code == 400 and "JSON" in response.json()["detail"]
    response = voice(client, "[1, 2]")
    assert response.status_code == 400
    assert heard["calls"] == 0


def test_voice_without_the_audio_part(client, heard):
    response = client.post("/v1/voice", data={"request": "{}"})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)


def test_voice_audio_over_20_mb_is_413(client, heard):
    response = voice(client, {}, audio=b"\0" * (20 * 1024 * 1024 + 1))
    assert response.status_code == 413
    assert "20 MB" in response.json()["detail"]
    assert heard["calls"] == 0
    assert voice(client, {}, audio=b"\0" * (2 * 1024 * 1024)).status_code == 200      # 2 MB is fine


def test_voice_upload_far_over_the_limit_is_refused_before_it_is_read(client, heard):
    response = voice(client, {}, audio=b"\0" * (23 * 1024 * 1024))
    assert response.status_code == 413
    assert heard["calls"] == 0


def test_voice_saves_the_last_clip_only_when_asked(client, heard, monkeypatch):
    path = config.settings.last_audio_path
    voice(client, {}, audio=b"clip one")
    assert not path.exists()
    monkeypatch.setattr(config.settings, "save_last_audio", True)
    voice(client, {}, audio=b"clip two")
    assert path.read_bytes() == b"clip two"


# ---- sessions --------------------------------------------------------------------------------------------------------

def test_the_llm_sees_the_earlier_turns_of_the_session(client, context, catalog_raw, monkeypatch):
    seen = {}

    def fake_ask(text, ctx, catalog, history=None):
        seen["history"] = history
        return {"reply": "Sure.", "actions": []}

    monkeypatch.setattr(llm, "ask", fake_ask)
    chat(client, "show me the left biceps", context=context, catalog_id="s1", catalog=catalog_raw, session_id="A")
    chat(client, "pause", context=context, session_id="B")
    chat(client, "and what does it do?", context=context, catalog_id="s1", session_id="A")
    roles = [turn["role"] for turn in seen["history"]]
    assert roles == ["user", "assistant"]
    assert seen["history"][0]["content"] == "show me the left biceps"
    assert "Biceps brachii" in seen["history"][1]["content"]
