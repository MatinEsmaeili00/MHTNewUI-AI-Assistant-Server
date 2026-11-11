"""The LLM fallback with the HTTP call to Ollama replaced by a fake (no Ollama needed)."""

from __future__ import annotations

import json

import pytest
import requests

from assistant import config, llm, pipeline
from assistant.catalog import store
from assistant.context import Context


class FakeResponse:
    def __init__(self, content=None, status=200, text="", body=None):
        self.status_code = status
        self.text = text
        self._body = body if body is not None else {"message": {"role": "assistant", "content": content}}

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)


@pytest.fixture
def ollama(monkeypatch):
    """Replaces requests.post inside llm.py. Set ollama['answer'] to a dict, a string or an Exception."""
    state = {"answer": {"reply": "Hello.", "actions": []}, "calls": []}

    def fake_post(url, json=None, timeout=None):
        state["calls"].append({"url": url, "payload": json, "timeout": timeout})
        answer = state["answer"]
        if callable(answer):
            answer = answer(json)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, FakeResponse):
            return answer
        return FakeResponse(answer if isinstance(answer, str) else __import__("json").dumps(answer))

    monkeypatch.setattr(llm.requests, "post", fake_post)
    return state


@pytest.fixture
def known_catalog(catalog_raw):
    store.put("cat", catalog_raw)
    return "cat"


def ask(text, context, catalog_id="cat"):
    return pipeline.handle(text, context, catalog_id, None, "session")


# ---- a valid answer ---------------------------------------------------------------------------------------

def test_question_gets_a_reply_and_no_action(ollama, context, known_catalog):
    ollama["answer"] = {"reply": "The deltoid lifts the arm away from the body.", "actions": []}
    answer = ask("what does the deltoid do?", context)
    assert answer == {
        "transcript": "what does the deltoid do?",
        "reply": "The deltoid lifts the arm away from the body.",
        "actions": [],
        "source": "llm",
        "need_catalog": False,
    }
    assert llm.ready() is True


def test_the_request_to_ollama(ollama, context, known_catalog):
    ask("what does the deltoid do?", context)
    assert len(ollama["calls"]) == 1
    call = ollama["calls"][0]
    assert call["url"] == "http://localhost:11434/api/chat"
    assert call["timeout"] == config.settings.llm_timeout_s
    payload = call["payload"]
    assert payload["model"] == "gemma4:e4b"
    assert payload["stream"] is False and payload["think"] is False
    assert payload["options"]["temperature"] == 0
    assert payload["keep_alive"] == "1h"
    assert payload["format"]["required"] == ["reply", "actions"]            # a JSON schema, not just "json"
    assert payload["messages"][-1] == {"role": "user", "content": "what does the deltoid do?"}
    system = payload["messages"][0]
    assert system["role"] == "system"
    # every name of the avatar is in the prompt (for "the muscle that lifts the arm"), and a short list
    # of the parts closest to the sentence
    assert "Deltoid (middle)" in system["content"]
    assert "Vomer" in system["content"] and "Cuboid" in system["content"]
    assert "\n- muscle: Deltoid (middle)" in system["content"]
    assert 1 <= system["content"].count("\n- ") <= 12
    # the state and the available actions are in the prompt
    assert '"layer": "body"' in system["content"]
    assert "Idle, Running, Jogging, TennisForehand" in system["content"]
    assert len(system["content"]) < 12000


def test_actions_from_the_model_are_used_after_validation(ollama, context, known_catalog):
    ollama["answer"] = {
        "reply": "Here are the muscles from behind.",
        "actions": [
            {"type": "set_layer", "layer": "muscles"},
            {"type": "camera", "op": "view", "view": "back"},
            {"type": "timeline", "op": "seek", "value": 40},
        ],
    }
    answer = ask("let me look at the musculature from the rear at forty percent of the gait", context)
    assert answer["source"] == "llm"
    assert answer["actions"] == [
        {"type": "set_layer", "layer": "muscles"},
        {"type": "camera", "op": "view", "value": "back"},
        {"type": "timeline", "op": "seek", "value": 0.4},
    ]
    assert answer["reply"] == "Here are the muscles from behind."


