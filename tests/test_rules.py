"""
The rules alone (no LLM, no HTTP): what the user says -> the actions.

The context is the PROTOCOL.md example: pose estimation running with three angles, actions
Idle / Running / Jogging / TennisForehand, three avatars, layer "body".
"""

from __future__ import annotations

import time

import pytest

from assistant import rules
from assistant.actions import sanitize


def layer(name):
    return {"type": "set_layer", "layer": name}


def tl(op, value=None):
    return {"type": "timeline", "op": op} if value is None else {"type": "timeline", "op": op, "value": value}


def cam(op, value=None, **more):
    action = {"type": "camera", "op": op}
    if value is not None:
        action["value"] = value
    action.update(more)
    return action


def part(kind, name, side=""):
    return {"type": "show_part", "kind": kind, "name": name, "side": side}


def clip(name):
    return {"type": "play_action", "name": name}


def panel(name):
    return {"type": "open_panel", "panel": name}


def avatar(direction):
    return {"type": "switch_avatar", "direction": direction}


POSE_ON = {"type": "pose_estimation", "enabled": True}
POSE_OFF = {"type": "pose_estimation", "enabled": False}
READ = {"type": "read_pose_data"}
CLEAR = {"type": "clear_selection"}

# (what the user says, the actions that must come back)
CASES = [
    # ---- the Examples table of PROTOCOL.md --------------------------------------------------
    ("switch to the muscle layer", [layer("muscles")]),
    ("show me the bones", [layer("skeleton")]),
    ("pause", [tl("pause")]),
    ("play it faster", [tl("faster"), tl("play")]),
    ("half speed", [tl("set_speed", 0.5)]),
    ("zoom in a lot", [cam("zoom_in", 3)]),
    ("show me from behind", [cam("view", "back")]),
    ("start the pose estimation and read the data", [POSE_ON, READ]),
    ("what is the right knee angle?", []),
    ("show me the left hamstring", [part("muscle", "Biceps femoris (long head)", "left")]),
    ("highlight the femur", [part("bone", "Femur")]),
    ("make her run", [clip("Running")]),

    # ---- the acceptance table of docs/HANDOFF.md --------------------------------------------
    ("show me the skeleton", [layer("skeleton")]),
    ("make it faster", [tl("faster")]),
    ("zoom out a lot", [cam("zoom_out", 3)]),
    ("look from behind", [cam("view", "back")]),
    ("reset the view", [cam("reset")]),
    ("activate realtime pose estimation and read the data", [POSE_ON, READ]),
    ("show me the left biceps", [part("muscle", "Biceps brachii", "left")]),
    ("show me the hamstring", [part("muscle", "Biceps femoris (long head)")]),

    # ---- layers --------------------------------------------------------------------------------
    ("muscles", [layer("muscles")]),
    ("show the muscular system", [layer("muscles")]),
    ("I want to see the muscles", [layer("muscles")]),
    ("turn on the muscle view", [layer("muscles")]),
    ("skeleton", [layer("skeleton")]),
    ("skeletal view please", [layer("skeleton")]),
    ("x-ray", [layer("skeleton")]),
    ("go to the bone layer", [layer("skeleton")]),
    ("let me see the skeleton please", [layer("skeleton")]),
    ("skin", [layer("body")]),
    ("back to the body", [layer("body")]),
    ("show the body layer", [layer("body")]),
    ("Switch to the skin layer.", [layer("body")]),

    # ---- timeline ------------------------------------------------------------------------------
    ("play", [tl("play")]),
    ("play the animation", [tl("play")]),
    ("resume", [tl("play")]),
    ("continue", [tl("play")]),
    ("start the animation", [tl("play")]),
    ("stop", [tl("pause")]),
    ("freeze", [tl("pause")]),
    ("pause the animation", [tl("pause")]),
    ("hold it", [tl("pause")]),
    ("please pause the animation right now", [tl("pause")]),
    ("toggle playback", [tl("toggle")]),
    ("restart", [tl("restart")]),
    ("from the beginning", [tl("restart")]),
    ("start over", [tl("restart")]),
    ("play from the beginning", [tl("restart"), tl("play")]),
    ("play it again", [tl("restart"), tl("play")]),
    ("faster", [tl("faster")]),
    ("speed up", [tl("faster")]),
    ("speed it up a bit", [tl("faster")]),
    ("a lot faster", [tl("faster", 2)]),
    ("speed up twice", [tl("faster", 2)]),
    ("um can you make it go faster please", [tl("faster")]),
    ("slower", [tl("slower")]),
    ("slow down", [tl("slower")]),
    ("slow it down a lot", [tl("slower", 2)]),
    ("play it slower", [tl("slower"), tl("play")]),
    ("too fast", [tl("slower")]),
    ("2x", [tl("set_speed", 2)]),
    ("play at 1.5x", [tl("set_speed", 1.5), tl("play")]),
    ("double speed", [tl("set_speed", 2)]),
    ("twice as fast", [tl("set_speed", 2)]),
    ("normal speed", [tl("set_speed", 1)]),
    ("back to normal speed", [tl("set_speed", 1)]),
    ("at half speed", [tl("set_speed", 0.5)]),
    ("slow motion", [tl("set_speed", 0.5)]),
    ("quarter speed", [tl("set_speed", 0.25)]),
    ("set the speed to 3", [tl("set_speed", 3)]),
    ("full speed", [tl("set_speed", 3)]),
    ("go to the middle", [tl("seek", 0.5)]),
    ("jump to the middle of the animation", [tl("seek", 0.5)]),
    ("go to 30 percent", [tl("seek", 0.3)]),
    ("skip to 75%", [tl("seek", 0.75)]),
    ("go to the start", [tl("seek", 0)]),
    ("go to the end", [tl("seek", 1)]),
    ("bookmark", [tl("bookmark")]),
    ("bookmark this", [tl("bookmark")]),
    ("add a bookmark here", [tl("bookmark")]),
    ("start recording", [tl("record_start")]),
    ("record this", [tl("record_start")]),
    ("stop recording", [tl("record_stop")]),
    ("stop the recording", [tl("record_stop")]),
    ("next marker", [tl("next_marker")]),
    ("go to the next bookmark", [tl("next_marker")]),
    ("step forward", [tl("step", 0.1)]),
    ("step back", [tl("step", -0.1)]),
    ("next frame", [tl("step", 0.033)]),
    ("previous frame", [tl("step", -0.033)]),
    ("go back 2 seconds", [tl("step", -2)]),
    ("forward one second", [tl("step", 1)]),

    # ---- camera --------------------------------------------------------------------------------
    ("zoom in", [cam("zoom_in")]),
    ("zoom in a bit", [cam("zoom_in")]),
    ("zoom in a little more", [cam("zoom_in")]),
    ("zoom in twice", [cam("zoom_in", 2)]),
    ("closer", [cam("zoom_in")]),
    ("come a lot closer", [cam("zoom_in", 3)]),
    ("give me a closer look", [cam("zoom_in")]),
    ("zoom all the way in", [cam("zoom_to", 1)]),
    ("zoom out", [cam("zoom_out")]),
    ("zoom out a bit", [cam("zoom_out")]),
    ("farther", [cam("zoom_out")]),
    ("move the camera back", [cam("zoom_out")]),
    ("zoom all the way out", [cam("zoom_to", 0)]),
    ("zoom to 50 percent", [cam("zoom_to", 0.5)]),
    ("rotate left", [cam("orbit", yaw=-45)]),
    ("turn right", [cam("orbit", yaw=45)]),
    ("orbit left", [cam("orbit", yaw=-45)]),
    ("rotate right 90 degrees", [cam("orbit", yaw=90)]),
    ("rotate 30 degrees to the left", [cam("orbit", yaw=-30)]),
    ("turn left a little", [cam("orbit", yaw=-15)]),
    ("rotate the camera to the right", [cam("orbit", yaw=45)]),
    ("turn around", [cam("orbit", yaw=180)]),
    ("tilt up", [cam("orbit", pitch=20)]),
    ("from behind", [cam("view", "back")]),
    ("from the back", [cam("view", "back")]),
    ("look at her from behind", [cam("view", "back")]),
    ("from the front", [cam("view", "front")]),
    ("front view", [cam("view", "front")]),
    ("from the left side", [cam("view", "left")]),
    ("show me the right side", [cam("view", "right")]),
    ("from above", [cam("view", "top")]),
    ("top view", [cam("view", "top")]),
    ("reset the camera", [cam("reset")]),
    ("reset view", [cam("reset")]),

    # ---- pose estimation ------------------------------------------------------------------------
    ("activate pose estimation", [POSE_ON]),
    ("start pose tracking", [POSE_ON]),
    ("enable pose detection", [POSE_ON]),
    ("turn on realtime pose estimation", [POSE_ON]),
    ("turn the pose estimation on", [POSE_ON]),
    ("activate the post estimation", [POSE_ON]),          # what Whisper makes of "pose estimation"
    ("stop the pose estimation", [POSE_OFF]),
    ("turn off pose tracking", [POSE_OFF]),
    ("disable pose estimation", [POSE_OFF]),
    ("hide the overlay", [{"type": "pose_estimation", "enabled": True, "show_panel": True, "overlay": False}]),
    ("show the overlay", [{"type": "pose_estimation", "enabled": True, "show_panel": True, "overlay": True}]),
    ("read the data", [READ]),
    ("what are the angles", [READ]),
    ("read out the joint angles", [READ]),
    ("what is the knee angle", []),

    # ---- parts ---------------------------------------------------------------------------------
    ("show me the right femur", [part("bone", "Femur", "right")]),
    ("where is the gastrocnemius", [part("muscle", "Gastrocnemius")]),
    ("point to the kneecap", [part("bone", "Patella (kneecap)")]),
    ("find the L3", [part("bone", "L3 vertebra", "center")]),
    ("select the third rib", [part("bone", "Rib 3 (true rib)")]),
    ("biceps", [part("muscle", "Biceps brachii")]),
    ("the left deltoid", [part("muscle", "Deltoid (middle)", "left")]),
    ("highlight the trapezius", [part("muscle", "Trapezius (upper)")]),
    ("show me the quads", [part("muscle", "Rectus femoris")]),
    ("show me the shin bone", [part("bone", "Tibia")]),
    ("show me the jaw", [part("bone", "Mandible (jaw)", "center")]),
    ("where is the ACL", [part("muscle", "Anterior cruciate ligament")]),
    ("show me the knee", [part("bone", "Patella (kneecap)")]),
    ("show me the shoulder", [part("muscle", "Deltoid (middle)")]),
    ("zoom in on the left biceps", [part("muscle", "Biceps brachii", "left")]),
    ("show me the adductor magnus and minimus", [part("muscle", "Adductor magnus & minimus")]),
    ("show me the left and right biceps", [part("muscle", "Biceps brachii")]),
    ("show me the lats", []),                               # not in this avatar's catalog
    ("show me the flux capacitor", []),                     # not a part at all
    ("clear the highlight", [CLEAR]),
    ("remove the highlight", [CLEAR]),
    ("deselect", [CLEAR]),

    # ---- action clips ---------------------------------------------------------------------------
    ("make her jog", [clip("Jogging")]),
    ("make the avatar run", [clip("Running")]),
    ("run", [clip("Running")]),
    ("start jogging", [clip("Jogging")]),
    ("tennis forehand", [clip("TennisForehand")]),
    ("do the tennis forehand", [clip("TennisForehand")]),
    ("play the running animation", [clip("Running")]),
    ("idle", [clip("Idle")]),
    ("stop running", [clip("Idle")]),
    ("make her jump", []),                                  # this avatar has no jump clip

    # ---- panels and avatar ----------------------------------------------------------------------
    ("open the graphs", [panel("graphs")]),
    ("show me the charts", [panel("graphs")]),
    ("close the panel", [panel("none")]),
    ("close it", [panel("none")]),
    ("show saved videos", [panel("saved_videos")]),
    ("open my recordings", [panel("saved_videos")]),
    ("settings", [panel("settings")]),
    ("open the settings panel", [panel("settings")]),
    ("open the actions panel", [panel("actions")]),
    ("show the timeline", [panel("timeline")]),
    ("open the pose estimation panel", [panel("pose_estimation")]),
    ("next avatar", [avatar("next")]),
    ("switch character", [avatar("next")]),
    ("change the avatar", [avatar("next")]),
    ("previous avatar", [avatar("previous")]),
    ("go back to the previous character", [avatar("previous")]),

    # ---- several commands in one sentence -------------------------------------------------------
    ("zoom in and rotate left", [cam("zoom_in"), cam("orbit", yaw=-45)]),
    ("show me the skeleton then zoom out a bit", [layer("skeleton"), cam("zoom_out")]),
    ("go to the middle and pause", [tl("seek", 0.5), tl("pause")]),
    ("switch to muscles, zoom in a lot and play", [layer("muscles"), cam("zoom_in", 3), tl("play")]),
    ("zoom in, then show me the right femur and pause",
     [cam("zoom_in"), part("bone", "Femur", "right"), tl("pause")]),
    ("pause and show me the left biceps", [tl("pause"), part("muscle", "Biceps brachii", "left")]),
    ("open the graphs and start recording", [panel("graphs"), tl("record_start")]),
    ("make her run and play it faster", [clip("Running"), tl("faster"), tl("play")]),

    # ---- speech-recognition style: no punctuation, filler words, wake word ---------------------
    ("hey bot can you please show me the left biceps", [part("muscle", "Biceps brachii", "left")]),
    ("okay so switch to the skeleton layer please", [layer("skeleton")]),
    ("uh could you zoom in a little bit", [cam("zoom_in")]),
    ("hey bot pause", [tl("pause")]),
    ("Okay. Show me the muscles.", [layer("muscles")]),
    ("can you please make her run now", [clip("Running")]),
    ("alright next avatar please", [avatar("next")]),
    ("i want you to rotate left forty five degrees", [cam("orbit", yaw=-45)]),
    ("go to thirty percent", [tl("seek", 0.3)]),
    ("okay now activate the real time pose estimation and read the data please", [POSE_ON, READ]),
    ("show me the bye steps", [part("muscle", "Biceps brachii")]),
    ("where is the gastrocnemious", [part("muscle", "Gastrocnemius")]),
    ("highlight the left soleous", [part("muscle", "Soleus", "left")]),
    ("show me the femer", [part("bone", "Femur")]),
    ("show me the humorous", [part("bone", "Humerus")]),
    ("can you show me the right hamstrings thanks", [part("muscle", "Biceps femoris (long head)", "right")]),
    ("buns.", [layer("skeleton")]),                          # Whisper base.en for a short "Bones."
    ("Where is the brachio AD ALICE?", [part("muscle", "Brachioradialis")]),     # ... for "brachioradialis"
    ("Show me the PSO S major.", [part("muscle", "Psoas major")]),               # ... for "psoas major"
    ("Show me the semi-dendenosis.", [part("muscle", "Semitendinosus")]),        # ... for "semitendinosus"
    ("Point to the Tibealis anterior.", [part("muscle", "Tibialis anterior")]),  # ... for "tibialis anterior"
    ("Cause", [tl("pause")]),                               # Whisper base.en for a short "Pause."
    ("Show me the lads.", []),                              # ... and for "lats" (not in this catalog)
]


