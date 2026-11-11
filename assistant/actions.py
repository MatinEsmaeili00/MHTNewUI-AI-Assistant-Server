"""
The actions of PROTOCOL.md: builders (used by the rules) and `sanitize`, which every action passes
through before it is sent to Unreal, whether it came from the rules or from the LLM.

`sanitize` never raises. What it does to a list of raw actions:
  - unknown `type`                 -> dropped
  - a value outside an enum        -> dropped (after trying a few obvious synonyms)
  - numbers                        -> clamped to the allowed range
  - show_part                      -> `name` replaced by the exact catalog name, `kind` and `side` fixed;
                                      dropped if the catalog has no such part
  - play_action                    -> `name` replaced by the exact name in context.action.available;
                                      dropped if the avatar has no such action
  - fields the protocol does not know -> removed
"""

from __future__ import annotations

import difflib
import re

from . import config
from .catalog import Catalog, PartMatch
from .context import LAYERS, Context
from .text import normalize, tidy_number, to_float

TIMELINE_OPS = (
    "play", "pause", "toggle", "restart", "faster", "slower", "set_speed", "seek", "step",
    "next_marker", "bookmark", "record_start", "record_stop",
)
CAMERA_OPS = ("zoom_in", "zoom_out", "zoom_to", "orbit", "view", "reset")
CAMERA_VIEWS = ("front", "back", "left", "right", "top")
PANELS = ("none", "actions", "pose_estimation", "graphs", "timeline", "saved_videos", "settings")
DIRECTIONS = ("next", "previous")
PART_KINDS = ("muscle", "bone")
PART_SIDES = ("left", "right", "center", "")
ACTION_TYPES = (
    "set_layer", "timeline", "camera", "pose_estimation", "read_pose_data", "show_part", "clear_selection",
    "play_action", "open_panel", "switch_avatar",
)

# Limits for numbers.
MAX_SPEED_STEPS = 5          # there are 6 speed steps, so 5 jumps is the whole range
MAX_ZOOM_STEPS = 10
MIN_SPEED, MAX_SPEED = 0.05, 10.0
MAX_STEP_SECONDS = 10.0
DEFAULT_STEP_SECONDS = 0.1
MAX_YAW, MAX_PITCH = 360.0, 90.0

# The joint and segment angles Sports2D reports (lower case, as in context.pose.angles).
ANGLE_JOINTS = ("ankle", "knee", "hip", "shoulder", "elbow", "wrist")
ANGLE_SEGMENTS = ("foot", "shank", "thigh", "arm", "forearm")
ANGLE_CENTER = ("pelvis", "trunk", "shoulders", "head")
ANGLE_NAMES = tuple(
    [f"{side} {name}" for name in ANGLE_JOINTS + ANGLE_SEGMENTS for side in ("right", "left")] + list(ANGLE_CENTER)
)

_SYNONYMS = {
    "layer": {"muscle": "muscles", "muscular": "muscles", "bones": "skeleton", "bone": "skeleton",
              "skeletal": "skeleton", "skin": "body"},
    "view": {"behind": "back", "rear": "back", "above": "top", "side": "right"},
    "panel": {"close": "none", "closed": "none", "": "none", "pose": "pose_estimation", "graph": "graphs",
              "videos": "saved_videos", "saved videos": "saved_videos", "action": "actions",
              "pose estimation": "pose_estimation"},
    "direction": {"prev": "previous", "back": "previous", "forward": "next"},
}


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def set_layer(layer: str) -> dict:
    return {"type": "set_layer", "layer": layer}


def timeline(op: str, value: float | None = None) -> dict:
    action: dict = {"type": "timeline", "op": op}
    if value is not None:
        action["value"] = tidy_number(value)
    return action


def camera(op: str, value: object = None, yaw: float | None = None, pitch: float | None = None) -> dict:
    action: dict = {"type": "camera", "op": op}
    if value is not None:
        action["value"] = value if isinstance(value, str) else tidy_number(value)  # type: ignore[arg-type]
    if yaw is not None:
        action["yaw"] = tidy_number(yaw)
    if pitch is not None:
        action["pitch"] = tidy_number(pitch)
    return action


def pose_estimation(enabled: bool, show_panel: bool | None = None, overlay: bool | None = None) -> dict:
    action: dict = {"type": "pose_estimation", "enabled": enabled}
    if show_panel is not None:
        action["show_panel"] = show_panel
    if overlay is not None:
        action["overlay"] = overlay
    return action


def read_pose_data(angles: list[str] | None = None) -> dict:
    action: dict = {"type": "read_pose_data"}
    if angles:
        action["angles"] = list(angles)
    return action


def show_part(kind: str, name: str, side: str = "") -> dict:
    return {"type": "show_part", "kind": kind, "name": name, "side": side}


def clear_selection() -> dict:
    return {"type": "clear_selection"}


def play_action(name: str) -> dict:
    return {"type": "play_action", "name": name}


def open_panel(panel: str) -> dict:
    return {"type": "open_panel", "panel": panel}


def switch_avatar(direction: str) -> dict:
    return {"type": "switch_avatar", "direction": direction}