def test_part_names_from_the_model_are_forced_to_the_catalog(ollama, context, known_catalog):
    ollama["answer"] = {
        "reply": "Here is the muscle on the back of the thigh.",
        "actions": [{"type": "show_part", "kind": "bone", "name": "biceps femoris", "side": "right"}],
    }
    answer = ask("the big one at the back of my right thigh that runners always pull", context)
    assert answer["actions"] == [
        {"type": "show_part", "kind": "muscle", "name": "Biceps femoris (long head)", "side": "right"},
    ]


def test_invented_part_is_dropped_and_the_reply_says_so(ollama, context, known_catalog):
    ollama["answer"] = {
        "reply": "Here is the latissimus dorsi.",
        "actions": [{"type": "show_part", "kind": "muscle", "name": "Latissimus dorsi", "side": ""}],
    }
    answer = ask("that big wing shaped one that swimmers have", context)
    assert answer["actions"] == []
    assert answer["source"] == "llm"
    assert "Here is" not in answer["reply"]                   # the model's claim is not passed on
    assert "Latissimus dorsi" in answer["reply"] and "no" in answer["reply"]


def test_invented_action_clip_is_dropped_and_the_reply_says_so(ollama, context, known_catalog):
    ollama["answer"] = {"reply": "She is doing a backflip.", "actions": [{"type": "play_action", "name": "Backflip"}]}
    answer = ask("let her show off something acrobatic", context)
    assert answer["actions"] == []
    assert "Idle, Running, Jogging, TennisForehand" in answer["reply"]


def test_unknown_action_types_and_bad_values_are_dropped(ollama, context, known_catalog):
    ollama["answer"] = {
        "reply": "Done.",
        "actions": [
            {"type": "run_shell", "command": "rm -rf /"},
            {"type": "set_layer", "layer": "nerves"},
            {"type": "camera", "op": "orbit", "yaw": 99999},
            "pause",
        ],
    }
    answer = ask("do something clever with the view", context)
    assert answer["actions"] == [{"type": "camera", "op": "orbit", "yaw": 360}]


def test_reply_is_made_plain_and_short(ollama, context, known_catalog):
    ollama["answer"] = {"reply": "**The deltoid**\n\n- lifts the arm\n- `three` parts " + "x" * 2000, "actions": []}
    reply = ask("tell me everything about the deltoid", context)["reply"]
    assert "*" not in reply and "`" not in reply and "\n" not in reply
    assert len(reply) <= 610


def test_answer_without_reply_or_actions(ollama, context, known_catalog):
    ollama["answer"] = {}
    answer = ask("hmm what now", context)
    assert answer["source"] == "llm" and answer["actions"] == [] and answer["reply"]
    ollama["answer"] = {"reply": "", "actions": [{"type": "clear_selection"}]}
    answer = ask("get rid of that glowing thing", context)
    assert answer["actions"] == [{"type": "clear_selection"}] and answer["reply"]
    ollama["answer"] = {"reply": 5, "actions": "none"}
    assert ask("hmm what now", context)["actions"] == []


def test_json_wrapped_in_other_text_is_still_read(ollama, context, known_catalog):
    ollama["answer"] = 'Sure! {"reply": "Hi there.", "actions": []} Anything else?'
    assert ask("hello", context)["reply"] == "Hi there."


def test_history_is_sent(ollama, context, known_catalog):
    ask("show me the left biceps", context)                    # rules; remembered for the session
    ask("and what does it do?", context)                       # LLM
    messages = ollama["calls"][-1]["payload"]["messages"]
    assert [message["role"] for message in messages] == ["system", "user", "assistant", "user"]
    assert messages[1]["content"] == "show me the left biceps"
    assert "Biceps brachii" in messages[2]["content"]


def test_history_is_capped(ollama, context, known_catalog, monkeypatch):
    monkeypatch.setattr(config.settings, "session_turns", 2)
    for number in range(10):
        ask(f"tell me fact number {number} about posture", context)
    messages = ollama["calls"][-1]["payload"]["messages"]
    assert len(messages) == 1 + 2 * 2 + 1                      # system + two turns + the new sentence