def test_the_table_is_large_enough():
    assert len(CASES) >= 120
    assert len({text for text, _ in CASES}) == len(CASES), "a phrasing is listed twice"


@pytest.mark.parametrize("text, expected", CASES, ids=[text for text, _ in CASES])
def test_rules(text, expected, context, catalog):
    result = rules.parse(text, context, catalog)
    assert result is not None, "the rules did not understand this (it would go to the LLM)"
    assert not result.need_catalog
    assert result.actions == expected
    assert result.reply, "every answer needs a reply"
    # what the rules build is already valid protocol: validation changes nothing
    clean, dropped = sanitize(result.actions, context, catalog)
    assert clean == result.actions and not dropped


def test_every_action_type_is_covered():
    kinds = {action["type"] for _, actions in CASES for action in actions}
    assert kinds == {
        "set_layer", "timeline", "camera", "pose_estimation", "read_pose_data", "show_part", "clear_selection",
        "play_action", "open_panel", "switch_avatar",
    }
    timeline_ops = {a["op"] for _, actions in CASES for a in actions if a["type"] == "timeline"}
    assert timeline_ops == {
        "play", "pause", "toggle", "restart", "faster", "slower", "set_speed", "seek", "step", "next_marker",
        "bookmark", "record_start", "record_stop",
    }
    camera_ops = {a["op"] for _, actions in CASES for a in actions if a["type"] == "camera"}
    assert camera_ops == {"zoom_in", "zoom_out", "zoom_to", "orbit", "view", "reset"}