# ---------------------------------------------------------------------------
# Names of action clips: "make her run" -> "Running", "tennis forehand" -> "TennisForehand"
# ---------------------------------------------------------------------------

def _stem(word: str) -> str:
    if word.endswith("ing") and len(word) > 5:
        word = word[:-3]
    elif word.endswith("s") and len(word) > 3 and not word.endswith("ss"):
        word = word[:-1]
    if word.endswith("e") and len(word) > 3:
        word = word[:-1]
    if len(word) > 2 and word[-1] == word[-2]:
        word = word[:-1]
    return word


def action_words(name: object) -> list[str]:
    """'TennisForehand' -> ['tenni', 'forehand'], 'Running' -> ['run'], 'jog' -> ['jog']."""
    if not isinstance(name, str):
        return []
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).replace("_", " ")
    return [_stem(word) for word in normalize(spaced).split()]


def match_action_name(heard: object, available: list[str], fuzzy: bool = True) -> str | list[str] | None:
    """
    The name in `available` that `heard` means. Returns the name, a list of names if several fit
    equally well ("tennis" with TennisForehand and TennisBackhand), or None.
    """
    words = action_words(heard)
    if not words or not available:
        return None
    wanted = "".join(words)
    known = {name: action_words(name) for name in available}
    for name, name_words in known.items():
        if "".join(name_words) == wanted:
            return name
    containing = [name for name, name_words in known.items() if set(words) <= set(name_words)]
    if len(containing) == 1:
        return containing[0]
    if len(containing) > 1:
        return containing
    if fuzzy and len(wanted) >= 4:
        scored = sorted(
            ((difflib.SequenceMatcher(None, wanted, "".join(name_words)).ratio(), name)
             for name, name_words in known.items()),
            reverse=True,
        )
        if scored[0][0] >= 0.82:
            return scored[0][1]
    return None


def normalize_angle_names(names: object, context: Context | None = None) -> list[str]:
    """['Right Knee', 'knee', 'torso'] -> ['right knee', 'left knee', 'trunk'] (unknown names are dropped)."""
    if isinstance(names, str):
        names = [names]
    if not isinstance(names, list):
        return []
    known = set(ANGLE_NAMES) | set(context.pose_angles if context else {})
    synonyms = {"torso": "trunk", "back": "trunk", "spine": "trunk", "shin": "shank", "lower leg": "shank",
                "upper arm": "arm", "neck": "head"}
    clean: list[str] = []
    for name in names[:30]:
        text = normalize(name)
        if not text:
            continue
        side = re.search(r"\b(left|right)\b", text)
        rest = re.sub(r"\b(?:left|right|angles?|joints?|the)\b", " ", text)
        rest = re.sub(r"\s+", " ", rest).strip()
        rest = synonyms.get(rest, rest)
        if text in known:
            candidates = [text]
        elif rest in ANGLE_CENTER:
            candidates = [rest]
        elif rest.endswith("s") and rest[:-1] in ANGLE_JOINTS + ANGLE_SEGMENTS and not side:
            candidates = [f"right {rest[:-1]}", f"left {rest[:-1]}"]
        elif rest in ANGLE_JOINTS + ANGLE_SEGMENTS:
            candidates = [f"{side.group(1)} {rest}"] if side else [f"right {rest}", f"left {rest}"]
        else:
            candidates = []
        for candidate in candidates:
            if candidate not in clean:
                clean.append(candidate)
    return clean


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _enum(value: object, allowed: tuple[str, ...], field: str) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    text = _SYNONYMS.get(field, {}).get(text, text)
    return text if text in allowed else None


def _bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    return None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _steps(value: object, maximum: int) -> int | None:
    number = to_float(value)
    if number is None:
        return None
    return int(_clamp(round(number), 1, maximum))


def _clean_timeline(raw: dict, context: Context) -> tuple[dict | None, str]:
    op = _enum(raw.get("op"), TIMELINE_OPS, "op")
    if not op:
        return None, f"timeline: unknown op {raw.get('op')!r}"
    value = to_float(raw.get("value"))
    if op in ("faster", "slower"):
        steps = _steps(value, MAX_SPEED_STEPS)
        return timeline(op, steps if steps and steps > 1 else None), ""
    if op == "set_speed":
        if value is None or value <= 0:
            return None, "timeline set_speed: no usable value"
        return timeline(op, _clamp(value, MIN_SPEED, MAX_SPEED)), ""
    if op == "seek":
        if value is None:
            return None, "timeline seek: no value"
        if 1 < value <= 100:
            value = value / 100.0          # "30" meant as 30 percent
        return timeline(op, _clamp(value, 0.0, 1.0)), ""
    if op == "step":
        if value is None:
            value = DEFAULT_STEP_SECONDS
        if value == 0:
            return None, "timeline step: zero seconds"
        return timeline(op, _clamp(value, -MAX_STEP_SECONDS, MAX_STEP_SECONDS)), ""
    return timeline(op), ""


