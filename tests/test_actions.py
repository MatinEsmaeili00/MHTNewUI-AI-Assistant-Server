"""Validation of actions (actions.sanitize): what may leave the server and what may not."""

from __future__ import annotations

import pytest

from assistant.actions import ANGLE_NAMES, match_action_name, normalize_angle_names, sanitize


def clean(action, context, catalog=None):
    actions, dropped = sanitize([action], context, catalog)
    assert len(actions) + len(dropped) == 1
    return actions[0] if actions else None


# ---- every action type in its valid form passes unchanged ---------------------------------------------

VALID = [
    {"type": "set_layer", "layer": "muscles"},
    {"type": "timeline", "op": "play"},
    {"type": "timeline", "op": "pause"},
    {"type": "timeline", "op": "toggle"},
    {"type": "timeline", "op": "restart"},
    {"type": "timeline", "op": "faster"},
    {"type": "timeline", "op": "slower", "value": 2},
    {"type": "timeline", "op": "set_speed", "value": 0.5},
    {"type": "timeline", "op": "seek", "value": 0.3},
    {"type": "timeline", "op": "step", "value": -0.1},
    {"type": "timeline", "op": "next_marker"},
    {"type": "timeline", "op": "bookmark"},
    {"type": "timeline", "op": "record_start"},
    {"type": "timeline", "op": "record_stop"},
    {"type": "camera", "op": "zoom_in", "value": 2},
    {"type": "camera", "op": "zoom_out"},
    {"type": "camera", "op": "zoom_to", "value": 0.5},
    {"type": "camera", "op": "orbit", "yaw": -45},
    {"type": "camera", "op": "orbit", "yaw": 30, "pitch": 10},
    {"type": "camera", "op": "view", "value": "back"},
    {"type": "camera", "op": "reset"},
    {"type": "pose_estimation", "enabled": True},
    {"type": "pose_estimation", "enabled": True, "show_panel": False, "overlay": False},
    {"type": "read_pose_data"},
    {"type": "read_pose_data", "angles": ["right knee", "left knee"]},
    {"type": "show_part", "kind": "muscle", "name": "Biceps brachii", "side": "left"},
    {"type": "show_part", "kind": "bone", "name": "Femur", "side": ""},
    {"type": "show_part", "kind": "bone", "name": "Mandible (jaw)", "side": "center", "describe": False},
    {"type": "clear_selection"},
    {"type": "play_action", "name": "Running"},
    {"type": "open_panel", "panel": "graphs"},
    {"type": "open_panel", "panel": "none"},
    {"type": "switch_avatar", "direction": "previous"},
]


@pytest.mark.parametrize("action", VALID, ids=[str(a) for a in VALID])
def test_valid_actions_pass_unchanged(action, context, catalog):
    assert clean(dict(action), context, catalog) == action


# ---- unknown types and wrong shapes --------------------------------------------------------------------

@pytest.mark.parametrize("action", [
    {"type": "delete_everything"},
    {"type": "run_shell", "command": "rm -rf /"},
    {"type": ""},
    {"type": None},
    {"type": 3},
    {"layer": "muscles"},
    {},
    "set_layer",
    None,
    42,
    ["set_layer"],
])
def test_unknown_types_are_dropped(action, context, catalog):
    assert clean(action, context, catalog) is None


def test_unknown_fields_are_removed(context, catalog):
    action = {"type": "set_layer", "layer": "body", "command": "rm -rf /", "extra": {"a": 1}}
    assert clean(action, context, catalog) == {"type": "set_layer", "layer": "body"}
    action = {"type": "clear_selection", "name": "x", "value": 3}
    assert clean(action, context, catalog) == {"type": "clear_selection"}
    action = {"type": "timeline", "op": "pause", "value": 9}
    assert clean(action, context, catalog) == {"type": "timeline", "op": "pause"}


def test_not_a_list(context, catalog):
    assert sanitize(None, context, catalog) == ([], [])
    assert sanitize("pause", context, catalog) == ([], [])
    assert sanitize({"type": "clear_selection"}, context, catalog)[0] == [{"type": "clear_selection"}]


def test_at_most_eight_actions(context, catalog):
    actions, _ = sanitize([{"type": "camera", "op": "zoom_in"}] * 50, context, catalog)
    assert len(actions) == 8