# ---- sentences the rules must leave to the LLM -------------------------------------------------

@pytest.mark.parametrize("text", [
    "what does the deltoid do?",
    "hello",
    "what can you do",
    "thanks",
    "why does my knee hurt when I run",
    "what is the capital of France",
    "show me how to stretch my hamstring",
    "zoom in and tell me a joke",                 # one piece is not understood -> the whole sentence
    "how do I prevent an ACL injury",
    "",
    "   ",
    "again",
    "back",
])
def test_left_to_the_llm(text, context, catalog):
    assert rules.parse(text, context, catalog) is None


# ---- replies -----------------------------------------------------------------------------------

def test_reply_for_a_known_part(context, catalog):
    assert rules.parse("show me the left biceps", context, catalog).reply == "Here is the left Biceps brachii."


def test_reply_names_the_part_picked_for_a_region(context, catalog):
    result = rules.parse("show me the knee", context, catalog)
    assert "Patella (kneecap)" in result.reply and "knee" in result.reply


def test_reply_for_a_part_that_is_not_in_the_catalog(context, catalog):
    result = rules.parse("show me the lats", context, catalog)
    assert result.actions == []
    assert "Latissimus dorsi" in result.reply
    assert "Teres major" in result.reply                   # offers parts that do exist


def test_reply_for_an_unknown_word_offers_names(context, catalog):
    result = rules.parse("highlight the fibia", context, catalog)
    assert result.actions == []
    assert "fibia" in result.reply
    assert "Tibia" in result.reply and "Fibula" in result.reply