def test_think_flag_is_dropped_when_the_model_rejects_it(ollama, context, known_catalog):
    def answer(payload):
        if "think" in payload:
            return FakeResponse(status=400, text='{"error":"registry.ollama.ai/x does not support thinking"}')
        return {"reply": "Hello.", "actions": []}

    ollama["answer"] = answer
    assert ask("hello", context)["reply"] == "Hello."
    assert len(ollama["calls"]) == 2
    assert "think" not in ollama["calls"][1]["payload"]


# ---- invalid JSON, timeouts, Ollama down ---------------------------------------------------------------------

DOWN = "The language model is not answering right now, but the direct commands still work."


@pytest.mark.parametrize("bad", [
    "I am sorry, I cannot help with that.",
    '{"reply": "cut off in the mid',
    "",
    "[1, 2, 3]",
    FakeResponse(body={"unexpected": "shape"}),
    FakeResponse(body={"message": None}),
    FakeResponse(body=ValueError("not json at all")),
])
def test_invalid_answer_from_the_model(ollama, context, known_catalog, bad):
    ollama["answer"] = bad
    answer = ask("what does the deltoid do?", context)
    assert answer["source"] == "error"
    assert answer["actions"] == [] and answer["need_catalog"] is False
    assert answer["reply"].startswith(DOWN)
    assert "show me the left biceps" in answer["reply"]         # one line of example commands


@pytest.mark.parametrize("error", [
    requests.Timeout("read timed out"),
    requests.ConnectionError("connection refused"),
    FakeResponse(status=500, text="internal error"),
    FakeResponse(status=404, text='{"error":"model not found"}'),
    RuntimeError("something unexpected inside the LLM path"),
])
def test_ollama_down_or_slow(ollama, context, known_catalog, error):
    ollama["answer"] = error
    answer = ask("what does the deltoid do?", context)
    assert answer["source"] == "error"
    assert answer["actions"] == []
    assert answer["reply"].startswith(DOWN)
    assert llm.ready() is False
    # the rule-covered commands still work
    still = ask("show me the left biceps", context)
    assert still["source"] == "rules"
    assert still["actions"] == [{"type": "show_part", "kind": "muscle", "name": "Biceps brachii", "side": "left"}]


def test_llm_ready_follows_the_last_call(ollama, context, known_catalog):
    assert llm.ready() is False
    ask("hello", context)
    assert llm.ready() is True
    ollama["answer"] = requests.ConnectionError("gone")
    ask("hello", context)
    assert llm.ready() is False


def test_failed_turns_are_not_put_into_the_history(ollama, context, known_catalog):
    ollama["answer"] = requests.Timeout("slow")
    ask("first question", context)
    ollama["answer"] = {"reply": "Hi.", "actions": []}
    ask("second question", context)
    messages = ollama["calls"][-1]["payload"]["messages"]
    assert [message["role"] for message in messages] == ["system", "user"]


# ---- warm-up and the fake LLM ------------------------------------------------------------------------------------

def test_warm_up(ollama):
    ollama["answer"] = FakeResponse(body={"done": True})
    llm.warm_up()
    assert ollama["calls"][0]["url"].endswith("/api/generate")
    assert ollama["calls"][0]["payload"] == {"model": "gemma4:e4b", "prompt": "", "keep_alive": "1h"}
    assert llm.ready() is True


def test_warm_up_failure_is_not_fatal(ollama):
    ollama["answer"] = requests.ConnectionError("no ollama here")
    llm.warm_up()                                            # must not raise
    assert llm.ready() is False


def test_warm_up_tries_again_while_ollama_is_not_up_yet(ollama, monkeypatch):
    """After a reboot the server can be up before Ollama."""
    monkeypatch.setattr(llm.time, "sleep", lambda seconds: None)
    answers = [requests.ConnectionError("refused"), requests.ConnectionError("refused"), FakeResponse("")]
    ollama["answer"] = lambda payload: answers.pop(0)
    llm.warm_up(attempts=5, wait_s=10)
    assert llm.ready() is True
    assert len(ollama["calls"]) == 3


def test_warm_up_gives_up_after_the_last_try(ollama, monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda seconds: None)
    ollama["answer"] = requests.ConnectionError("refused")
    llm.warm_up(attempts=3, wait_s=10)
    assert llm.ready() is False
    assert len(ollama["calls"]) == 3


