"""
The rule-based parser: text + context + catalog -> actions and a reply, in milliseconds, with no LLM.

    result = parse("play it faster", context, catalog)
    result.actions  -> [{"type": "timeline", "op": "faster"}, {"type": "timeline", "op": "play"}]
    result.reply    -> "Playing faster."

`parse` returns None when the rules do not understand the sentence; the pipeline then asks the LLM.

How a sentence is read:
  1. `normalize` (text.py): lower case, no punctuation, digits for spoken numbers.
  2. The sentence is cut at "and" / "then" / "," into pieces. Every piece must be understood,
     otherwise the whole sentence is tried as one command, and if that fails too the answer is None.
  3. Each piece is reduced to its *core*: politeness, the wake word, articles and pronouns are removed
     ("hey bot can you please show me the left biceps" -> "show left biceps").
  4. The RULES are tried in order on the core. Almost all patterns must match the whole core
     (re.fullmatch), so a sentence that only looks a bit like a command goes to the LLM instead of
     doing the wrong thing.

To add a command: write a pattern for the core form in the matching `_rule_...` function (or add a
new function to RULES) and add its phrasings to tests/test_rules.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import actions as A
from .catalog import Catalog, NoMatch, PartMatch, mentions_anatomy
from .context import Context
from .text import join_names, normalize, tidy_number


@dataclass
class Result:
    actions: list[dict] = field(default_factory=list)
    reply: str = ""
    need_catalog: bool = False


# ---------------------------------------------------------------------------
# From sentence to core
# ---------------------------------------------------------------------------

_SPLIT_RE = re.compile(r"\s*(?:,|\band then\b|\bafter that\b|\bfollowed by\b|\bthen\b|\band also\b|\band\b)\s*")

_FILLERS = [
    "hey bot", "hi bot", "hello bot", "ok bot", "okay bot", "hey assistant", "hey there", "excuse me",
    "can you", "could you", "would you", "will you", "can we", "could we", "can i", "could i", "may i",
    "shall we", "i want you to", "i would like you to", "id like you to", "i need you to", "i want to",
    "i would like to", "id like to", "i wanna", "i need to", "i want", "i would like", "id like", "i need",
    "lets", "let us", "let me", "go ahead", "if you can", "if possible", "please", "pls", "kindly", "just",
    "right now", "now", "okay", "ok", "alright", "all right", "um", "umm", "uh", "uhh", "er", "hmm",
    "actually", "maybe", "for me", "for us", "thank you", "thanks", "quickly", "real quick", "bot", "assistant",
]
_FILLER_RE = re.compile(r"\b(?:" + "|".join(sorted(_FILLERS, key=len, reverse=True)) + r")\b")

_DROP = {
    "the", "a", "an", "it", "its", "this", "that", "these", "those", "me", "her", "him", "his", "she", "he",
    "them", "their", "my", "our", "your", "us", "some", "there", "here",
}
_LEADING = {
    "then", "and", "also", "finally", "so", "well", "yes", "yeah", "yep", "yup", "sure", "great", "nice",
    "cool", "perfect", "good", "awesome", "hello", "hi", "hey", "oh", "ah", "no", "nope", "and",
}


def make_core(piece: str) -> str:
    piece = re.sub(r"\bi ?t (band|tract)\b", r"itb \1", piece)       # "IT band": this "it" is not a pronoun
    words = [word for word in _FILLER_RE.sub(" ", piece).split() if word not in _DROP]
    while words and words[0] in _LEADING:
        words.pop(0)
    return " ".join(words)


def _full(pattern: str, text: str) -> re.Match | None:
    return re.fullmatch(pattern, text)


# Amount words: "a bit", "a lot", "twice", "all the way". (Articles are already gone in the core.)
_AMOUNTS = [
    ("max", r"all way|as (?:much|far|close) as (?:possible|you can|it goes)|completely|fully|to max|to maximum"
            r"|maximum|max"),
    ("count", r"(\d+) (?:times?|steps?|notch|notches|levels?|clicks?|x)|twice|once|thrice|couple of times"
              r"|couple times|few times"),
    ("big", r"whole lot|lot more|lots more|much more|way more|lot|lots|much|way|far|really|very|significantly"
            r"|considerably|big"),
    ("small", r"little bit more|tiny bit more|little bit|tiny bit|wee bit|bit more|little more|bit|little|tad"
              r"|touch|slightly|somewhat|tiny|small"),
    ("more", r"some more|even more|more|again"),
]
_COUNT_WORDS = {"twice": 2, "once": 1, "thrice": 3, "couple of times": 2, "couple times": 2, "few times": 3}


def take_amount(core: str) -> tuple[str, int, str]:
    """'zoom in lot' -> ('big', 0, 'zoom in'); 'faster 2 times' -> ('count', 2, 'faster'); else ('', 0, core)."""
    kind, count, rest = "", 0, core
    for name, pattern in _AMOUNTS:
        match = re.search(rf"\b(?:{pattern})\b", rest)
        while match:
            if not kind:
                kind = name
                if name == "count":
                    count = int(match.group(1)) if match.group(1) else _COUNT_WORDS[match.group(0)]
            rest = (rest[:match.start()] + " " + rest[match.end():]).strip()
            match = re.search(rf"\b(?:{pattern})\b", rest)
    return kind, count, re.sub(r"\s+", " ", rest)


_NUM = r"(\d+(?:\.\d+)?)"


# ---------------------------------------------------------------------------
# Highlight
# ---------------------------------------------------------------------------

_CLEAR_RE = (
    r"(?:clear|remove|hide|delete|cancel|reset|drop|stop|turn off|switch off|get rid of|lose|undo|no|no more)"
    r"(?: (?:highlight|highlighting|highlights|selection|selected|selected part|highlighted part))"
    r"|clear|clear all|clear everything|deselect(?: all| everything)?|unselect(?: all| everything)?"
    r"|unhighlight(?: all| everything)?|select nothing|highlight nothing|stop highlighting|nothing selected"
)


def _rule_clear(core: str, piece: str, ctx: Context, catalog, joined: bool):
    if _full(_CLEAR_RE, core):
        return Result([A.clear_selection()], "Highlight removed.")
    return None


# ---------------------------------------------------------------------------
# Questions that the context answers
# ---------------------------------------------------------------------------

def _speed_text(speed: float) -> str:
    return f"{tidy_number(speed, 2)}x"


def _rule_question(core: str, piece: str, ctx: Context, catalog, joined: bool):
    if joined:
        return None
    text = piece

    if re.search(r"\b(?:what|which)\b.*\blayer\b|\bcurrent layer\b", text):
        if ctx.layer:
            return Result([], f"You are on the {ctx.layer} layer.")
        return Result([], "I cannot see which layer is showing right now.")

    if re.search(r"\bhow fast\b|\b(?:what|which|whats)\b.*\bspeed\b|\bcurrent speed\b", text):
        if ctx.speed is None:
            return Result([], "I cannot see the playback speed right now.")
        if ctx.playing is False:
            return Result([], f"It is paused. The speed is set to {_speed_text(ctx.speed)}.")
        return Result([], f"It is playing at {_speed_text(ctx.speed)} speed.")

    if re.search(r"^(?:is|are|am)\b.*\brecording\b", text):
        if ctx.recording is None and ctx.pose_recording is None:
            return Result([], "I cannot see whether it is recording right now.")
        if ctx.recording or ctx.pose_recording:
            return Result([], "Yes, it is recording.")
        return Result([], "No, it is not recording.")

    if re.search(r"^(?:is|are)\b.*\b(?:pose|tracking|estimation|sports ?2d)\b", text):
        if not ctx.pose_status:
            return Result([], "I cannot see the state of the pose estimation right now.")
        if ctx.pose_status == "running":
            return Result([], "Yes, the pose estimation is running.")
        if ctx.pose_status == "loading":
            return Result([], "The pose estimation is starting.")
        if ctx.pose_status == "error":
            return Result([], "The pose estimation reported an error.")
        return Result([], "No, the pose estimation is off.")

    if re.search(r"^(?:is|are)\b.*\b(?:playing|paused|moving|stopped)\b", text):
        if ctx.playing is None:
            return Result([], "I cannot see whether it is playing right now.")
        return Result([], "It is playing." if ctx.playing else "It is paused.")

    if re.search(
        r"\b(?:what|which|whats)\b.*\b(?:selected|highlighted)\b|\bwhat am i looking at\b"
        r"|^(?:what|which) (?:muscle|bone|part) is (?:this|that)$|^(?:what is|whats) (?:this|that)$", text,
    ):
        part = ctx.selected_part
        if not part:
            return Result([], "Nothing is highlighted right now.")
        side = f"{part['side']} " if part["side"] in ("left", "right") else ""
        kind = f" ({part['kind']})" if part["kind"] else ""
        return Result([], f"The highlighted part is the {side}{part['name']}{kind}.")

    if re.search(r"\b(?:which|what) (?:avatar|character|model)\b|\bhow many (?:avatars|characters|models)\b", text):
        if not ctx.avatar_name and ctx.avatar_count is None:
            return Result([], "I cannot see which avatar is showing right now.")
        name = ctx.avatar_name or "the current avatar"
        if ctx.avatar_count and ctx.avatar_index is not None:
            return Result([], f"This is {name}, avatar {ctx.avatar_index + 1} of {ctx.avatar_count}.")
        return Result([], f"This is {name}.")

    if re.search(
        r"\b(?:what|which) (?:actions|animations|clips|moves)\b|\blist (?:the )?(?:actions|animations)\b"
        r"|\bwhat can (?:she|he|it|the avatar) do\b|\bavailable (?:actions|animations)\b", text,
    ):
        names = ctx.available_actions
        if not names:
            return Result([], "This avatar has no action clips right now.")
        return Result([], f"This avatar can do: {', '.join(names[:20])}.")

    if re.search(
        r"\b(?:what|which) (?:action|animation|clip) is\b|\bwhat is (?:she|he|the avatar) doing\b"
        r"|\bcurrent (?:action|animation)\b", text,
    ):
        playing = ctx.action_playing
        if playing is None:
            return Result([], "I cannot see which action is playing right now.")
        if not playing:
            return Result([], "The avatar is playing its own animation, no action clip.")
        return Result([], f"The avatar is playing the {playing} action.")

    if re.search(r"\b(?:which|what) panel\b", text):
        if not ctx.panel:
            return Result([], "I cannot see which panel is open right now.")
        if ctx.panel == "none":
            return Result([], "No panel is open.")
        return Result([], f"The {ctx.panel.replace('_', ' ')} panel is open.")

    if re.search(r"^did (?:that|it|this) (?:work|succeed|fail|happen)\b|^was that ok\b", text):
        results = ctx.last_results
        if not results:
            return Result([], "I have no result from the last command.")
        failed = [result for result in results if not result["ok"]]
        if not failed:
            return Result([], "Yes, that worked.")
        reason = failed[0]["message"] or f"{failed[0]['type']} failed"
        return Result([], f"No. {reason}")
    return None


# ---------------------------------------------------------------------------
# Pose estimation
# ---------------------------------------------------------------------------

# "post estimation" / "pause estimation" is what speech recognition makes of "pose estimation".
_POSE = (
    r"(?:(?:real ?time|live|2d|3d) )?"
    r"(?:(?:pose|poses|posed|motion|body|movement|human|skeleton) "
    r"(?:estimation|estimations|estimate|estimator|tracking|tracker|detection|detector|analysis|capture"
    r"|recognition)"
    r"|(?:post|pause|posts|polls|pulse|pose a|pose and) (?:estimation|estimations)"
    r"|estimation|estimations|tracking|tracker|detection|sports ?2d|mocap)"
)
_POSE_ON = (
    r"(?:activate|start|enable|turn on|switch on|launch|run|begin|open|use|do|show|initiate|fire up|bring up"
    r"|engage|go to|start up|set up)"
)
_POSE_OFF = (
    r"(?:deactivate|stop|disable|turn off|switch off|close|end|kill|quit|exit|shut down|shut off|cancel"
    r"|terminate|finish|no|no more)"
)
_OVERLAY = (
    r"(?:(?:pose|skeleton|2d|sports ?2d|tracking|pose estimation) )?"
    r"(?:overlay|stick figure|stickman|stick man|keypoints|key points|landmarks)"
)
_READ = (
    r"(?:(?:read|read out|read back|tell|give|show|display|report|say|print|write|get|list|check|see|what are"
    r"|what is|whats|output|announce) )?"
    r"(?:(?:pose|joint|body|current|all|main|tracking|estimation|measured|live|of) )*"
    r"(?:data|angles|angle|values|numbers|measurements|readings|results)(?: out| aloud)?"
    r"|read(?: out| aloud)?"
)
_ANGLE_WORD_RE = re.compile(r"\b(?:angles?|degrees?|bent|bending|flexion|flexed)\b")
_JOINT_RE = re.compile(
    r"\b(?:(left|right|both) )?(ankles?|knees?|hips?|shoulders?|elbows?|wrists?|foot|feet|shanks?|shins?"
    r"|lower legs?|thighs?|upper arms?|forearms?|arms?|pelvis|trunk|torso|back|spine|head|neck)\b"
)


# A question about what an angle means, not what it is. The language model sees the angles too.
_JUDGEMENT_RE = re.compile(
    r"\b(?:normal|good|bad|ok|okay|fine|healthy|safe|dangerous|correct|wrong|ideal|optimal|enough|problem"
    r"|risky?|injur\w*|should|mean|means|why|improve|better|worse|too (?:much|little|high|low|big|small|far)"
    r"|compared?)\b"
)


def _asked_angles(piece: str, ctx: Context) -> list[str]:
    names: list[str] = []
    for side, joint in _JOINT_RE.findall(piece):
        joint = "foot" if joint == "feet" else joint
        side = "" if side == "both" else side
        for name in A.normalize_angle_names([f"{side} {joint}".strip()], ctx):
            if name not in names:
                names.append(name)
    for name in ctx.pose_angles:
        if re.search(rf"\b{re.escape(name)}\b", piece) and name not in names:
            names.append(name)
    return names


def _angle_sentence(values: dict[str, float]) -> str:
    parts = [f"the {name} is at {round(value)} degrees" for name, value in values.items()]
    sentence = join_names(parts)
    return sentence[0].upper() + sentence[1:] + "."


def _rule_pose_angle(core: str, piece: str, ctx: Context, catalog, joined: bool):
    """'what is the right knee angle': answered from the context when the number is there."""
    if not _ANGLE_WORD_RE.search(piece):
        return None
    asked = _asked_angles(piece, ctx)
    if not asked:
        return None
    label = join_names([f"{name} angle" for name in asked]) if len(asked) <= 2 else "those angles"
    if not ctx.pose_running:
        if ctx.pose_status == "loading":
            return Result([A.read_pose_data(asked)], f"The pose estimation is starting. Reading the {label}.")
        return Result(
            [A.pose_estimation(True), A.read_pose_data(asked)],
            f"Starting the pose estimation to read the {label}.",
        )
    known = ctx.pose_angles
    have = {name: known[name] for name in asked if name in known}
    missing = [name for name in asked if name not in known]
    if have and _JUDGEMENT_RE.search(piece):
        return None        # "is my knee angle normal for running": the number alone is no answer -> LLM
    if have and not missing:
        return Result([], _angle_sentence(have))
    if have:
        # "knee angle" with one knee visible: report it; say which one is not visible
        return Result([], f"{_angle_sentence(have)} I cannot see the {join_names(missing)} right now.")
    if known:
        return Result([], f"I cannot see the {join_names(missing)} right now. I can see: {', '.join(known)}.")
    return Result([A.read_pose_data(asked)], f"Reading the {label}.")


def _rule_pose(core: str, piece: str, ctx: Context, catalog, joined: bool):
    show = (
        rf"(?:show|display|turn on|switch on|enable|bring back|unhide|draw|see|activate) {_OVERLAY}"
        rf"|{_OVERLAY} on|(?:turn|switch) {_OVERLAY} on"
    )
    hide = (
        rf"(?:hide|remove|turn off|switch off|disable|clear|no|get rid of|lose|stop showing|stop drawing|kill"
        rf"|deactivate|without) {_OVERLAY}|{_OVERLAY} off|(?:turn|switch) {_OVERLAY} off"
    )
    if _full(show, core):
        return Result([A.pose_estimation(True, ctx.pose_panel_open, True)], "Showing the pose overlay.")
    if _full(hide, core):
        if ctx.has_pose and not (ctx.pose_running or ctx.pose_status == "loading"):
            return Result([], "The pose estimation is not running, so there is no overlay to hide.")
        return Result([A.pose_estimation(True, ctx.pose_panel_open, False)], "Hiding the pose overlay.")

    if _full(rf"{_POSE_OFF} {_POSE}|{_POSE} off|(?:turn|switch) {_POSE} off|stop tracking(?: i)?", core):
        return Result([A.pose_estimation(False)], "Stopping the pose estimation.")
    if _full(
        rf"(?:{_POSE_ON} )?{_POSE}(?: on)?|(?:turn|switch) {_POSE} on"
        rf"|track(?: (?:pose|movement|motion|body|i|myself))?|(?:analy[sz]e|estimate|detect) (?:pose|movement"
        rf"|motion|posture|form)", core,
    ):
        return Result([A.pose_estimation(True)], "Starting the pose estimation.")

    if _full(_READ, core):
        if ctx.pose_running or ctx.pose_status == "loading":
            return Result([A.read_pose_data()], "Reading the joint angles.")
        return Result(
            [A.pose_estimation(True), A.read_pose_data()],
            "Starting the pose estimation and reading the joint angles.",
        )
    return None


# ---------------------------------------------------------------------------
# Panels, avatar, layers
# ---------------------------------------------------------------------------

_OPEN = (
    r"(?:open|open up|show|display|bring up|pull up|go to|see|view|switch to|launch|give|take to|look at"
    r"|toggle)"
)
_PANEL = r"(?: (?:panel|tab|window|menu|page|screen|view|list|section|sidebar|side bar))?"
_PANEL_WORD = r"(?: (?:panel|tab|window|menu|page|screen|list|section|sidebar|side bar))"
_PANEL_RULES = [
    ("graphs", rf"(?:{_OPEN} )?(?:graph|graphs|chart|charts|plot|plots|curves){_PANEL}"),
    ("saved_videos", rf"(?:{_OPEN} )?(?:(?:saved|recorded|old|previous|all|video) )*"
                     rf"(?:videos|recordings|video list|gallery|video gallery|video library){_PANEL}"),
    ("settings", rf"(?:{_OPEN} )?(?:settings|setting|options|preferences|configuration|config){_PANEL}"),
    ("actions", rf"(?:{_OPEN} )?(?:actions|animations){_PANEL}|(?:{_OPEN} )?(?:action|animation){_PANEL_WORD}"),
    ("timeline", rf"(?:{_OPEN} )?timeline{_PANEL}"),
    ("pose_estimation", rf"(?:{_OPEN} )?(?:{_POSE}|pose){_PANEL_WORD}"),
    ("none", r"(?:close|hide|dismiss|exit|collapse|shut|remove|get rid of)"
             r"(?: (?:all|current|open|side))?"
             r"(?: (?:panel|panels|tab|tabs|window|windows|menu|sidebar|side bar|everything|graph|graphs|charts"
             r"|settings|videos|saved videos|recordings|actions|timeline|options))"
             r"|close|close all|dismiss|no panel"),
]
_PANEL_NAMES = {
    "graphs": "graphs", "saved_videos": "saved videos", "settings": "settings", "actions": "actions",
    "timeline": "timeline", "pose_estimation": "pose estimation",
}


def _rule_panel(core: str, piece: str, ctx: Context, catalog, joined: bool):
    for panel, pattern in _PANEL_RULES:
        if _full(pattern, core):
            if panel == "none":
                return Result([A.open_panel("none")], "Closing the panel.")
            return Result([A.open_panel(panel)], f"Opening the {_PANEL_NAMES[panel]} panel.")
    return None


_AVATAR = r"(?:avatar|avatars|character|characters|model|models|person|figure)"
_SWITCH = r"(?:switch|change|swap|go|skip|move|cycle|show|load|bring up|use|get|select|pick|try|give|see)"


def _rule_avatar(core: str, piece: str, ctx: Context, catalog, joined: bool):
    direction = ""
    if _full(
        rf"(?:{_SWITCH} )?(?:back )?(?:to )?(?:previous|last|prior|earlier|former) {_AVATAR}"
        rf"|(?:go )?back to {_AVATAR}|{_AVATAR} (?:back|before)", core,
    ):
        direction = "previous"
    elif _full(
        rf"(?:{_SWITCH} )?(?:to )?(?:next|another|other|different|new|following) {_AVATAR}"
        rf"|(?:switch|change|swap|cycle) {_AVATAR}|{_AVATAR} next", core,
    ):
        direction = "next"
    if not direction:
        return None
    if ctx.avatar_count is not None and ctx.avatar_count <= 1:
        return Result([], "There is only one avatar.")
    return Result([A.switch_avatar(direction)], f"Switching to the {direction} avatar.")


_LAYER_VERB = (
    r"(?:switch|change|go|show|display|see|view|set|turn on|enable|select|put on|bring up|back|go back"
    r"|switch back|return|use|activate|toggle|look at|give|reveal)"
)
_LAYER_WORDS = [
    ("body", r"body|skin|skin layer|surface|outside|outer layer|body layer"),
    ("muscles", r"muscle|muscles|muscular|musculature|muscle system|muscular system|all muscles"),
    # "buns" / "bonds": what Whisper base.en made of a short "Bones."
    ("skeleton", r"skeleton|skeletal|bones|bone|x ray|xray|skeletal system|all bones|skeleton system|buns|bonds"),
]
_LAYER_NAMES = {"body": "body", "muscles": "muscle", "skeleton": "skeleton"}


def _rule_layer(core: str, piece: str, ctx: Context, catalog, joined: bool):
    for layer, words in _LAYER_WORDS:
        if _full(rf"(?:{_LAYER_VERB} )?(?:to |on |in |into )?(?:{words})(?: (?:layer|view|mode|only))?", core):
            return Result([A.set_layer(layer)], f"Showing the {_LAYER_NAMES[layer]} layer.")
    return None


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

_CLIP = r"(?:animation|playback|timeline|motion|movement|clip|video|cycle)"
_SEEK_VERB = (
    r"(?:go|jump|skip|seek|move|scrub|fast forward|forward|rewind|back|go back|take|bring|set|put|start"
    r"|play|scroll|slide)"
)
_SPEED_LEFTOVER = {
    "play", "playing", "go", "run", "set", "put", "make", "change", "switch", "at", "to", "in", "on", "of",
    "speed", "playback", "animation", "rate", "is", "be", "as", "fast", "with", "back", "time", "times",
    "video", "motion", "mode", "tempo", "pace", "use", "do", "show", "watch", "see", "slow", "down", "up",
}
_SPEEDS = [
    ("relative", 2.0, r"(?:twice|double|2 times|2 x) as fast"),
    ("relative", 0.5, r"half as fast|twice as slow|2 times slower"),
    ("relative", None, rf"{_NUM} times (?:as fast|faster)"),
    ("absolute", 0.25, r"quarter(?: of)? speed|25 percent speed|super slow motion|very slow motion"
                       r"|super slow mo|super slow"),
    ("minimum", None, r"slowest(?: speed)?|minimum speed|min speed|lowest speed|as slow as (?:possible|you can)"),
    ("absolute", 0.5, r"half(?: of)? speed|half time|slow motion|slow mo|slomo|slowmo|speed (?:to |at )?half"),
    ("absolute", 1.0, r"(?:normal|regular|original|default|real|usual|standard|real ?time) "
                      r"(?:speed|pace|playback|tempo|rate)|speed (?:back )?(?:to )?normal|reset speed|speed reset"),
    ("maximum", None, r"(?:full|max|maximum|top|highest) speed|fastest(?: speed)?"
                      r"|as fast as (?:possible|you can)"),
    ("absolute", 2.0, r"double(?: speed| time)?|twice speed"),
    ("absolute", 3.0, r"triple(?: speed| time)?"),
    ("number", None, rf"{_NUM} x(?: speed)?|{_NUM} times(?: speed)?|speed (?:to |at |of |is )?{_NUM}(?: x| times)?"),
    ("percent", None, rf"{_NUM} percent(?: of)? speed|speed (?:to |at |of )?{_NUM} percent"),
]
_FASTER = (
    r"(?:(?:play|playing|go|run|make|move|do|be|animation|playback) )*"
    r"(?:faster|quicker|speed up|speedup|(?:increase|raise) speed|too slow|accelerate|hurry up|hurry"
    r"|pick up pace)"
    rf"(?: {_CLIP})?"
)
_SLOWER = (
    r"(?:(?:play|playing|go|run|make|move|do|be|animation|playback) )*"
    r"(?:slower|slow down|slow|slowly|(?:decrease|reduce|lower|drop) speed|too fast|ease up)"
    rf"(?: {_CLIP})?"
)
_POSITIONS = [
    (0.0, r"start|beginning|very start|very beginning|first frame|0 percent|0"),
    (1.0, r"end|very end|last frame|finish|100 percent"),
    (0.5, r"middle|halfway|half way|halfway point|half way point|midpoint|mid point|half|mid"),
    (0.25, r"quarter|1 quarter|first quarter|quarter way"),
    (0.75, r"3 quarters|three quarters|3 quarter"),
    (1 / 3, r"third|1 third"),
    (2 / 3, r"2 thirds"),
]
# What speech recognition makes of a short "Pause." Only when it is the whole sentence.
_PAUSE_MISHEARD = {"cause", "paws", "pours", "paused", "pauze", "paus"}
STEP_SECONDS = 0.1            # "step forward"
FRAME_SECONDS = 1 / 30        # "next frame"


def _rule_speed(core: str, ctx: Context):
    for kind, value, pattern in _SPEEDS:
        match = re.search(rf"\b(?:{pattern})\b", core)
        if not match:
            continue
        leftover = set((core[:match.start()] + " " + core[match.end():]).split())
        if not leftover <= _SPEED_LEFTOVER:
            continue
        number = next((float(group) for group in match.groups() if group), None)
        steps = ctx.speed_steps
        if kind == "relative":
            speed = (ctx.speed or 1.0) * (value if value is not None else number or 1.0)
        elif kind == "minimum":
            speed = steps[0] if steps else 0.25
        elif kind == "maximum":
            speed = steps[-1] if steps else 3.0
        elif kind == "number":
            speed = number or 1.0
        elif kind == "percent":
            speed = (number or 100.0) / 100.0
        else:
            speed = value or 1.0
        if speed <= 0:
            return None
        acts = [A.timeline("set_speed", speed)]
        if "play" in leftover or "playing" in leftover:
            acts.append(A.timeline("play"))
        reply = "Back to normal speed." if speed == 1 else f"Speed set to {_speed_text(speed)}."
        return Result(acts, reply)

    amount, count, rest = take_amount(core)
    for op, pattern in (("faster", _FASTER), ("slower", _SLOWER)):
        if not _full(pattern, rest):
            continue
        if amount == "max":
            steps = ctx.speed_steps
            speed = (steps[-1] if steps else 3.0) if op == "faster" else (steps[0] if steps else 0.25)
            acts = [A.timeline("set_speed", speed)]
        else:
            jumps = {"count": count, "big": 2}.get(amount, 1)
            acts = [A.timeline(op, jumps if jumps > 1 else None)]
        if rest.startswith("play"):
            acts.append(A.timeline("play"))
        return Result(acts, f"Playing {op}.")
    return None


def _rule_timeline(core: str, piece: str, ctx: Context, catalog, joined: bool):
    # recording, bookmarks, markers
    if _full(
        r"(?:stop|end|finish|save|quit|cancel|halt|done) (?:recording|record|video|video recording)"
        r"|(?:recording|record) (?:stop|off|done)|stop record", core,
    ):
        return Result([A.timeline("record_stop")], "Recording stopped and saved.")
    if _full(
        r"(?:(?:start|begin|make|take|do|capture|create) )?(?:(?:video|new|screen) )?(?:recording|record)"
        r"(?: (?:video|animation|clip|screen|on))?|(?:recording|record) (?:start|on)"
        r"|(?:record|capture|film) (?:video|clip)", core,
    ):
        return Result([A.timeline("record_start")], "Recording started.")
    if _full(
        r"(?:(?:add|drop|set|place|put|make|create|save|leave|insert) )?(?:bookmark|marker|mark)"
        r"(?: (?:here|now|at current time|frame|moment|position|spot|point))?"
        r"|(?:bookmark|mark|save|remember) (?:frame|moment|position|spot|point|time|here)", core,
    ):
        return Result([A.timeline("bookmark")], "Bookmark added.")
    if _full(
        r"(?:(?:go|jump|skip|move|take) )?(?:to )?next (?:marker|bookmark|mark|recording)", core,
    ):
        return Result([A.timeline("next_marker")], "Jumping to the next marker.")

    # seek: "go to the middle", "jump to 30 percent"
    for value, words in _POSITIONS:
        if _full(
            rf"{_SEEK_VERB}(?: (?:to|at|from))? (?:{words})(?: (?:of|in) {_CLIP}| point| mark)?"
            rf"|to (?:{words})(?: (?:of|in) {_CLIP})?|(?:{words}) (?:of|in) {_CLIP}", core,
        ):
            if value == 0.0 and re.match(r"(?:play|start) ", core):
                return Result([A.timeline("restart"), A.timeline("play")], "Playing from the start.")
            where = {0.0: "the start", 1.0: "the end", 0.5: "the middle"}.get(value, f"{round(value * 100)} percent")
            return Result([A.timeline("seek", value)], f"Jumping to {where}.")
    match = _full(
        rf"(?:{_SEEK_VERB} )?(?:to |at )?{_NUM} percent(?: (?:of|in|into|through) {_CLIP}| mark| point)?", core,
    )
    if match and (core.split()[0] not in (match.group(1),) or " of " in core or " in " in core):
        value = min(float(match.group(1)), 100.0) / 100.0
        return Result([A.timeline("seek", value)], f"Jumping to {tidy_number(value * 100, 1)} percent.")

    # step: "step forward", "next frame", "back 2 seconds"
    unit = r"(seconds?|secs?|s|frames?)"
    forward = _full(
        rf"(?:(?:step|go|move|skip|jump|advance|nudge|fast forward) )?(?:forward|forwards|ahead)"
        rf"(?: (?:by )?{_NUM} ?{unit})?|(?:step|skip|advance|nudge)(?: (?:by )?{_NUM} ?{unit})"
        rf"|(?:(?:go|skip|step|move|advance) )?(?:to )?next frame|(?:advance|forward) (?:1 )?frame"
        rf"|(?:1 )?frame (?:forward|ahead)|{_NUM} ?{unit} (?:forward|ahead)|step", core,
    )
    backward = _full(
        rf"(?:(?:step|go|move|skip|jump|nudge|rewind) )?(?:back|backward|backwards)(?: (?:by )?{_NUM} ?{unit})?"
        rf"|rewind (?:by )?{_NUM} ?{unit}|(?:(?:go|skip|step|move) )?(?:to |back )?(?:previous|last) frame"
        rf"|(?:1 )?frame (?:back|backward|backwards)|{_NUM} ?{unit} (?:back|backward|backwards|ago)", core,
    )
    for match, sign in ((forward, 1.0), (backward, -1.0)):
        if not match:
            continue
        if core in ("forward", "forwards", "ahead", "back", "backward", "backwards", "go back", "go forward"):
            return None                      # too vague: could be the camera or "undo"
        groups = [group for group in match.groups() if group]
        if len(groups) >= 2:
            seconds = float(groups[0]) * (FRAME_SECONDS if groups[1].startswith("frame") else 1.0)
        else:
            seconds = FRAME_SECONDS if "frame" in core else STEP_SECONDS
        direction = "forward" if sign > 0 else "back"
        return Result([A.timeline("step", sign * seconds)], f"Stepping {direction}.")

    # speed
    result = _rule_speed(core, ctx)
    if result:
        return result

    # restart, play, pause
    if _full(
        rf"(?:play|start|run|begin|watch|show)(?: {_CLIP})? (?:from|at) (?:beginning|start|top|very beginning)"
        rf"|(?:play|start|run|do|show|watch)(?: {_CLIP})? (?:again|once more|1 more time)|replay(?: {_CLIP})?"
        rf"|start again|begin again", core,
    ):
        return Result([A.timeline("restart"), A.timeline("play")], "Playing from the start.")
    if _full(
        rf"restart(?: {_CLIP})?|start over|from (?:beginning|start|top)|(?:back|go back|return) to "
        rf"(?:beginning|start)|rewind(?: {_CLIP})?|reset (?:{_CLIP}|time)|(?:{_CLIP} )?from (?:beginning|start)",
        core,
    ):
        return Result([A.timeline("restart")], "Back to the start.")
    if _full(r"toggle(?: (?:play|playback|animation|pause|play pause))?|play pause|pause play", core):
        return Result([A.timeline("toggle")], "Toggling play and pause.")
    if _full(
        rf"(?:play|resume|continue|unpause|un pause|unfreeze|start|go|go on|keep going|keep playing|carry on"
        rf"|start playing|begin playing|proceed|roll)(?: (?:{_CLIP}|playing))?"
        rf"|(?:start|run|begin|play|resume|continue|unpause) {_CLIP}|{_CLIP} (?:play|on)|press play|hit play",
        core,
    ):
        return Result([A.timeline("play")], "Playing.")
    if _full(
        rf"(?:pause|stop|freeze|halt|hold|hold on|hold up|wait|stand still|stay|stay still|freeze frame)"
        rf"(?: (?:{_CLIP}|playing|moving|playback))?|{_CLIP} (?:pause|stop|off)|(?:press|hit) pause"
        rf"|stop (?:{_CLIP} )?(?:playing|moving)|dont move|do not move", core,
    ) or core in _PAUSE_MISHEARD:
        return Result([A.timeline("pause")], "Paused.")
    return None


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------

_CAM = r"(?:camera|view|cam)"
_ZOOM_IN = (
    rf"zoom(?: {_CAM})? in(?: {_CAM})?(?: further| closer| close)?|zoom in on(?: (?:avatar|character|body|model))?"
    rf"|(?:(?:come|get|go|move|bring|zoom|step|push|look|take|give|have)(?: {_CAM})?(?: in)? )?"
    rf"(?:closer|nearer|closer look|closer view|close up|closeup)(?: (?:to avatar|to body|in))?"
    rf"|move in|push in|magnify|enlarge|bigger|make bigger|zoom"
)
_ZOOM_OUT = (
    rf"zoom(?: {_CAM})? out(?: {_CAM})?(?: further)?"
    rf"|(?:(?:go|move|get|pull|bring|zoom|step)(?: {_CAM})? )?(?:farther|further|farther away|further away"
    rf"|away|farther back|further back)"
    rf"|(?:move|pull|zoom|bring)(?: {_CAM})? back|back up|back off|back away|pull out|move out|smaller"
    rf"|make smaller|wider|wider view|wide view|wide shot"
)
_ORBIT_VERB = r"(?:rotate|turn|orbit|spin|pan|swing|move|go|circle|look|rotating|twist|swivel|revolve)"
_ORBIT_WHAT = r"(?: (?:camera|view|cam|avatar|character|model|body|around|camera around|around avatar))?"
_TILT_VERB = r"(?:rotate|orbit|tilt|pan|move|go|look|swing|angle)"
_VIEW_VERB = (
    r"(?:show|view|look|look at|see|go|go to|switch to|change to|display|turn to|rotate to|move to|give|camera"
    r"|view from|show view|camera to|look from|see from|watch|watch from)"
)
_VIEWS = [
    ("back", rf"(?:{_VIEW_VERB} )?from (?:behind|back|rear|back side|backside)"
             rf"|(?:{_VIEW_VERB} )?(?:back|rear|behind|posterior) (?:view|side)|(?:show|see|view) (?:back|behind|rear)"
             rf"|behind|(?:{_VIEW_VERB} )?back of (?:avatar|body|character|model)"),
    ("front", rf"(?:{_VIEW_VERB} )?(?:from )?(?:front|anterior)(?: (?:view|side))?"
              rf"|(?:{_VIEW_VERB} )?front of (?:avatar|body|character|model)|face (?:camera|forward|front)"),
    ("left", rf"(?:{_VIEW_VERB} )?from left(?: (?:side|hand side))?"
             rf"|(?:{_VIEW_VERB} )?left (?:side|view|side view|profile|hand side)|(?:show|see|view) left"),
    ("right", rf"(?:{_VIEW_VERB} )?from right(?: (?:side|hand side))?"
              rf"|(?:{_VIEW_VERB} )?right (?:side|view|side view|profile|hand side)|(?:show|see|view) right"
              rf"|(?:{_VIEW_VERB} )?(?:from side|side view|profile(?: view)?|sideways|side on)"),
    ("top", rf"(?:{_VIEW_VERB} )?from (?:above|top|overhead|up high)"
            rf"|(?:{_VIEW_VERB} )?(?:top|overhead|birds eye|bird eye|aerial|top down)(?: view)|above|overhead"
            rf"|top down|birds eye"),
]
_VIEW_REPLY = {
    "back": "Here is the view from the back.", "front": "Here is the view from the front.",
    "left": "Here is the view from the left side.", "right": "Here is the view from the right side.",
    "top": "Here is the view from above.",
}
_RESET = (
    rf"reset(?: (?:{_CAM}|zoom|position|angle|rotation|everything))*"
    rf"|(?:re ?center|center)(?: (?:{_CAM}|avatar|on avatar|view))?"
    rf"|(?:(?:go|back|go back|return|switch|reset) )?(?:to )?(?:default|home|original|starting|initial|start"
    rf"|normal) (?:{_CAM}|position|angle)|{_CAM} reset"
)
YAW_DEGREES = {"": 45, "more": 45, "small": 15, "big": 90, "max": 180}
PITCH_DEGREES = {"": 20, "more": 20, "small": 10, "big": 45, "max": 60}
ZOOM_A_LOT = 3


def _rule_camera(core: str, piece: str, ctx: Context, catalog, joined: bool):
    for view, pattern in _VIEWS:
        if _full(pattern, core):
            return Result([A.camera("view", view)], _VIEW_REPLY[view])
    if _full(_RESET, core):
        return Result([A.camera("reset")], "Camera reset.")

    # zoom
    match = _full(rf"zoom(?: (?:to|at))? {_NUM} percent|{_NUM} percent zoom", core)
    if match:
        value = min(float(match.group(1) or match.group(2)), 100.0) / 100.0
        return Result([A.camera("zoom_to", value)], f"Zoom set to {round(value * 100)} percent.")
    if _full(r"zoom (?:to )?(?:halfway|half way|half|middle|medium)|half zoom|medium zoom", core):
        return Result([A.camera("zoom_to", 0.5)], "Zoom set to the middle.")
    if _full(r"closest|(?:max|maximum|full) zoom|zoom (?:max|maximum|to max)", core):
        return Result([A.camera("zoom_to", 1)], "Zooming all the way in.")
    if _full(r"farthest|furthest|widest|(?:min|minimum) zoom|zoom (?:min|minimum|to min)", core):
        return Result([A.camera("zoom_to", 0)], "Zooming all the way out.")
    amount, count, rest = take_amount(core)
    for op, pattern, word in (("zoom_in", _ZOOM_IN, "in"), ("zoom_out", _ZOOM_OUT, "out")):
        if not _full(pattern, rest):
            continue
        if amount == "max":
            return Result([A.camera("zoom_to", 1 if op == "zoom_in" else 0)], f"Zooming all the way {word}.")
        steps = {"count": count, "big": ZOOM_A_LOT}.get(amount, 1)
        reply = f"Zooming {word} a lot." if amount == "big" else f"Zooming {word}."
        return Result([A.camera(op, steps if steps > 1 else None)], reply)

    # orbit: degrees first ("45 degrees", "by 30"), then the direction
    degrees = None
    text = core
    match = re.search(rf"\b(?:by )?{_NUM} ?(?:degrees?|deg)\b", text)
    if match:
        degrees = float(match.group(1))
        text = (text[:match.start()] + " " + text[match.end():]).strip()
    amount, count, rest = take_amount(re.sub(r"\s+", " ", text))
    if degrees is None:
        match = re.search(rf"\b(?:by )?{_NUM}\b", rest)
        if match and re.match(rf"{_ORBIT_VERB}\b", rest):
            degrees = float(match.group(1))
            rest = re.sub(r"\s+", " ", rest[:match.start()] + " " + rest[match.end():]).strip()
    times = count if amount == "count" else 1

    def yaw() -> float:
        return degrees if degrees is not None else YAW_DEGREES.get(amount, 45) * times

    def pitch() -> float:
        return degrees if degrees is not None else PITCH_DEGREES.get(amount, 20) * times

    side = rf"{_ORBIT_VERB}{_ORBIT_WHAT}(?: (?:to|towards|toward|on))?"
    if _full(rf"{side} left|left turn|{_CAM} left|(?:{_ORBIT_VERB}{_ORBIT_WHAT} )?(?:counter ?clockwise"
             rf"|anti ?clockwise)", rest):
        return Result([A.camera("orbit", yaw=-yaw())], f"Turning the camera {tidy_number(yaw())} degrees to the left.")
    if _full(rf"{side} right|right turn|{_CAM} right|(?:{_ORBIT_VERB}{_ORBIT_WHAT} )?clockwise", rest):
        return Result([A.camera("orbit", yaw=yaw())], f"Turning the camera {tidy_number(yaw())} degrees to the right.")
    if _full(rf"{_TILT_VERB}{_ORBIT_WHAT} (?:up|upward|upwards)|(?:look|view|see)(?: from)? higher(?: up)?"
             rf"|higher|from higher|{_CAM} up|raise {_CAM}", rest):
        return Result([A.camera("orbit", pitch=pitch())], f"Tilting the camera {tidy_number(pitch())} degrees up.")
    if _full(rf"{_TILT_VERB}{_ORBIT_WHAT} (?:down|downward|downwards)|(?:look|view|see)(?: from)? lower"
             rf"|from lower|{_CAM} down|lower {_CAM}", rest):
        return Result([A.camera("orbit", pitch=-pitch())], f"Tilting the camera {tidy_number(pitch())} degrees down.")
    if _full(r"(?:turn|spin|flip|look|walk)(?: (?:camera|view|avatar|character|model|body))? around|flip"
             r"|flip (?:over|view|camera|avatar)|about face|u turn|(?:(?:see|show|view|look at|go to) )?"
             r"(?:other|opposite) side", rest):
        return Result([A.camera("orbit", yaw=degrees if degrees is not None else 180)], "Turning the camera around.")
    if _full(rf"(?:rotate|orbit|spin|revolve|circle|rotating|go|move){_ORBIT_WHAT}"
             rf"|(?:rotate|orbit|spin|go|move|turn){_ORBIT_WHAT} (?:full circle|full turn|full rotation)"
             rf"|full circle|full turn|full rotation", rest) and rest not in ("go", "move"):
        full = "full" in rest
        angle = degrees if degrees is not None else (360 if full else yaw())
        return Result([A.camera("orbit", yaw=angle)], f"Turning the camera {tidy_number(angle)} degrees.")
    return None


# ---------------------------------------------------------------------------
# Action clips
# ---------------------------------------------------------------------------

_ACTION_TAIL = r"(?: (?:action|animation|clip|motion|move|movement|exercise|cycle))?(?: (?:again|too))?$"
_WHO = r"(?:(?:avatar|character|model|body|person|figure) )?"
_MAKE = rf"(?:make|have|let|get|tell|ask) {_WHO}(?:to )?(?:(?:do|perform|play|start|go|begin) )?"
_DO = "(?:" + "|".join(sorted([
    "do", "perform", "play", "start", "show", "run", "begin", "trigger", "try", "switch to", "change to", "go to",
    "animate", "execute", "demonstrate", "demo", "go", "go for", "start doing", "play back",
], key=len, reverse=True)) + ") "
_ACTION_SYNONYMS = {
    "sprint": "run", "sprinting": "run", "hop": "jump", "hopping": "jump", "leap": "jump", "stand": "idle",
    "stand still": "idle", "standing": "idle", "rest": "idle", "relax": "idle", "nothing": "idle",
    "default": "idle", "normal": "idle",
}
# Things people ask an avatar to do. With none of them available the reply says so instead of guessing.
_COMMON_ACTIONS = {
    "run", "jog", "walk", "jump", "idle", "sprint", "dance", "squat", "kick", "throw", "wave", "sit", "stand",
    "lunge", "stretch", "swim", "punch", "serve", "forehand", "backhand", "crouch", "hop", "skip", "climb",
}


_COMMON_ACTION_STEMS = {A.action_words(word)[0] for word in _COMMON_ACTIONS}


def no_such_action(target: str, ctx: Context) -> Result:
    names = ctx.available_actions
    if not names:
        return Result([], "This avatar has no action clips right now.")
    return Result([], f"This avatar has no \"{target}\" action. It can do: {', '.join(names[:12])}.")


def _rule_action(core: str, piece: str, ctx: Context, catalog, joined: bool):
    available = ctx.available_actions

    # back to the avatar's own animation
    if _full(
        r"stop (?:action|actions|running|jogging|jumping|walking|dancing|doing)(?: (?:action|clip|animation))?"
        r"|(?:back|go back|return|switch back) to (?:idle|normal|own|default)(?: (?:animation|walk|pose|state))?"
        r"|(?:stop|end|cancel) (?:action|clip)|no action", core,
    ):
        idle = A.match_action_name("idle", available)
        if isinstance(idle, str):
            return Result([A.play_action(idle)], "Back to the avatar's own animation.")
        return no_such_action("idle", ctx)

    made = re.match(_MAKE, core)
    done = re.match(_DO, core)
    target = core[(made or done).end():] if (made or done) else core
    target = re.sub(_ACTION_TAIL, "", re.sub(r"^(?:like )", "", target)).strip()
    if not target or len(target.split()) > 4:
        return None
    wanted = _ACTION_SYNONYMS.get(target, target)

    found = A.match_action_name(wanted, available, fuzzy=bool(made or done))
    if isinstance(found, str):
        if A.action_words(found) == ["idl"]:
            return Result([A.play_action(found)], "Back to the avatar's own animation.")
        return Result([A.play_action(found)], f"Playing the {found} action.")
    if isinstance(found, list):
        return Result([], f"Which one do you mean: {join_names(found[:4], 'or')}?")
    stems = set(A.action_words(wanted))
    if stems and stems <= _COMMON_ACTION_STEMS:
        return no_such_action(target, ctx)
    return None


# ---------------------------------------------------------------------------
# Muscles and bones
# ---------------------------------------------------------------------------

def _any(words: list[str]) -> str:
    return "(?:" + "|".join(sorted(words, key=len, reverse=True)) + ")"


# After one of these, words that are not a known part get "I do not know a part called ...".
_SHOW_STRONG = [
    "show where is", "show where", "show", "highlight", "high light", "where is", "wheres", "where are", "where",
    "point to", "point at", "point out", "point", "find", "select", "locate", "look at", "take look at",
    "have look at", "focus on", "focus", "zoom to", "zoom in on", "zoom on", "zoom into", "zoom in to", "mark",
    "indicate", "light up", "identify", "isolate", "center on",
]
# After one of these, unknown words go to the LLM ("go to the gym" is not about a part).
_SHOW_WEAK = [
    "display", "see", "go to", "pick", "choose", "bring up", "pull up", "view", "give", "check out", "check",
    "tap", "click on", "click", "search for", "search", "switch to", "move to", "jump to", "take to",
]
_SHOW = _any(_SHOW_STRONG + _SHOW_WEAK)
_DESCRIBE = _any([
    "what is", "whats", "what are", "describe", "tell about", "tell more about", "explain", "info on",
    "info about", "information on", "information about", "define", "what about", "how about",
])
# No alternative may be the start of another one here (that would make the pattern slow on hostile input).
_TARGET_TAIL = (
    r"(?: (?:is|are|located|at|too|again|area|region|on body|on skeleton|on avatar|on model|in body|as well"
    r"|muscle group|look like|looks like))+$"
)
MAX_PART_WORDS = 8            # the longest catalog name has 6 words; plus "left" and "muscle"
_NOT_A_NAME = {
    "how", "why", "what", "when", "which", "who", "do", "does", "should", "can", "to", "if", "with", "for",
    "about", "is", "are", "i", "you", "we", "not", "dont", "work", "works",
}


# "the muscle that lifts the arm": a description, not a name. The core has lost "that", so look at the piece.
_DESCRIBED_RE = re.compile(
    r"\b(?:muscles?|bones?|ligaments?|tendons?|parts?|ones?|things?) (?:that|which|who|where|used|involved"
    r"|responsible)\b"
)

# Said alone, these are more likely "go back" / "the front" than a body region.
_NOT_A_BARE_PART = {"back", "head", "face", "hand", "core", "index", "pointer", "temple", "hammer", "anvil"}


def _part_reply(found: PartMatch) -> str:
    side = f"{found.side} " if found.side in ("left", "right") else ""
    name = f"the {side}{found.part.name}"
    if found.how == "region":
        return f"For the {found.heard} I am showing {name}."
    if found.how == "fuzzy":
        return f"Here is {name}. I heard \"{found.heard}\"."
    if found.how == "default" and found.others:
        return f"Here is {name}. There is also {join_names(found.others[:3])}."
    return f"Here is {name}."


def no_part_reply(found: NoMatch) -> str:
    names = join_names(found.suggestions[:3])
    if found.reason == "missing":
        closest = f" The closest parts I have are {names}." if names else ""
        return f"This avatar has no {found.wanted} in its list of muscles and bones.{closest}"
    if found.reason == "ambiguous":
        if found.total <= 3:
            return f"Which one do you mean: {join_names(found.suggestions[:3], 'or')}?"
        return f"There are {found.total} parts with \"{found.heard}\" in the name, for example {names}. Which one?"
    if names and found.close:
        return f"I do not know a part called \"{found.heard}\". The closest names I have are {names}."
    if names:
        return f"I do not know a part called \"{found.heard}\". I know muscles and bones such as {names}."
    return f"I do not know a part called \"{found.heard}\"."


def _rule_other_side(core: str, piece: str, ctx: Context, catalog, joined: bool):
    """'the other side', 'now the right one': the highlighted part on the other side."""
    match = _full(
        r"(?:(?:show|see|highlight|do|same on|same for|what about|how about) )?(?:on )?"
        r"(?:(other|opposite) (?:side|one|leg|arm|hand|foot)|(left|right) one|other)(?: too| as well)?", core,
    )
    part = ctx.selected_part
    if not match or not part or part["side"] not in ("left", "right") or catalog is None:
        return None
    known = catalog.part_named(part["name"], part["kind"])
    if not known:
        return None
    side = match.group(2) or ("right" if part["side"] == "left" else "left")
    if side not in known.sides:
        return None
    return Result([A.show_part(known.kind, known.name, side)], f"Here is the {side} {known.name}.")


def _rule_part(core: str, piece: str, ctx: Context, catalog: Catalog | None, joined: bool):
    match = _full(rf"({_SHOW}|{_DESCRIBE}) (.+)", core)
    verb, target = (match.group(1), match.group(2)) if match else ("", core)
    asks_what = bool(verb) and bool(_full(_DESCRIBE, verb))
    target = re.sub(_TARGET_TAIL, "", target).strip()
    words = target.split()
    if not words or len(words) > MAX_PART_WORDS:
        return None
    plausible = len(words) <= 5 and not (_NOT_A_NAME & set(words)) and not _DESCRIBED_RE.search(piece)

    if catalog is None:
        if (verb and not asks_what and plausible) or mentions_anatomy(target):
            return Result(need_catalog=True)
        return None

    prefer = {"muscles": "muscle", "skeleton": "bone"}.get(ctx.layer, "")
    found = catalog.find(target, prefer_kind=prefer, bare=not verb or asks_what)
    if isinstance(found, PartMatch):
        if asks_what and found.how == "fuzzy":
            return None
        if not verb and core in _NOT_A_BARE_PART:
            return None
        return Result([found.to_action()], _part_reply(found))
    if not found.heard:
        return None
    if not plausible or asks_what:
        return None
    if found.reason == "missing":
        return Result([], no_part_reply(found))        # "lats": we know what it is and that it is not there
    if not verb or verb in _SHOW_WEAK:
        return None
    return Result([], no_part_reply(found))


# ---------------------------------------------------------------------------
# Putting it together
# ---------------------------------------------------------------------------

RULES = [
    _rule_clear,
    _rule_question,
    _rule_pose_angle,
    _rule_pose,
    _rule_panel,
    _rule_avatar,
    _rule_layer,
    _rule_timeline,
    _rule_other_side,
    _rule_camera,
    _rule_action,
    _rule_part,
]

# Sending the same one of these twice in one answer is pointless ("start the pose estimation and read
# the data" would otherwise switch it on twice when it was off).
_ONCE = {"pose_estimation", "set_layer", "open_panel", "clear_selection"}


def _parse_piece(piece: str, ctx: Context, catalog: Catalog | None, joined: bool = False) -> Result | None:
    core = make_core(piece)
    if not core:
        return None
    for rule in RULES:
        result = rule(core, piece, ctx, catalog, joined)
        if result is not None:
            return result
    return None


def _merge(results: list[Result]) -> Result:
    if any(result.need_catalog for result in results):
        return Result(need_catalog=True)
    merged = Result()
    replies: list[str] = []
    for result in results:
        for action in result.actions:
            if action["type"] in _ONCE and action in merged.actions:
                continue
            merged.actions.append(action)
        if result.reply and result.reply not in replies:
            replies.append(result.reply)
    # "Starting the pose estimation." + "Starting the pose estimation and reading ..." -> keep the second
    replies = [
        reply for reply in replies
        if not any(other != reply and other.startswith(reply.rstrip(".")) for other in replies)
    ]
    merged.reply = " ".join(replies)
    return merged


def parse(text: object, context: object = None, catalog: Catalog | None = None) -> Result | None:
    """The rules' answer, or None when the LLM should handle the sentence."""
    ctx = context if isinstance(context, Context) else Context(context)
    sentence = normalize(text, commas=True)
    sentence = re.sub(r"\b(?:left and right|right and left)\b", "both", sentence)
    if not sentence:
        return None

    pieces = [piece for piece in _SPLIT_RE.split(sentence) if piece and make_core(piece)]
    if len(pieces) > 1:
        results = []
        for piece in pieces:
            result = _parse_piece(piece, ctx, catalog)
            if result is None:
                break
            results.append(result)
        else:
            return _merge(results)

    whole = re.sub(r"\s+", " ", sentence.replace(",", " ")).strip()
    return _parse_piece(whole, ctx, catalog, joined=len(pieces) > 1)