def test_reply_for_an_action_the_avatar_does_not_have(context, catalog):
    result = rules.parse("make her jump", context, catalog)
    assert result.actions == []
    assert "Running" in result.reply and "jump" in result.reply


def test_reply_for_several_commands_is_joined(context, catalog):
    result = rules.parse("zoom in and pause", context, catalog)
    assert result.reply == "Zooming in. Paused."


# ---- questions answered from the context ----------------------------------------------------------

def test_knee_angle_from_context(context, catalog):
    result = rules.parse("what is the right knee angle?", context, catalog)
    assert result.actions == []
    assert result.reply == "The right knee is at 142 degrees."


def test_both_knees_when_no_side_is_said(context, catalog):
    result = rules.parse("what's the knee angle", context, catalog)
    assert result.actions == []
    assert "right knee is at 142" in result.reply and "left knee is at 95" in result.reply


def test_trunk_angle_with_other_words(context, catalog):
    result = rules.parse("hey bot how many degrees is the torso at", context, catalog)
    assert result.actions == [] and "trunk is at 84 degrees" in result.reply


def test_angle_that_is_not_visible(context, catalog):
    result = rules.parse("what is the left elbow angle", context, catalog)
    assert result.actions == []
    assert "cannot see the left elbow" in result.reply


def test_angle_question_with_pose_off_switches_it_on(context_pose_off, catalog):
    result = rules.parse("what is the right knee angle", context_pose_off, catalog)
    assert result.actions == [
        {"type": "pose_estimation", "enabled": True},
        {"type": "read_pose_data", "angles": ["right knee"]},
    ]