def test_warm_up_can_be_switched_off(ollama, monkeypatch):
    monkeypatch.setattr(config.settings, "llm_warmup", False)
    llm.warm_up()
    assert ollama["calls"] == []


def test_fake_llm_needs_no_ollama(context, known_catalog, monkeypatch):
    monkeypatch.setattr(config.settings, "fake_llm", True)
    answer = ask("what does the deltoid do?", context)
    assert answer["source"] == "llm" and answer["actions"] == []
    assert answer["reply"] == "(fake LLM) You said: what does the deltoid do?"
    assert llm.ready() is True and llm.model_name() == "fake"


# ---- the prompt ------------------------------------------------------------------------------------------------------

def test_prompt_without_catalog_and_context(catalog):
    messages = llm.build_messages("hello", Context(None), None, [])
    assert "do not use show_part" in messages[0]["content"]
    assert "Available action clips: none" in messages[0]["content"]
    messages = llm.build_messages("tell me a joke", Context({}), catalog, [])
    assert "no name in the lists matches a word of the sentence" in messages[0]["content"]
    assert "All muscles and ligaments of this avatar: " in messages[0]["content"]
    assert "All bones of this avatar: " in messages[0]["content"]


def test_names_come_before_what_changes_per_request(catalog, context):
    """Ollama reuses the evaluated prompt up to the first difference: the long name lists must be in that part."""
    first = llm.build_messages("what does the deltoid do?", Context(context), catalog, [])[0]["content"]
    context["layer"] = "skeleton"
    second = llm.build_messages("show me the bone of the heel", Context(context), catalog, [])[0]["content"]
    same = next(i for i, (a, b) in enumerate(zip(first, second)) if a != b)
    assert first.index("All bones of this avatar: ") < same
    assert first.index("Current state of the app") <= same


def test_follow_up_without_a_name_gets_the_parts_of_the_last_turns(ollama, context, known_catalog):
    context.pop("selected_part")
    ask("what does the gastrocnemius do?", context)            # LLM; remembered for the session
    ask("ok show it to me", context)                           # no name in this sentence
    closest = ollama["calls"][-1]["payload"]["messages"][0]["content"].split("Closest to what the user said")[1]
    assert "- muscle: Gastrocnemius" in closest


def test_highlighted_part_is_among_the_closest_parts(ollama, context, known_catalog):
    ask("tell me more about it", context)                      # selected_part: left Biceps brachii
    closest = ollama["calls"][-1]["payload"]["messages"][0]["content"].split("Closest to what the user said")[1]
    assert "- muscle: Biceps brachii" in closest


@pytest.mark.parametrize("text, side", [
    ("show me the muscle runners pull at the back of the thigh", ""),      # nobody said a side
    ("show me the muscle that bends the knee on the left", "left"),
    ("show me the muscle that bends the knee on the other leg", "right"),
])
def test_side_from_the_model_is_kept_only_when_the_user_said_one(ollama, context, known_catalog, text, side):
    model_side = side or "right"
    ollama["answer"] = {"reply": "Showing it.", "actions": [
        {"type": "show_part", "kind": "muscle", "name": "Biceps femoris (long head)", "side": model_side}]}
    answer = ask(text, context)
    assert answer["source"] == "llm"
    assert answer["actions"] == [
        {"type": "show_part", "kind": "muscle", "name": "Biceps femoris (long head)", "side": side}]


def test_prompt_says_act_or_ask():
    for line in ("Either act or ask, never both", "A question gets an answer", "Never use another action instead"):
        assert line in llm.SYSTEM_PROMPT


def test_prompt_lists_every_action_type():
    for kind in ("set_layer", "timeline", "camera", "pose_estimation", "read_pose_data", "show_part",
                 "clear_selection", "play_action", "open_panel", "switch_avatar"):
        assert f'"type":"{kind}"' in llm.SYSTEM_PROMPT
    assert set(llm.ANSWER_SCHEMA["properties"]["actions"]["items"]["properties"]["type"]["enum"]) == {
        "set_layer", "timeline", "camera", "pose_estimation", "read_pose_data", "show_part", "clear_selection",
        "play_action", "open_panel", "switch_avatar",
    }
    json.dumps(llm.ANSWER_SCHEMA)                             # must be plain JSON
