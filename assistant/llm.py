"""
The LLM fallback (Ollama). Only used when the rules do not understand a sentence.

    answer = ask("what does the deltoid do?", context, catalog, history)
    answer -> {"reply": "The deltoid lifts the arm ...", "actions": []}

`ask` raises LLMError when Ollama cannot be reached, times out or answers something that is not the
JSON we asked for. The pipeline turns that into `source: "error"`.

What comes back is NOT trusted: the pipeline sends every action through actions.sanitize.
"""

from __future__ import annotations

import json
import re
import time

import requests

from . import config, log
from .actions import ACTION_TYPES, CAMERA_OPS, CAMERA_VIEWS, DIRECTIONS, PANELS, PART_KINDS, TIMELINE_OPS
from .catalog import Catalog
from .context import LAYERS, Context

MAX_REPLY_CHARS = 600
WARM_UP_TIMEOUT_S = 180

_ready = False


class LLMError(Exception):
    pass


def ready() -> bool:
    """What /health reports. True after the warm-up or any call worked, False after one failed."""
    return _ready or config.settings.fake_llm


def model_name() -> str:
    return "fake" if config.settings.fake_llm else config.settings.llm_model


# The shape the model must answer in. `view` is separate from `value` because one field cannot be a
# number for zoom and a word for the camera view in this kind of schema; it is merged back below.
ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": list(ACTION_TYPES)},
                    "layer": {"type": "string", "enum": list(LAYERS)},
                    "op": {"type": "string", "enum": sorted(set(TIMELINE_OPS) | set(CAMERA_OPS))},
                    "value": {"type": "number"},
                    "view": {"type": "string", "enum": list(CAMERA_VIEWS)},
                    "yaw": {"type": "number"},
                    "pitch": {"type": "number"},
                    "enabled": {"type": "boolean"},
                    "overlay": {"type": "boolean"},
                    "angles": {"type": "array", "items": {"type": "string"}},
                    "kind": {"type": "string", "enum": list(PART_KINDS)},
                    "name": {"type": "string"},
                    "side": {"type": "string", "enum": ["left", "right", "center", ""]},
                    "panel": {"type": "string", "enum": list(PANELS)},
                    "direction": {"type": "string", "enum": list(DIRECTIONS)},
                },
                "required": ["type"],
            },
        },
    },
    "required": ["reply", "actions"],
}

SYSTEM_PROMPT = """You are BOT, the assistant inside a 3D human anatomy viewer. The user sees an animated avatar \
and can show three layers (body, muscles, skeleton), control the animation timeline, move the camera, run a \
real-time pose estimation from the webcam (joint angles), highlight single muscles and bones, play action clips, \
open side panels and switch avatars. The user typed or said one sentence. It may come from speech recognition, \
so expect misheard words.

Answer with ONE JSON object: {"reply": "...", "actions": [...]}

reply: plain text for the chat box, at most 3 short sentences. No markdown, no lists, no emojis. Write it as if \
the actions will work.
actions: the things the app should do now, in order. Use an action only when the user asks the app to do \
something. For a question (anatomy, sports injuries, training, what you can do) or small talk, answer in reply \
and use "actions": []. Never invent part names, action names or numbers the user did not ask for.

Rules for every answer:
1. A question gets an answer and "actions": []. "What does the calf muscle do?" is a question: explain, do not \
show the part and do not play a clip.
2. Either act or ask, never both. With actions, reply says what you are doing now ("Showing the muscle layer.") \
and has no question in it. If you must ask what the user means, use "actions": [].
3. When the request is clear enough, choose the most likely meaning and act: "it is too fast" means slower, \
"under the skin" means the muscle layer, "the calf" means the main calf muscle.
4. If the app cannot do it (no such action clip, no such part, nothing to do with the viewer), say so in one \
sentence and use "actions": []. Never use another action instead.
5. Do not end the reply with an offer or a question such as "Would you like me to ...?".
6. Joint angles in the current state are in degrees, measured live from the webcam. When the user asks whether \
an angle is normal or good, say the number, give a short general answer, and do not diagnose.

Actions:
{"type":"set_layer","layer":"body|muscles|skeleton"}
{"type":"timeline","op":"play|pause|toggle|restart|next_marker|bookmark|record_start|record_stop"}
{"type":"timeline","op":"faster|slower","value":steps}   (default 1)
{"type":"timeline","op":"set_speed","value":multiplier}   (1 = normal speed)
{"type":"timeline","op":"seek","value":0..1}   (position in the animation cycle)
{"type":"timeline","op":"step","value":seconds}   (negative = back)
{"type":"camera","op":"zoom_in|zoom_out","value":steps}   (default 1, "a lot" = 3)
{"type":"camera","op":"zoom_to","value":0..1}   (0 = farthest, 1 = closest)
{"type":"camera","op":"orbit","yaw":degrees,"pitch":degrees}   (+yaw = camera moves to its right, +pitch = up)
{"type":"camera","op":"view","view":"front|back|left|right|top"}
{"type":"camera","op":"reset"}
{"type":"pose_estimation","enabled":true|false}   (optional "overlay": true|false shows or hides the overlay)
{"type":"read_pose_data"}   (optional "angles": ["right knee", "left hip", "trunk"])
{"type":"show_part","kind":"muscle|bone","name":"<exact name from the part list>","side":"left|right|center|"}
{"type":"clear_selection"}
{"type":"play_action","name":"<one of the available action clips>"}
{"type":"open_panel","panel":"none|actions|pose_estimation|graphs|timeline|saved_videos|settings"}   (none = close)
{"type":"switch_avatar","direction":"next|previous"}

show_part: only with a name from the lists of this avatar's muscles and bones below, written exactly like \
that. When the user describes a part instead of naming it ("the muscle that lifts the arm"), choose the name \
from those lists that fits best. "side" is "" when the user did not say left or right. If the avatar does not \
have the part, say so and use no show_part.
What you can do, if the user asks: show a layer, play / pause / change the speed, zoom and turn the camera, \
start the pose estimation and read the joint angles, show a muscle or a bone by name, play an action clip, \
open a panel, switch the avatar, and answer short questions about anatomy and movement."""