def test_angle_question_without_any_context(catalog):
    result = rules.parse("what is the knee angle", None, catalog)
    assert result.actions == [
        {"type": "pose_estimation", "enabled": True},
        {"type": "read_pose_data", "angles": ["right knee", "left knee"]},
    ]


def test_read_the_data_with_pose_off_switches_it_on(context_pose_off, catalog):
    assert rules.parse("read the data", context_pose_off, catalog).actions == [POSE_ON, READ]


def test_combined_command_does_not_switch_pose_on_twice(context_pose_off, catalog):
    result = rules.parse("activate pose estimation and read the data", context_pose_off, catalog)
    assert result.actions == [POSE_ON, READ]
    assert result.reply == "Starting the pose estimation and reading the joint angles."


def test_hide_overlay_when_pose_is_off(context_pose_off, catalog):
    result = rules.parse("hide the overlay", context_pose_off, catalog)
    assert result.actions == [] and "not running" in result.reply


@pytest.mark.parametrize("text, expected", [
    ("what layer am I on", "You are on the body layer."),
    ("which layer is this?", "You are on the body layer."),
    ("how fast is it playing", "It is playing at 1x speed."),
    ("what is the speed", "It is playing at 1x speed."),
    ("is it recording", "No, it is not recording."),
    ("is the pose estimation running", "Yes, the pose estimation is running."),
    ("is it playing", "It is playing."),
    ("what is selected", "The highlighted part is the left Biceps brachii (muscle)."),
    ("which avatar is this", "This is BP_Abby8, avatar 1 of 3."),
    ("what actions are there", "This avatar can do: Idle, Running, Jogging, TennisForehand."),
    ("which panel is open", "No panel is open."),
    ("did that work", "Yes, that worked."),
])
def test_questions_about_the_state(text, expected, context, catalog):
    result = rules.parse(text, context, catalog)
    assert result is not None and result.actions == []
    assert result.reply == expected