# ---- enums ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("action", [
    {"type": "set_layer", "layer": "nerves"},
    {"type": "set_layer"},
    {"type": "set_layer", "layer": 2},
    {"type": "timeline", "op": "explode"},
    {"type": "timeline"},
    {"type": "camera", "op": "teleport"},
    {"type": "camera", "op": "view", "value": "inside"},
    {"type": "camera", "op": "view"},
    {"type": "camera", "op": "view", "value": 3},
    {"type": "pose_estimation"},
    {"type": "pose_estimation", "enabled": "maybe"},
    {"type": "open_panel", "panel": "secrets"},
    {"type": "open_panel"},
    {"type": "switch_avatar", "direction": "sideways"},
    {"type": "switch_avatar"},
])
def test_bad_enums_are_dropped(action, context, catalog):
    assert clean(action, context, catalog) is None


@pytest.mark.parametrize("action, expected", [
    ({"type": "SET_LAYER", "layer": "Muscles"}, {"type": "set_layer", "layer": "muscles"}),
    ({"type": "set_layer", "layer": "bones"}, {"type": "set_layer", "layer": "skeleton"}),
    ({"type": "set_layer", "layer": "muscle"}, {"type": "set_layer", "layer": "muscles"}),
    ({"type": "set_layer", "layer": "skin"}, {"type": "set_layer", "layer": "body"}),
    ({"type": "camera", "op": "view", "value": "behind"}, {"type": "camera", "op": "view", "value": "back"}),
    ({"type": "camera", "op": "view", "view": "top"}, {"type": "camera", "op": "view", "value": "top"}),
    ({"type": "open_panel", "panel": "close"}, {"type": "open_panel", "panel": "none"}),
    ({"type": "switch_avatar", "direction": "prev"}, {"type": "switch_avatar", "direction": "previous"}),
    ({"type": "pose_estimation", "enabled": "true"}, {"type": "pose_estimation", "enabled": True}),
    ({"type": "pose_estimation", "enabled": 0}, {"type": "pose_estimation", "enabled": False}),
])
def test_obvious_synonyms_are_repaired(action, expected, context, catalog):
    assert clean(action, context, catalog) == expected


# ---- numbers ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("action, expected", [
    ({"type": "timeline", "op": "seek", "value": 7.5}, {"type": "timeline", "op": "seek", "value": 0.075}),
    ({"type": "timeline", "op": "seek", "value": 30}, {"type": "timeline", "op": "seek", "value": 0.3}),
    ({"type": "timeline", "op": "seek", "value": -2}, {"type": "timeline", "op": "seek", "value": 0}),
    ({"type": "timeline", "op": "seek", "value": 5000}, {"type": "timeline", "op": "seek", "value": 1}),
    ({"type": "timeline", "op": "seek", "value": "0.25"}, {"type": "timeline", "op": "seek", "value": 0.25}),
    ({"type": "timeline", "op": "set_speed", "value": 1000}, {"type": "timeline", "op": "set_speed", "value": 10}),
    ({"type": "timeline", "op": "set_speed", "value": 0.001}, {"type": "timeline", "op": "set_speed", "value": 0.05}),
    ({"type": "timeline", "op": "set_speed", "value": 2.0}, {"type": "timeline", "op": "set_speed", "value": 2}),
    ({"type": "timeline", "op": "faster", "value": 99}, {"type": "timeline", "op": "faster", "value": 5}),
    ({"type": "timeline", "op": "faster", "value": 1}, {"type": "timeline", "op": "faster"}),
    ({"type": "timeline", "op": "slower", "value": -3}, {"type": "timeline", "op": "slower"}),
    ({"type": "timeline", "op": "slower", "value": "x"}, {"type": "timeline", "op": "slower"}),
    ({"type": "timeline", "op": "step", "value": 500}, {"type": "timeline", "op": "step", "value": 10}),
    ({"type": "timeline", "op": "step", "value": -500}, {"type": "timeline", "op": "step", "value": -10}),
    ({"type": "timeline", "op": "step"}, {"type": "timeline", "op": "step", "value": 0.1}),
    ({"type": "camera", "op": "zoom_in", "value": 1000}, {"type": "camera", "op": "zoom_in", "value": 10}),
    ({"type": "camera", "op": "zoom_in", "value": 0}, {"type": "camera", "op": "zoom_in"}),
    ({"type": "camera", "op": "zoom_out", "value": 2.6}, {"type": "camera", "op": "zoom_out", "value": 3}),
    ({"type": "camera", "op": "zoom_to", "value": 3}, {"type": "camera", "op": "zoom_to", "value": 0.03}),
    ({"type": "camera", "op": "zoom_to", "value": -1}, {"type": "camera", "op": "zoom_to", "value": 0}),
    ({"type": "camera", "op": "zoom_to", "value": 1e9}, {"type": "camera", "op": "zoom_to", "value": 1}),
    ({"type": "camera", "op": "orbit", "yaw": 100000}, {"type": "camera", "op": "orbit", "yaw": 360}),
    ({"type": "camera", "op": "orbit", "yaw": -720, "pitch": 500},
     {"type": "camera", "op": "orbit", "yaw": -360, "pitch": 90}),
    ({"type": "camera", "op": "orbit", "pitch": -300}, {"type": "camera", "op": "orbit", "pitch": -90}),
])
def test_numbers_are_clamped(action, expected, context, catalog):
    assert clean(action, context, catalog) == expected