def _clean_camera(raw: dict) -> tuple[dict | None, str]:
    op = _enum(raw.get("op"), CAMERA_OPS, "op")
    if not op:
        return None, f"camera: unknown op {raw.get('op')!r}"
    if op in ("zoom_in", "zoom_out"):
        steps = _steps(raw.get("value"), MAX_ZOOM_STEPS)
        return camera(op, steps if steps and steps > 1 else None), ""
    if op == "zoom_to":
        value = to_float(raw.get("value"))
        if value is None:
            return None, "camera zoom_to: no value"
        if 1 < value <= 100:
            value = value / 100.0
        return camera(op, _clamp(value, 0.0, 1.0)), ""
    if op == "orbit":
        yaw = _clamp(to_float(raw.get("yaw"), 0.0) or 0.0, -MAX_YAW, MAX_YAW)
        pitch = _clamp(to_float(raw.get("pitch"), 0.0) or 0.0, -MAX_PITCH, MAX_PITCH)
        if yaw == 0 and pitch == 0:
            return None, "camera orbit: no angle"
        return camera(op, yaw=yaw if yaw else None, pitch=pitch if pitch else None), ""
    if op == "view":
        view = _enum(raw.get("value"), CAMERA_VIEWS, "view") or _enum(raw.get("view"), CAMERA_VIEWS, "view")
        if not view:
            return None, f"camera view: unknown view {raw.get('value')!r}"
        return camera(op, view), ""
    return camera(op), ""


def _clean_show_part(raw: dict, catalog: Catalog | None) -> tuple[dict | None, str]:
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        return None, "show_part: no name"
    if catalog is None:
        return None, f"show_part: no catalog to check {name!r} against"
    kind = _enum(raw.get("kind"), PART_KINDS, "kind") or ""
    side = _enum(raw.get("side"), PART_SIDES, "side") or ""
    name = name.strip()[:120]
    part = catalog.part_named(name, kind)
    if part is None:
        found = catalog.find(f"{side} {name}".strip(), prefer_kind=kind)
        if not isinstance(found, PartMatch):
            return None, f"show_part: {name!r} is not in the catalog"
        part = found.part
    if part.sides == ("center",):
        side = "center"
    elif side not in part.sides:
        side = ""
    action = show_part(part.kind, part.name, side)
    for option in ("describe", "focus"):
        value = _bool(raw.get(option))
        if value is not None:
            action[option] = value
    return action, ""


def _clean_one(raw: object, context: Context, catalog: Catalog | None) -> tuple[dict | None, str]:
    if not isinstance(raw, dict):
        return None, "not an object"
    kind = raw.get("type")
    if not isinstance(kind, str) or kind.strip().lower() not in ACTION_TYPES:
        return None, f"unknown type {str(kind)[:40]!r}"
    kind = kind.strip().lower()

    if kind == "set_layer":
        layer = _enum(raw.get("layer"), LAYERS, "layer")
        return (set_layer(layer), "") if layer else (None, f"set_layer: unknown layer {raw.get('layer')!r}")
    if kind == "timeline":
        return _clean_timeline(raw, context)
    if kind == "camera":
        return _clean_camera(raw)
    if kind == "pose_estimation":
        enabled = _bool(raw.get("enabled"))
        if enabled is None:
            return None, "pose_estimation: enabled is not true or false"
        return pose_estimation(enabled, _bool(raw.get("show_panel")), _bool(raw.get("overlay"))), ""
    if kind == "read_pose_data":
        return read_pose_data(normalize_angle_names(raw.get("angles"), context)), ""
    if kind == "show_part":
        return _clean_show_part(raw, catalog)
    if kind == "clear_selection":
        return clear_selection(), ""
    if kind == "play_action":
        name = match_action_name(raw.get("name"), context.available_actions)
        if not isinstance(name, str):
            return None, f"play_action: {str(raw.get('name'))[:60]!r} is not one of the available actions"
        return play_action(name), ""
    if kind == "open_panel":
        panel = _enum(raw.get("panel"), PANELS, "panel")
        return (open_panel(panel), "") if panel else (None, f"open_panel: unknown panel {raw.get('panel')!r}")
    direction = _enum(raw.get("direction"), DIRECTIONS, "direction")
    if direction:
        return switch_avatar(direction), ""
    return None, f"switch_avatar: unknown direction {raw.get('direction')!r}"


def sanitize(raw_actions: object, context: Context | object = None, catalog: Catalog | None = None):
    """
    Returns (actions, dropped): the valid actions in protocol form, and one note per action that was
    thrown away as (raw action, reason).
    """
    if not isinstance(context, Context):
        context = Context(context)
    if isinstance(raw_actions, dict):
        raw_actions = [raw_actions]
    if not isinstance(raw_actions, list):
        return [], []
    actions: list[dict] = []
    dropped: list[tuple[object, str]] = []
    for raw in raw_actions[:32]:
        try:
            action, reason = _clean_one(raw, context, catalog)
        except Exception as error:                     # never let one odd action break the answer
            action, reason = None, f"could not be checked: {error!r}"
        if action is None:
            dropped.append((raw, reason))
        elif len(actions) < config.settings.max_actions:
            actions.append(action)
    return actions, dropped