def test_state_questions_use_the_context(context, catalog):
    context["layer"] = "muscles"
    context["timeline"].update(playing=False, speed=0.5, recording=True)
    context["last_results"] = [{"type": "play_action", "ok": False, "message": "This avatar has no Jumping clip."}]
    assert rules.parse("what layer am i on", context, catalog).reply == "You are on the muscles layer."
    assert rules.parse("how fast is it playing", context, catalog).reply == "It is paused. The speed is set to 0.5x."
    assert rules.parse("is it recording", context, catalog).reply == "Yes, it is recording."
    assert rules.parse("did that work", context, catalog).reply == "No. This avatar has no Jumping clip."


# ---- context dependent commands --------------------------------------------------------------------

def test_relative_speed_uses_the_current_speed(context, catalog):
    context["timeline"]["speed"] = 1.5
    assert rules.parse("twice as fast", context, catalog).actions == [tl("set_speed", 3)]


def test_full_speed_uses_the_speed_steps(context, catalog):
    context["timeline"]["speed_steps"] = [0.5, 1, 2]
    assert rules.parse("full speed", context, catalog).actions == [tl("set_speed", 2)]


def test_the_other_side_flips_the_selected_part(context, catalog):
    assert rules.parse("the other side", context, catalog).actions == [part("muscle", "Biceps brachii", "right")]
    assert rules.parse("now the right one", context, catalog).actions == [part("muscle", "Biceps brachii", "right")]