@pytest.mark.parametrize("action", [
    {"type": "timeline", "op": "seek"},
    {"type": "timeline", "op": "seek", "value": "the middle"},
    {"type": "timeline", "op": "seek", "value": float("nan")},
    {"type": "timeline", "op": "set_speed"},
    {"type": "timeline", "op": "set_speed", "value": 0},
    {"type": "timeline", "op": "set_speed", "value": -1},
    {"type": "timeline", "op": "set_speed", "value": float("inf")},
    {"type": "timeline", "op": "step", "value": 0},
    {"type": "camera", "op": "zoom_to"},
    {"type": "camera", "op": "orbit"},
    {"type": "camera", "op": "orbit", "yaw": 0, "pitch": 0},
    {"type": "camera", "op": "orbit", "yaw": "left"},
])
def test_actions_without_a_usable_number_are_dropped(action, context, catalog):
    assert clean(action, context, catalog) is None


# ---- show_part: only exact catalog names ---------------------------------------------------------------------

@pytest.mark.parametrize("action, expected", [
    # the exact name in another spelling
    ({"type": "show_part", "kind": "muscle", "name": "biceps brachii", "side": "left"},
     {"type": "show_part", "kind": "muscle", "name": "Biceps brachii", "side": "left"}),
    # a common name -> the catalog name
    ({"type": "show_part", "kind": "muscle", "name": "hamstring", "side": "right"},
     {"type": "show_part", "kind": "muscle", "name": "Biceps femoris (long head)", "side": "right"}),
    ({"type": "show_part", "kind": "muscle", "name": "Deltoid", "side": ""},
     {"type": "show_part", "kind": "muscle", "name": "Deltoid (middle)", "side": ""}),
    # the kind is taken from the catalog, not from the model
    ({"type": "show_part", "kind": "muscle", "name": "Femur", "side": "left"},
     {"type": "show_part", "kind": "bone", "name": "Femur", "side": "left"}),
    ({"type": "show_part", "name": "Femur"},
     {"type": "show_part", "kind": "bone", "name": "Femur", "side": ""}),
    # a centre-only part is always "center"
    ({"type": "show_part", "kind": "bone", "name": "Sacrum", "side": "left"},
     {"type": "show_part", "kind": "bone", "name": "Sacrum", "side": "center"}),
    ({"type": "show_part", "kind": "bone", "name": "Sacrum"},
     {"type": "show_part", "kind": "bone", "name": "Sacrum", "side": "center"}),
    # a side the part does not have, or nonsense -> ""
    ({"type": "show_part", "kind": "bone", "name": "Femur", "side": "center"},
     {"type": "show_part", "kind": "bone", "name": "Femur", "side": ""}),
    ({"type": "show_part", "kind": "bone", "name": "Femur", "side": "both"},
     {"type": "show_part", "kind": "bone", "name": "Femur", "side": ""}),
    # misspelled by the model
    ({"type": "show_part", "kind": "muscle", "name": "Gastrocnemious", "side": "LEFT"},
     {"type": "show_part", "kind": "muscle", "name": "Gastrocnemius", "side": "left"}),
    # optional flags survive only as booleans
    ({"type": "show_part", "kind": "bone", "name": "Tibia", "side": "", "describe": False, "focus": "yes"},
     {"type": "show_part", "kind": "bone", "name": "Tibia", "side": "", "describe": False}),
])
def test_show_part_is_forced_to_the_catalog(action, expected, context, catalog):
    assert clean(action, context, catalog) == expected