def _state(ctx: Context) -> dict:
    """The context, reduced to what the model needs and to a bounded size."""
    state: dict = {}
    if ctx.layer:
        state["layer"] = ctx.layer
    timeline = {"playing": ctx.playing, "speed": ctx.speed, "speed_steps": ctx.speed_steps or None,
                "recording": ctx.recording}
    timeline = {name: value for name, value in timeline.items() if value is not None}
    if timeline:
        state["timeline"] = timeline
    if ctx.action_playing is not None:
        state["action_playing"] = ctx.action_playing or "(the avatar's own animation)"
    if ctx.panel:
        state["panel"] = ctx.panel
    if ctx.has_pose:
        state["pose"] = {"status": ctx.pose_status or "unknown", "angles": ctx.pose_angles}
    if ctx.selected_part:
        state["selected_part"] = ctx.selected_part
    if ctx.avatar_name or ctx.avatar_count is not None:
        state["avatar"] = {"name": ctx.avatar_name, "index": ctx.avatar_index, "count": ctx.avatar_count}
    if ctx.last_results:
        state["last_results"] = ctx.last_results[:8]
    return state


MAX_PROMPT_PARTS = 12
MAX_ALL_NAMES_CHARS = 6000        # per kind; the real avatar needs about 2400
_SIDE_WORD_RE = re.compile(r"\b(?:left|right|other|opposite|both|same)\b", flags=re.IGNORECASE)


def _parts_in_question(text: str, ctx: Context, catalog: Catalog, history: list[dict]) -> list:
    """
    The parts the model may name: those the sentence could mean, then the highlighted part, then those the
    last turns were about ("ok show it to me" after "what does the gastrocnemius do?" has no name in it).
    """
    parts = list(catalog.candidates(text, MAX_PROMPT_PARTS))
    selected = ctx.selected_part
    if selected:
        part = catalog.part_named(selected.get("name"))
        if part and part not in parts:
            parts.append(part)
    for turn in reversed(history[-4:]):
        if len(parts) >= MAX_PROMPT_PARTS:
            break
        for part in catalog.candidates(turn.get("content", ""), 4):
            if part not in parts:
                parts.append(part)
    return parts[:MAX_PROMPT_PARTS]


def _fix_sides(actions: list[dict], text: str) -> None:
    """The model likes to pick a side nobody asked for. Without a side word in the sentence: "" (the client
    then takes the side nearer to the camera)."""
    if _SIDE_WORD_RE.search(text):
        return
    for action in actions:
        if action.get("type") == "show_part" and action.get("side") in ("left", "right"):
            action["side"] = ""