def test_the_other_side_without_a_selection_turns_the_camera(context, catalog):
    del context["selected_part"]
    assert rules.parse("the other side", context, catalog).actions == [cam("orbit", yaw=180)]


def test_only_one_avatar(context, catalog):
    context["avatar"]["count"] = 1
    result = rules.parse("next avatar", context, catalog)
    assert result.actions == [] and "only one avatar" in result.reply


def test_layer_decides_between_muscle_and_bone_for_a_region(context, catalog):
    assert rules.parse("show me the shoulder", context, catalog).actions[0]["name"] == "Deltoid (middle)"
    context["layer"] = "skeleton"
    assert rules.parse("show me the shoulder", context, catalog).actions[0]["name"] == "Scapula (shoulder blade)"


def test_action_names_come_from_the_context(context, catalog):
    context["action"]["available"] = ["Idle", "Jumping", "TennisForehand", "TennisBackhand"]
    assert rules.parse("make her jump", context, catalog).actions == [clip("Jumping")]
    assert rules.parse("backhand", context, catalog).actions == [clip("TennisBackhand")]
    ambiguous = rules.parse("play tennis", context, catalog)
    assert ambiguous.actions == [] and "TennisForehand" in ambiguous.reply and "TennisBackhand" in ambiguous.reply
    missing = rules.parse("make her run", context, catalog)
    assert missing.actions == [] and "Jumping" in missing.reply


# ---- robustness --------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad_context", [
    None, {}, [], "nonsense", 42,
    {"timeline": None, "pose": "off", "action": [], "avatar": 3, "layer": 7, "selected_part": "x"},
    {"timeline": {"speed": "fast", "speed_steps": ["a", None]}, "pose": {"angles": ["x"], "status": 1},
     "action": {"available": [1, None, "Running"]}, "last_results": [1, {"type": 2}]},
])
def test_no_crash_without_context(bad_context, catalog):
    for text, _ in CASES:
        rules.parse(text, bad_context, catalog)          # must not raise
    for text in ("what layer am i on", "how fast is it playing", "is it recording", "what is the knee angle",
                 "which avatar is this", "did that work", "what is selected", "the other side"):
        result = rules.parse(text, bad_context, catalog)
        assert result is not None and isinstance(result.reply, str)


def test_commands_that_need_no_catalog_work_without_one(context):
    assert rules.parse("pause", context, None).actions == [tl("pause")]
    assert rules.parse("switch to the muscle layer", context, None).actions == [layer("muscles")]
    assert rules.parse("make her run", context, None).actions == [clip("Running")]


def test_part_request_without_a_catalog_asks_for_it(context):
    for text in ("show me the left biceps", "highlight the femur", "gastrocnemius", "where is the kneecap"):
        result = rules.parse(text, context, None)
        assert result is not None and result.need_catalog, text
        assert result.actions == []


@pytest.mark.parametrize("text", [
    "pose estimation " * 120 + "x",
    "show me the femur " + "is located " * 150 + "z",
    "read " + "pose joint body current all main " * 60 + "nothing",
    "play " * 400,
    "show me the " + "gastrocnemiusx " * 130,
    "highlight " + "z" * 1900,
    "a and b and c and " * 110,
    "saved recorded old previous all video " * 50 + "videosx",
    "what " + "layer " * 300,
    "(" * 2000,
    "x" * 2000,
])
def test_hostile_text_is_answered_quickly(text, context, catalog):
    """The server is reachable from outside: no sentence may keep the rules busy for long."""
    started = time.perf_counter()
    rules.parse(text[:2000], context, catalog)
    assert time.perf_counter() - started < 0.5


def test_rules_are_fast(context, catalog):
    started = time.perf_counter()
    for text, _ in CASES:
        rules.parse(text, context, catalog)
    per_sentence_ms = (time.perf_counter() - started) * 1000 / len(CASES)
    assert per_sentence_ms < 20, f"{per_sentence_ms:.1f} ms per sentence"