@pytest.mark.parametrize("action", [
    {"type": "show_part", "kind": "muscle", "name": "Latissimus dorsi", "side": "left"},
    {"type": "show_part", "kind": "muscle", "name": "Flux capacitor", "side": ""},
    {"type": "show_part", "kind": "bone", "name": "", "side": ""},
    {"type": "show_part", "kind": "bone", "side": "left"},
    {"type": "show_part", "kind": "bone", "name": ["Femur"], "side": ""},
    {"type": "show_part", "kind": "bone", "name": "vastus", "side": ""},        # three parts fit: not exact
])
def test_show_part_with_a_name_that_is_not_in_the_catalog_is_dropped(action, context, catalog):
    assert clean(action, context, catalog) is None


def test_show_part_without_a_catalog_is_dropped(context):
    action = {"type": "show_part", "kind": "bone", "name": "Femur", "side": ""}
    assert clean(action, context, None) is None


def test_every_show_part_that_passes_uses_an_exact_catalog_name(context, catalog):
    names = {part.name for part in catalog.parts}
    for heard in ("femur", "HAMSTRING", "the left biceps", "kneecap", "L3", "gastrocnemious", "lats", "banana"):
        action = clean({"type": "show_part", "kind": "muscle", "name": heard, "side": ""}, context, catalog)
        assert action is None or action["name"] in names


# ---- play_action: only names the avatar has ---------------------------------------------------------------------

@pytest.mark.parametrize("name, expected", [
    ("Running", "Running"),
    ("running", "Running"),
    ("run", "Running"),
    ("RUN", "Running"),
    ("jog", "Jogging"),
    ("tennis forehand", "TennisForehand"),
    ("Tennis_Forehand", "TennisForehand"),
    ("forehand", "TennisForehand"),
    ("idle", "Idle"),
])
def test_play_action_is_forced_to_the_available_names(name, expected, context, catalog):
    assert clean({"type": "play_action", "name": name}, context, catalog) == {"type": "play_action", "name": expected}


@pytest.mark.parametrize("name", ["Jumping", "Backflip", "", None, 3, ["Running"], "fly to the moon"])
def test_play_action_with_an_unknown_name_is_dropped(name, context, catalog):
    assert clean({"type": "play_action", "name": name}, context, catalog) is None


def test_play_action_without_available_actions_is_dropped(catalog):
    assert clean({"type": "play_action", "name": "Running"}, {}, catalog) is None
    assert clean({"type": "play_action", "name": "Running"}, {"action": {"available": []}}, catalog) is None
    assert clean({"type": "play_action", "name": "Running"}, None, catalog) is None


def test_match_action_name():
    available = ["Idle", "Running", "TennisForehand", "TennisBackhand"]
    assert match_action_name("run", available) == "Running"
    assert match_action_name("backhand", available) == "TennisBackhand"
    assert match_action_name("tennis", available) == ["TennisForehand", "TennisBackhand"]
    assert match_action_name("tenis forhand", available) == "TennisForehand"       # typos
    assert match_action_name("tenis forhand", available, fuzzy=False) is None
    assert match_action_name("swim", available) is None
    assert match_action_name("run", []) is None


# ---- read_pose_data angles ----------------------------------------------------------------------------------------

def test_angle_names(context):
    assert normalize_angle_names(["Right Knee", "left hip"]) == ["right knee", "left hip"]
    assert normalize_angle_names(["knee"]) == ["right knee", "left knee"]
    assert normalize_angle_names(["knees", "torso"]) == ["right knee", "left knee", "trunk"]
    assert normalize_angle_names("right elbow angle") == ["right elbow"]
    assert normalize_angle_names(["wing", 3, None, ""]) == []
    assert normalize_angle_names(None) == []
    assert set(normalize_angle_names(list(ANGLE_NAMES))) == set(ANGLE_NAMES)


def test_read_pose_data_angles_are_cleaned(context, catalog):
    action = {"type": "read_pose_data", "angles": ["Right Knee", "wing", "trunk"]}
    assert clean(action, context, catalog) == {"type": "read_pose_data", "angles": ["right knee", "trunk"]}
    assert clean({"type": "read_pose_data", "angles": "everything"}, context, catalog) == {"type": "read_pose_data"}
    assert clean({"type": "read_pose_data", "angles": []}, context, catalog) == {"type": "read_pose_data"}


def test_sanitize_reports_what_it_dropped(context, catalog):
    actions, dropped = sanitize([
        {"type": "set_layer", "layer": "muscles"},
        {"type": "show_part", "kind": "muscle", "name": "Latissimus dorsi", "side": ""},
        {"type": "self_destruct"},
    ], context, catalog)
    assert actions == [{"type": "set_layer", "layer": "muscles"}]
    assert len(dropped) == 2
    assert "not in the catalog" in dropped[0][1] and "unknown type" in dropped[1][1]