def build_messages(text: str, ctx: Context, catalog: Catalog | None, history: list[dict]) -> list[dict]:
    lines = [SYSTEM_PROMPT]
    if catalog is not None:
        # Every name, so that a description ("the muscle that lifts the arm") can be answered with a part
        # the avatar has. It comes before everything that changes per request: Ollama then reuses the
        # evaluated prompt up to here.
        for kind, title in (("muscle", "muscles and ligaments"), ("bone", "bones")):
            names = [part.name for part in catalog.parts if part.kind == kind]
            if names:
                lines.append(f"All {title} of this avatar: " + ", ".join(names)[:MAX_ALL_NAMES_CHARS])
    lines += ["", "Current state of the app: " + json.dumps(_state(ctx), ensure_ascii=False)[:2500]]
    actions = ctx.available_actions
    lines.append("Available action clips: " + (", ".join(actions[:40]) if actions else "none"))
    if catalog is None:
        lines.append("Part list: not available right now, so do not use show_part.")
    else:
        parts = _parts_in_question(text, ctx, catalog, history)
        if parts:
            lines.append("Closest to what the user said:")
            for part in parts:
                sides = "/".join(part.sides)
                region = f", {part.region}" if part.region else ""
                lines.append(f"- {part.kind}: {part.name} ({sides}{region})")
        else:
            lines.append("Closest to what the user said: no name in the lists matches a word of the sentence.")
    messages = [{"role": "system", "content": "\n".join(lines)}]
    messages.extend(history)
    messages.append({"role": "user", "content": text})
    return messages


def extract_json(model_text: object) -> dict | None:
    if not isinstance(model_text, str):
        return None
    for candidate in (model_text, (re.search(r"\{.*\}", model_text, flags=re.DOTALL) or [""])[0]):
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    return None


def clean_reply(reply: object) -> str:
    """Plain text, one paragraph, bounded length."""
    if not isinstance(reply, str):
        return ""
    reply = re.sub(r"[*_#`]+", "", reply)
    reply = re.sub(r"\s+", " ", reply).strip()
    if len(reply) > MAX_REPLY_CHARS:
        reply = reply[:MAX_REPLY_CHARS].rsplit(" ", 1)[0] + " ..."
    return reply


def _post(path: str, payload: dict, timeout: float) -> requests.Response:
    return requests.post(f"{config.settings.ollama_url}{path}", json=payload, timeout=timeout)


def _chat(payload: dict) -> requests.Response:
    timeout = config.settings.llm_timeout_s
    response = _post("/api/chat", payload, timeout)
    # Models without a thinking mode may reject the "think" flag.
    if response.status_code == 400 and "think" in response.text.lower():
        payload = {name: value for name, value in payload.items() if name != "think"}
        response = _post("/api/chat", payload, timeout)
    response.raise_for_status()
    return response


def ask(text: str, ctx: Context, catalog: Catalog | None, history: list[dict] | None = None) -> dict:
    """Returns {"reply": str, "actions": list} exactly as the model gave it (actions are not validated here)."""
    global _ready
    settings = config.settings
    if settings.fake_llm:
        return {"reply": f"(fake LLM) You said: {text}", "actions": []}

    payload = {
        "model": settings.llm_model,
        "stream": False,
        "think": False,
        "keep_alive": settings.llm_keep_alive,
        "format": ANSWER_SCHEMA,
        "options": {"temperature": 0, "num_predict": 400},
        "messages": build_messages(text, ctx, catalog, history or []),
    }
    try:
        model_text = _chat(payload).json()["message"]["content"]
    except (requests.RequestException, KeyError, TypeError, ValueError) as error:
        _ready = False
        raise LLMError(f"Ollama did not answer: {error!r}") from error
    _ready = True

    answer = extract_json(model_text)
    if answer is None:
        raise LLMError(f"the model did not answer with JSON: {str(model_text)[:200]!r}")

    actions = answer.get("actions")
    actions = [dict(action) for action in actions if isinstance(action, dict)] if isinstance(actions, list) else []
    for action in actions:
        if action.get("type") == "camera" and action.get("op") == "view" and "view" in action:
            action["value"] = action.pop("view")
    _fix_sides(actions, text)
    return {"reply": clean_reply(answer.get("reply")), "actions": actions}


def warm_up(attempts: int = 1, wait_s: float = 0.0) -> None:
    """
    Loads the model into memory at startup so the first real question is not slow.

    After a reboot this server can be up before Ollama is: the server passes `attempts` and `wait_s` so the
    warm-up is tried again for a while instead of leaving the first question to load the model.
    """
    global _ready
    settings = config.settings
    if settings.fake_llm or not settings.llm_warmup:
        return
    log.info("Loading LLM %s ...", settings.llm_model)
    for attempt in range(1, max(1, attempts) + 1):
        try:
            _post(
                "/api/generate",
                {"model": settings.llm_model, "prompt": "", "keep_alive": settings.llm_keep_alive},
                WARM_UP_TIMEOUT_S,
            ).raise_for_status()
            _ready = True
            log.info("LLM %s ready.", settings.llm_model)
            return
        except requests.RequestException as error:
            if _ready:
                return                     # a real question got through in the meantime
            log.warning("could not load LLM %s from %s (try %d of %d): %r",
                        settings.llm_model, settings.ollama_url, attempt, max(1, attempts), error)
            if attempt < attempts:
                time.sleep(wait_s)
    log.warning("The rule-based commands still work without it.")
