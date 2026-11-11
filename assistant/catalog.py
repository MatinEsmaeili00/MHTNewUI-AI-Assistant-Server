"""
The catalog (the avatar's muscle and bone names) and the matching of what the user said against it.

    catalog = store.get(catalog_id)
    found = catalog.find("left hamstring")      -> PartMatch(part=Biceps femoris (long head), side="left", ...)
    found = catalog.find("lats")                -> NoMatch(suggestions=[...])

Matching order (first hit wins):
  1. vertebrae, ribs, fingers and toes written in a regular way ("L3", "third rib", "pinky toe")
  2. catalog names: full name, name without the "(qualifier)", "qualifier name", a unique qualifier ("jaw")
  3. ALIASES: common English names
  4. REGIONS: body regions and muscle groups; one representative part is picked and the reply says so
  5. MISHEARD: what speech recognition makes of some names
  6. fuzzy matching (difflib) for misspellings, with a threshold
Every table is checked against the catalog that was sent: a name that is not in it is never returned.
"""

from __future__ import annotations

import difflib
import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from . import config, log
from .text import ORDINALS, normalize

KINDS = ("muscle", "bone")
SIDES = ("left", "right", "center")

STOPWORDS = {
    "the", "a", "an", "of", "and", "my", "her", "his", "your", "their", "its", "our", "this", "that", "these",
    "those", "on", "in", "at",
}

# ---------------------------------------------------------------------------
# Tables. Keys are written in the normalised form (lower case, no stop words).
# Values are catalog names in order of preference; the first one that exists in the catalog is used.
# ---------------------------------------------------------------------------

# "deltoid" -> which of Deltoid (anterior / middle / posterior)?
DEFAULT_QUALIFIER = {
    "deltoid": "middle",
    "trapezius": "upper",
    "biceps femoris": "long head",
}

# A qualifier that says nothing on its own is not a name ("show me the anterior").
GENERIC_QUALIFIERS = {
    "anterior", "posterior", "middle", "upper", "lower", "long head", "short head", "medial head", "deep head",
    "ear", "true rib", "false rib", "floating rib",
}

# Other names for one part.
ALIASES: dict[str, list[str]] = {
    # muscles
    "biceps": ["Biceps brachii"], "bicep": ["Biceps brachii"],
    "triceps": ["Triceps brachii"], "tricep": ["Triceps brachii"],
    "gastroc": ["Gastrocnemius"], "gastrocs": ["Gastrocnemius"],
    "glute max": ["Gluteus maximus"], "gluteus max": ["Gluteus maximus"],
    "glute med": ["Gluteus medius"], "gluteus med": ["Gluteus medius"],
    "glute min": ["Gluteus minimus"], "gluteus min": ["Gluteus minimus"],
    "pec": ["Pectoralis major"], "pecs": ["Pectoralis major"], "pectoral": ["Pectoralis major"],
    "pectorals": ["Pectoralis major"], "pectoralis": ["Pectoralis major"], "pec major": ["Pectoralis major"],
    "pec minor": ["Pectoralis minor"],
    "delt": ["Deltoid (middle)"], "delts": ["Deltoid (middle)"],
    "front deltoid": ["Deltoid (anterior)"], "front delt": ["Deltoid (anterior)"],
    "anterior delt": ["Deltoid (anterior)"],
    "rear deltoid": ["Deltoid (posterior)"], "rear delt": ["Deltoid (posterior)"],
    "back deltoid": ["Deltoid (posterior)"], "posterior delt": ["Deltoid (posterior)"],
    "side deltoid": ["Deltoid (middle)"], "side delt": ["Deltoid (middle)"],
    "lateral deltoid": ["Deltoid (middle)"], "middle delt": ["Deltoid (middle)"],
    "trap": ["Trapezius (upper)"], "traps": ["Trapezius (upper)"],
    "upper trap": ["Trapezius (upper)"], "upper traps": ["Trapezius (upper)"],
    "middle trap": ["Trapezius (middle)"], "middle traps": ["Trapezius (middle)"],
    "mid trap": ["Trapezius (middle)"], "mid traps": ["Trapezius (middle)"],
    "lower trap": ["Trapezius (lower)"], "lower traps": ["Trapezius (lower)"],
    "it band": ["Iliotibial tract"], "i t band": ["Iliotibial tract"], "itb": ["Iliotibial tract"],
    "itb band": ["Iliotibial tract"], "itb tract": ["Iliotibial tract"],
    "iliotibial band": ["Iliotibial tract"], "it tract": ["Iliotibial tract"],
    "acl": ["Anterior cruciate ligament"],
    "anterior cruciate": ["Anterior cruciate ligament"],
    "pcl": ["Posterior cruciate ligament"],
    "posterior cruciate": ["Posterior cruciate ligament"],
    "mcl": ["Medial collateral ligament"],
    "medial collateral": ["Medial collateral ligament"],
    "lcl": ["Lateral collateral ligament"],
    "lateral collateral": ["Lateral collateral ligament"],
    "mpfl": ["Medial patellofemoral ligament"], "lpfl": ["Lateral patellofemoral ligament"],
    "patellar tendon": ["Patellar ligament"], "patella tendon": ["Patellar ligament"],
    "patella ligament": ["Patellar ligament"], "kneecap tendon": ["Patellar ligament"],
    "quad tendon": ["Quadriceps tendon"],
    "tfl": ["Tensor fasciae latae"], "tensor fascia lata": ["Tensor fasciae latae"],
    "tensor fascia latae": ["Tensor fasciae latae"],
    "peroneus longus": ["Fibularis longus"], "peroneus brevis": ["Fibularis brevis"],
    "peroneus tertius": ["Fibularis tertius"],
    "psoas": ["Psoas major"], "iliopsoas": ["Psoas major", "Iliacus"],
    "vmo": ["Vastus medialis"], "teardrop": ["Vastus medialis"],
    "adductor magnus": ["Adductor magnus & minimus"], "adductor minimus": ["Adductor magnus & minimus"],
    # muscles and tendons people ask for that many atlases do not have (the first name is the real one)
    "lats": ["Latissimus dorsi"], "lat": ["Latissimus dorsi"], "latissimus": ["Latissimus dorsi"],
    "achilles": ["Achilles tendon", "Calcaneal tendon"], "achilles tendon": ["Achilles tendon", "Calcaneal tendon"],
    "achilles heel": ["Achilles tendon", "Calcaneal tendon"],
    "abs": ["Rectus abdominis"], "abdominals": ["Rectus abdominis"], "6 pack": ["Rectus abdominis"],
    "abdominal": ["Rectus abdominis"], "core": ["Rectus abdominis"],
    "obliques": ["External oblique"], "oblique": ["External oblique"],
    "serratus": ["Serratus anterior"],
    "erector spinae": ["Erector spinae"], "spinal erectors": ["Erector spinae"],
    "scm": ["Sternocleidomastoid"],
    "meniscus": ["Medial meniscus", "Lateral meniscus"],
    "plantar fascia": ["Plantar fascia", "Plantar aponeurosis"],
    # bones
    "thigh bone": ["Femur"], "thighbone": ["Femur"],
    "shin bone": ["Tibia"], "shinbone": ["Tibia"],
    "calf bone": ["Fibula"],
    "collar bone": ["Clavicle (collarbone)"], "collarbone": ["Clavicle (collarbone)"],
    "shoulderblade": ["Scapula (shoulder blade)"], "shoulder blade": ["Scapula (shoulder blade)"],
    "knee cap": ["Patella (kneecap)"], "kneecap": ["Patella (kneecap)"], "knee bone": ["Patella (kneecap)"],
    "jaw": ["Mandible (jaw)"], "jawbone": ["Mandible (jaw)"], "jaw bone": ["Mandible (jaw)"],
    "lower jaw": ["Mandible (jaw)"], "chin": ["Mandible (jaw)"],
    "upper jaw": ["Maxilla"],
    "breast bone": ["Sternum (breastbone)"], "breastbone": ["Sternum (breastbone)"],
    "chest bone": ["Sternum (breastbone)"],
    "heel bone": ["Calcaneus"], "heel": ["Calcaneus"],
    "tailbone": ["Coccyx"], "tail bone": ["Coccyx"],
    "pelvis": ["Hip bone (pelvis)"], "pelvic bone": ["Hip bone (pelvis)"], "hipbone": ["Hip bone (pelvis)"],
    "hip bone": ["Hip bone (pelvis)"], "hips": ["Hip bone (pelvis)"],
    "upper arm bone": ["Humerus"], "arm bone": ["Humerus"],
    "ankle bone": ["Talus"],
    "cheekbone": ["Zygomatic bone"], "cheek bone": ["Zygomatic bone"], "cheek": ["Zygomatic bone"],
    "forehead": ["Frontal bone"],
    "nose": ["Nasal bone"], "nose bone": ["Nasal bone"],
    "back head": ["Occipital bone"], "back skull": ["Occipital bone"],
    "temple": ["Temporal bone"],
    "adams apple": ["Thyroid cartilage"], "voice box": ["Thyroid cartilage"], "larynx": ["Thyroid cartilage"],
    "hammer": ["Malleus (ear)"], "anvil": ["Incus (ear)"], "stirrup": ["Stapes (ear)"],
    "atlas": ["Atlas (C1)"], "axis": ["Axis (C2)"],
}

# What to offer when an alias points at a part this catalog does not have.
RELATED: dict[str, list[str]] = {
    "Latissimus dorsi": ["Teres major", "Trapezius (lower)", "Rhomboid major"],
    "Achilles tendon": ["Gastrocnemius", "Soleus", "Calcaneus"],
    "Medial meniscus": ["Medial collateral ligament", "Anterior cruciate ligament", "Patella (kneecap)"],
    "Plantar fascia": ["Calcaneus", "Flexor digitorum longus", "Flexor hallucis longus"],
    "Erector spinae": ["Trapezius (middle)", "Rhomboid major", "L3 vertebra"],
    "Sternocleidomastoid": ["Trapezius (upper)", "Clavicle (collarbone)", "Sternum (breastbone)"],
    "Serratus anterior": ["Pectoralis minor", "Subscapularis", "Rib 5 (true rib)"],
    "Rectus abdominis": ["Psoas major", "Iliacus", "Hip bone (pelvis)"],
    "External oblique": ["Psoas major", "Iliacus", "Hip bone (pelvis)"],
}

# Regions and muscle groups: there is no single part, so one is picked and the reply names it.
# With "muscle" / "bone" in the sentence the first name of that kind is taken.
REGIONS: dict[str, list[str]] = {
    "knee": ["Patella (kneecap)", "Patellar ligament"], "knee joint": ["Patella (kneecap)", "Patellar ligament"],
    "knee ligament": ["Anterior cruciate ligament", "Medial collateral ligament"],
    "cruciate": ["Anterior cruciate ligament"], "cruciate ligament": ["Anterior cruciate ligament"],
    "collateral ligament": ["Medial collateral ligament"],
    "patellofemoral ligament": ["Medial patellofemoral ligament"],
    "shoulder": ["Deltoid (middle)", "Scapula (shoulder blade)"],
    "shoulder joint": ["Deltoid (middle)", "Scapula (shoulder blade)"],
    "rotator cuff": ["Supraspinatus", "Infraspinatus", "Teres minor", "Subscapularis"],
    "elbow": ["Ulna", "Anconeus"], "elbow joint": ["Ulna", "Anconeus"],
    "hip": ["Hip bone (pelvis)", "Gluteus medius"], "hip joint": ["Hip bone (pelvis)", "Gluteus medius"],
    "hip flexor": ["Psoas major", "Iliacus"],
    "abductor": ["Gluteus medius", "Tensor fasciae latae"], "hip abductor": ["Gluteus medius"],
    "ankle": ["Talus", "Tibialis anterior"], "ankle joint": ["Talus", "Tibialis anterior"],
    "wrist": ["Scaphoid", "Lunate", "Flexor carpi radialis"], "carpal": ["Scaphoid", "Lunate"],
    "wrist flexor": ["Flexor carpi radialis"], "wrist extensor": ["Extensor carpi radialis longus"],
    "neck": ["C4 vertebra", "Trapezius (upper)"],
    "cervical": ["C4 vertebra"], "cervical spine": ["C4 vertebra"], "cervical vertebra": ["C4 vertebra"],
    "cervical vertebrae": ["C4 vertebra"], "neck vertebra": ["C4 vertebra"], "neck vertebrae": ["C4 vertebra"],
    "thoracic": ["T6 vertebra"], "thoracic spine": ["T6 vertebra"], "thoracic vertebra": ["T6 vertebra"],
    "thoracic vertebrae": ["T6 vertebra"],
    "lumbar": ["L3 vertebra"], "lumbar spine": ["L3 vertebra"], "lumbar vertebra": ["L3 vertebra"],
    "lumbar vertebrae": ["L3 vertebra"],
    "lower back": ["L3 vertebra", "Psoas major"], "low back": ["L3 vertebra", "Psoas major"],
    "back": ["Trapezius (middle)", "T8 vertebra"],
    "upper back": ["Trapezius (middle)", "Rhomboid major", "T4 vertebra"],
    "spine": ["T8 vertebra", "L3 vertebra"], "backbone": ["T8 vertebra", "L3 vertebra"],
    "back bone": ["T8 vertebra", "L3 vertebra"], "spinal column": ["T8 vertebra", "L3 vertebra"],
    "vertebra": ["T8 vertebra", "L3 vertebra"], "vertebrae": ["T8 vertebra", "L3 vertebra"],
    "skull": ["Frontal bone", "Parietal bone"], "cranium": ["Frontal bone", "Parietal bone"],
    "head": ["Frontal bone", "Parietal bone"], "face": ["Maxilla", "Zygomatic bone"],
    "ear": ["Malleus (ear)", "Incus (ear)", "Stapes (ear)"], "ear bone": ["Malleus (ear)", "Incus (ear)"],
    "rib": ["Rib 5 (true rib)"], "rib cage": ["Rib 5 (true rib)"], "ribcage": ["Rib 5 (true rib)"],
    "true rib": ["Rib 1 (true rib)"], "false rib": ["Rib 8 (false rib)"],
    "floating rib": ["Rib 11 (floating rib)"],
    "chest": ["Pectoralis major", "Sternum (breastbone)"],
    "thigh": ["Rectus femoris", "Femur"], "front thigh": ["Rectus femoris"],
    "back thigh": ["Biceps femoris (long head)"], "inner thigh": ["Adductor longus"],
    "outer thigh": ["Vastus lateralis", "Iliotibial tract"],
    "hamstring": ["Biceps femoris (long head)", "Semitendinosus", "Semimembranosus"],
    "hammy": ["Biceps femoris (long head)"], "hammies": ["Biceps femoris (long head)"],
    "quad": ["Rectus femoris", "Vastus lateralis"], "quadricep": ["Rectus femoris", "Vastus lateralis"],
    "quadriceps": ["Rectus femoris", "Vastus lateralis"],
    "glute": ["Gluteus maximus"], "gluteus": ["Gluteus maximus"], "gluteal": ["Gluteus maximus"],
    "butt": ["Gluteus maximus"], "buttock": ["Gluteus maximus"], "buttocks": ["Gluteus maximus"],
    "bum": ["Gluteus maximus"], "backside": ["Gluteus maximus"],
    "groin": ["Adductor longus", "Pectineus"],
    "adductor": ["Adductor longus", "Adductor magnus & minimus", "Adductor brevis"],
    "calf": ["Gastrocnemius", "Soleus", "Fibula"], "calves": ["Gastrocnemius", "Soleus", "Fibula"],
    "shin": ["Tibialis anterior", "Tibia"],
    "lower leg": ["Tibia", "Gastrocnemius"],
    "leg": ["Rectus femoris", "Femur"],
    "arm": ["Biceps brachii", "Humerus"], "upper arm": ["Biceps brachii", "Humerus"],
    "forearm": ["Brachioradialis", "Radius", "Ulna"],
    "hand": ["Third metacarpal"], "palm": ["Third metacarpal"], "metacarpal": ["Third metacarpal"],
    "finger": ["Proximal phalanx of the index finger"], "phalanges": ["Proximal phalanx of the index finger"],
    "phalanx": ["Proximal phalanx of the index finger"],
    "foot": ["Third metatarsal", "Calcaneus"], "feet": ["Third metatarsal", "Calcaneus"],
    "metatarsal": ["Third metatarsal"], "tarsal": ["Talus"],
    "toe": ["Proximal phalanx of the big toe"],
    "cuneiform": ["Medial cuneiform"], "sesamoid": ["Medial sesamoid of the big toe"],
    "rhomboid": ["Rhomboid major"], "peroneal": ["Fibularis longus"], "fibularis": ["Fibularis longus"],
    "tibialis": ["Tibialis anterior"],
}

# What speech recognition writes for some names -> what was said. The right-hand side goes through the
# normal matching again.
MISHEARD: dict[str, str] = {
    "bye steps": "biceps", "by steps": "biceps", "buy steps": "biceps", "bye ceps": "biceps", "by ceps": "biceps",
    "bi ceps": "biceps", "bye seps": "biceps", "by seps": "biceps", "bisects": "biceps",
    "try steps": "triceps", "tri ceps": "triceps", "try ceps": "triceps", "try seps": "triceps",
    "humorous": "humerus", "humorist": "humerus",
    "fee more": "femur", "femor": "femur", "female": "femur",
    "ham string": "hamstring", "ham strings": "hamstring",
    "so as": "psoas", "soas": "psoas", "so as major": "psoas major",
    "cock 6": "coccyx", "cox 6": "coccyx", "coxics": "coccyx", "coccix": "coccyx",
    "dell toyed": "deltoid", "del toid": "deltoid", "deltoyed": "deltoid", "dealt oid": "deltoid",
    "tail us": "talus", "solace": "soleus", "so lee us": "soleus", "solis": "soleus",
    "glue teas": "glutes", "gloots": "glutes", "glutes maximus": "gluteus maximus",
    "glutious maximus": "gluteus maximus",
    "gastric nemius": "gastrocnemius", "gastro nemius": "gastrocnemius", "gastronomy us": "gastrocnemius",
    "trapeze ius": "trapezius", "trapeziums": "trapezius",
    "clavical": "clavicle", "scapular": "scapula", "fibular": "fibula", "ulnar": "ulna",
    "patellar": "patella", "tibial": "tibia", "femoral": "femur",
    "lads": "lats", "lots": "lats", "semi dendenosis": "semitendinosus", "semi tendinosis": "semitendinosus",
    "semi tendinosus": "semitendinosus", "semi membranosus": "semimembranosus",
}

# Fingers and toes: "{segment} phalanx of the {digit}".
DIGITS = {
    "thumb": "thumb",
    "index finger": "index finger", "index": "index finger", "pointer finger": "index finger",
    "forefinger": "index finger", "pointer": "index finger",
    "middle finger": "middle finger",
    "ring finger": "ring finger",
    "little finger": "little finger", "pinky": "little finger", "pinkie": "little finger",
    "pinky finger": "little finger", "pinkie finger": "little finger", "small finger": "little finger",
    "big toe": "big toe", "great toe": "big toe", "hallux": "big toe",
    "second toe": "second toe", "third toe": "third toe", "middle toe": "third toe", "fourth toe": "fourth toe",
    "little toe": "little toe", "pinky toe": "little toe", "pinkie toe": "little toe", "small toe": "little toe",
    "fifth toe": "little toe", "baby toe": "little toe",
}
SEGMENTS = {"proximal": "proximal", "base": "proximal", "middle": "middle", "distal": "distal", "tip": "distal"}
_DIGIT_RE = re.compile(
    r"^(?:(" + "|".join(SEGMENTS) + r")(?: phalanx| bone)? )?("
    + "|".join(sorted(DIGITS, key=len, reverse=True))
    + r")(?: bone| bones| phalanx| tip)?$"
)

_SPOKEN_LETTERS = {"see": "c", "sea": "c", "tea": "t", "tee": "t", "el": "l", "ell": "l"}
_SPINE_LETTER = {"cervical": "c", "thoracic": "t", "lumbar": "l"}
_ORDINAL = "|".join(ORDINALS)
_VERTEBRA_RE = re.compile(r"^(?:vertebra |vertebrae )?(c|t|l|see|sea|tea|tee|el|ell) ?(\d{1,2})(?: vertebra| vertebrae)?$")
_VERTEBRA_WORD_RE = re.compile(
    rf"^(?:({_ORDINAL}) (cervical|thoracic|lumbar)(?: vertebra| vertebrae)?"
    rf"|(cervical|thoracic|lumbar)(?: vertebra| vertebrae)? (\d{{1,2}}))$"
)
_RIB_RE = re.compile(rf"^(?:ribs? (?:number )?(\d{{1,2}})|({_ORDINAL}) ribs?)$")
_META_RE = re.compile(r"^(metacarpal|metatarsal) (?:number )?(\d)$")

# Words that say "this sentence is about a body part" even when no catalog is known yet.
ANATOMY_WORDS = {
    "muscle", "muscles", "bone", "bones", "ligament", "ligaments", "tendon", "tendons", "vertebra", "vertebrae",
    "joint", "femur", "tibia", "fibula", "humerus", "radius", "ulna", "patella", "scapula", "clavicle", "sternum",
    "sacrum", "coccyx", "mandible", "maxilla", "deltoid", "trapezius", "gastrocnemius", "soleus", "gluteus",
    "pectoralis", "sartorius", "gracilis", "piriformis", "brachialis", "brachioradialis", "vastus", "rectus",
    "femoris", "semitendinosus", "semimembranosus", "popliteus", "talus", "calcaneus", "iliacus", "supraspinatus",
    "infraspinatus", "subscapularis", "rhomboid", "phalanx", "metacarpal", "metatarsal", "cartilage",
}


def key(text: object) -> str:
    """The form used for all comparisons: normalised, without stop words."""
    return " ".join(word for word in normalize(text).split() if word not in STOPWORDS)


# The real names are aliases too: "latissimus dorsi" must get the same answer as "lats".
for _names in list(ALIASES.values()):
    for _name in _names:
        ALIASES.setdefault(key(_name), [_name])


def split_name(name: str) -> tuple[str, str]:
    """'Biceps femoris (long head)' -> ('Biceps femoris', 'long head')."""
    match = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", name)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return name.strip(), ""


@dataclass(frozen=True)
class Part:
    kind: str
    name: str
    sides: tuple[str, ...]
    region: str = ""

    def to_json(self) -> dict:
        return {"kind": self.kind, "name": self.name, "sides": list(self.sides), "region": self.region}


@dataclass
class PartMatch:
    part: Part
    side: str                     # "left" | "right" | "center" | ""
    how: str                      # "exact" | "alias" | "default" | "region" | "fuzzy"
    heard: str                    # the words that were matched, as the user said them
    others: list[str] = field(default_factory=list)   # the other parts with the same base name

    def to_action(self) -> dict:
        return {"type": "show_part", "kind": self.part.kind, "name": self.part.name, "side": self.side}


@dataclass
class NoMatch:
    heard: str
    reason: str                   # "unknown" | "missing" | "ambiguous"
    suggestions: list[str] = field(default_factory=list)
    wanted: str = ""              # "missing": the real name of what the user asked for
    total: int = 0                # "ambiguous": how many parts fit
    close: bool = True            # False: the suggestions are only examples, nothing was similar


def take_side(text: str) -> tuple[str, str]:
    """('left biceps') -> ('left', 'biceps'). Both sides or no side -> ''."""
    said = set(re.findall(r"\b(left|right)\b", text))
    rest = re.sub(
        r"\b(?:(?:on|in|at|from) )?(?:(?:the|her|his|my|your) )?(?:left|right)(?: hand side| side)?(?: (?:one|of))?\b",
        " ", text,
    )
    rest = re.sub(r"\bboth(?: sides)?\b", " ", rest)
    rest = re.sub(r"\s+", " ", rest).strip()
    return (said.pop() if len(said) == 1 else ""), rest


def take_kind(text: str) -> tuple[str, str]:
    """('thigh muscles') -> ('muscle', 'thigh')."""
    kind = ""
    if re.search(r"\bmuscles?\b", text):
        kind = "muscle"
    elif re.search(r"\bbones?\b", text):
        kind = "bone"
    rest = re.sub(r"\b(?:muscles?|bones?)\b", " ", text)
    return kind, re.sub(r"\s+", " ", rest).strip()


def _phonetic(text: str) -> str:
    """A rough sound key: 'gastrocnemious' and 'gastrocnemius' both give 'gstrknms'."""
    text = text.replace(" ", "")
    text = re.sub(r"ph", "f", text)
    text = re.sub(r"c(?=[eiy])", "s", text)
    text = re.sub(r"[cq]", "k", text)
    text = text.replace("x", "ks").replace("z", "s")
    text = re.sub(r"kk+", "k", text)
    head, tail = text[:1], re.sub(r"[aeiouyhw]", "", text[1:])
    return re.sub(r"(.)\1+", r"\1", head + tail)


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def similarity(heard: str, name_key: str) -> float:
    """0..1. Spelling counts most; the sound key rescues things like 'femer' / 'soleous'."""
    if not heard or not name_key:
        return 0.0
    if re.findall(r"\d+", heard) != re.findall(r"\d+", name_key):
        return 0.0                # "rib 13" must not become "rib 1"
    spelling = max(_ratio(heard, name_key), _ratio(heard.replace(" ", ""), name_key.replace(" ", "")))
    if heard[0] != name_key[0] and _phonetic(heard)[:1] != _phonetic(name_key)[:1]:
        return spelling * 0.9     # a wrong first sound is a different word more often than not
    sound_heard, sound_name = _phonetic(heard), _phonetic(name_key)
    score = max(spelling, 0.6 * spelling + 0.4 * _ratio(sound_heard, sound_name))
    if sound_heard == sound_name and len(sound_heard) >= 4:
        score = max(score, SAME_SOUND)        # "coxix" / "coccyx": spelled differently, said the same
    return score


# With "show me ..." in front we can be a little more generous than for a bare word.
FUZZY_MIN = 0.82
FUZZY_MIN_BARE = 0.90
FUZZY_MIN_LENGTH = 5          # "shine" must not become "shin": short names have to be said exactly
SAME_SOUND = 0.84             # enough after "show me ...", not enough for a bare word
SUGGEST_MIN = 0.55            # below this a name is not "close" to what was heard
MAX_NAME_CHARS = 80           # the longest catalog name has 37 characters
EXAMPLE_PARTS = ["Biceps brachii", "Femur", "Gluteus maximus", "Deltoid (middle)", "Tibia", "Humerus"]

# Words found in many names; one of them alone does not name a part ("the long one").
COMMON_NAME_WORDS = {
    "long", "short", "head", "deep", "major", "minor", "middle", "upper", "lower", "medial", "lateral",
    "anterior", "posterior", "intermediate", "superior", "inferior", "true", "false", "floating", "big", "little",
    "first", "second", "third", "fourth", "fifth", "proximal", "distal", "toe", "finger", "rib", "ear", "bone",
    "vertebra", "longus", "brevis", "index", "ring", "thumb",
}


class Catalog:
    def __init__(self, parts: list[Part]):
        self.parts = parts
        self.by_name: dict[str, list[Part]] = {}       # lower-case exact name -> parts
        self.index: dict[str, list[Part]] = {}         # every key that names a part -> parts
        self.vocabulary: set[str] = set()

        qualifiers: dict[str, list[Part]] = {}
        for part in parts:
            self.by_name.setdefault(part.name.lower(), []).append(part)
            base, qualifier = split_name(part.name)
            base_key, qualifier_key = key(base), key(qualifier)
            full_key = key(f"{base} {qualifier}")
            for name_key in {full_key, base_key, key(f"{qualifier} {base}")}:
                if name_key:
                    self._add(name_key, part)
            if qualifier_key:
                qualifiers.setdefault(qualifier_key, []).append(part)
            # "C5 vertebra" -> "c5"; "Atlas (C1)" -> "c1 vertebra"; "Frontal bone" -> "frontal"
            match = re.match(r"^([clt]\d{1,2}) vertebra$", base_key)
            if match:
                self._add(match.group(1), part)
            if re.match(r"^[clt]\d{1,2}$", qualifier_key):
                self._add(f"{qualifier_key} vertebra", part)
            if base_key.endswith(" bone") and base_key[:-5] not in REGIONS:
                self._add(base_key[:-5], part)

        for qualifier_key, owners in qualifiers.items():
            if len(owners) == 1 and qualifier_key not in GENERIC_QUALIFIERS and qualifier_key not in self.index:
                self._add(qualifier_key, owners[0])

        self._name_keys = {key(part.name): part for part in parts}
        for name_key in self._name_keys:
            self.vocabulary.update(name_key.split())

        # Keys the fuzzy matcher may land on: catalog keys plus every table key that resolves here.
        self._fuzzy_keys = set(self.index)
        for table in (ALIASES, REGIONS):
            for phrase, names in table.items():
                if any(self.part_named(name) for name in names):
                    self._fuzzy_keys.add(phrase)
        for phrase in self._fuzzy_keys:
            self.vocabulary.update(phrase.split())
        self.vocabulary -= STOPWORDS

    def _add(self, name_key: str, part: Part) -> None:
        owners = self.index.setdefault(name_key, [])
        if part not in owners:
            owners.append(part)

    # -- construction -------------------------------------------------------

    @classmethod
    def from_raw(cls, raw: object) -> "Catalog | None":
        """Builds a catalog from the JSON the client sent. Bad entries are skipped; None if nothing is usable."""
        if not isinstance(raw, dict) or not isinstance(raw.get("parts"), list):
            return None
        parts: list[Part] = []
        seen = set()
        for entry in raw["parts"][: config.settings.max_catalog_parts]:
            if not isinstance(entry, dict):
                continue
            kind, name = entry.get("kind"), entry.get("name")
            if kind not in KINDS or not isinstance(name, str):
                continue
            name = re.sub(r"\s+", " ", name).strip()[:120]
            if not name or (kind, name) in seen:
                continue
            sides = entry.get("sides") if isinstance(entry.get("sides"), list) else []
            sides = tuple(side for side in SIDES if side in sides) or ("center",)
            region = entry.get("region") if isinstance(entry.get("region"), str) else ""
            seen.add((kind, name))
            parts.append(Part(kind, name, sides, region.strip()[:120]))
        return cls(parts) if parts else None

    def to_raw(self) -> dict:
        return {"parts": [part.to_json() for part in self.parts]}

    # -- lookups ------------------------------------------------------------

    def part_named(self, name: object, kind: str = "") -> Part | None:
        """The part with exactly this catalog name (case does not matter)."""
        if not isinstance(name, str):
            return None
        owners = self.by_name.get(name.strip().lower()) or ([self._name_keys[key(name)]] if key(name) in self._name_keys else [])
        for part in owners:
            if part.kind == kind:
                return part
        return owners[0] if owners else None

    def _choose(self, owners: list[Part], name_key: str, kind: str) -> tuple[Part, list[str]]:
        """One part out of several with the same key: the wanted kind, then the plain name, then the default."""
        same_kind = [part for part in owners if part.kind == kind] or owners
        chosen = same_kind[0]
        for part in same_kind:
            base, qualifier = split_name(part.name)
            if not qualifier and key(base) == name_key:
                chosen = part
                break
            if qualifier and DEFAULT_QUALIFIER.get(name_key) == key(qualifier):
                chosen = part
        return chosen, [part.name for part in same_kind if part is not chosen]

    def _first_existing(self, names: list[str], kind: str) -> Part | None:
        existing = [part for part in (self.part_named(name) for name in names) if part]
        for part in existing:
            if part.kind == kind:
                return part
        return existing[0] if existing else None

    def _regular(self, name_key: str) -> str | None:
        """Vertebrae, ribs, fingers and toes -> the key of the catalog name, '' if that one does not exist."""
        match = _VERTEBRA_RE.match(name_key)
        if match:
            letter = _SPOKEN_LETTERS.get(match.group(1), match.group(1))
            wanted = f"{letter}{int(match.group(2))}"
            return wanted if wanted in self.index else ""
        match = _VERTEBRA_WORD_RE.match(name_key)
        if match:
            number = ORDINALS[match.group(1)] if match.group(1) else int(match.group(4))
            wanted = f"{_SPINE_LETTER[match.group(2) or match.group(3)]}{number}"
            return wanted if wanted in self.index else ""
        match = _RIB_RE.match(name_key)
        if match:
            number = int(match.group(1)) if match.group(1) else ORDINALS[match.group(2)]
            wanted = f"rib {number}"
            return wanted if wanted in self.index else ""
        match = _META_RE.match(name_key)
        if match:
            ordinal = {number: word for word, number in ORDINALS.items()}.get(int(match.group(2)), "")
            wanted = f"{ordinal} {match.group(1)}"
            return wanted if wanted in self.index else ""
        match = _DIGIT_RE.match(name_key)
        if match:
            digit = DIGITS[match.group(2)]
            for segment in (SEGMENTS.get(match.group(1) or "", "proximal"), "proximal"):
                wanted = f"{segment} phalanx {digit}"
                if wanted in self.index:
                    return wanted
            return ""
        return None

    def _lookup(self, name_key: str, kind: str, depth: int = 0):
        """Exact lookups only. Returns (part, how, others), a NoMatch for a known-but-missing part, or None."""
        if not name_key:
            return None
        regular = self._regular(name_key)
        if regular == "":
            return NoMatch(name_key, "missing", self.closest(name_key), wanted=name_key)
        if regular:
            part, _others = self._choose(self.index[regular], regular, kind)
            return part, "exact", []
        if name_key in self.index:
            owners = self.index[name_key]
            part, others = self._choose(owners, name_key, kind)
            if kind and part.kind != kind and name_key in REGIONS:
                pass                      # "hip muscle": the index only has the bone, let REGIONS answer
            else:
                return part, ("default" if others else "exact"), others
        if name_key in ALIASES:
            part = self._first_existing(ALIASES[name_key], kind)
            if part:
                return part, "alias", []
            wanted = ALIASES[name_key][0]
            related = [name for name in RELATED.get(wanted, []) if self.part_named(name)]
            return NoMatch(name_key, "missing", related[:3] or self.closest(name_key), wanted=wanted)
        if name_key in REGIONS:
            part = self._first_existing(REGIONS[name_key], kind)
            if part:
                return part, "region", []
        if name_key in MISHEARD and depth == 0:
            return self._lookup(key(MISHEARD[name_key]), kind, depth=1)
        return None

    def _lookup_forms(self, name_key: str, kind: str):
        """The key as it is, then without a plural 's' on the last word."""
        found = self._lookup(name_key, kind)
        if found is None and len(name_key) > 3 and name_key.endswith("s") and not name_key.endswith("ss"):
            found = self._lookup(name_key[:-1], kind)
        return found

    def _scores(self, name_key: str) -> list[tuple[float, str]]:
        matcher = difflib.SequenceMatcher(None, autojunk=False)
        matcher.set_seq2(name_key)
        scores = []
        for candidate in self._fuzzy_keys:
            matcher.set_seq1(candidate)
            if matcher.real_quick_ratio() < 0.4 or matcher.quick_ratio() < 0.4:
                continue
            score = similarity(name_key, candidate)
            if score >= 0.4:
                scores.append((score, candidate))
        scores.sort(key=lambda item: (-item[0], item[1]))
        return scores

    def _part_for_key(self, name_key: str, kind: str) -> Part | None:
        found = self._lookup(name_key, kind)
        return found[0] if isinstance(found, tuple) else None

    def closest(self, heard: str, limit: int = 3) -> list[str]:
        """The catalog names nearest to what was heard, best first (for 'did you mean')."""
        names: list[str] = []
        for _score, candidate in self._scores(key(heard)):
            part = self._part_for_key(candidate, "")
            if part and part.name not in names:
                names.append(part.name)
            if len(names) == limit:
                break
        return names

    def _fix_words(self, name_key: str, minimum: float) -> str:
        """Corrects each word on its own: 'bicep femoralis' -> 'biceps femoris'."""
        fixed = []
        for word in name_key.split():
            if word in self.vocabulary or len(word) < FUZZY_MIN_LENGTH:
                fixed.append(word)
                continue
            known_words = [known for known in self.vocabulary if len(known) >= FUZZY_MIN_LENGTH]
            best = max(known_words, key=lambda known: similarity(word, known), default="")
            fixed.append(best if best and similarity(word, best) >= minimum else word)
        return " ".join(fixed)

    def _containing(self, name_key: str, kind: str) -> list[Part]:
        """Parts whose name has all these words: 'vastus' -> the three vastus muscles."""
        words = set(name_key.split())
        if not words or not words <= self.vocabulary or (len(words) == 1 and words <= COMMON_NAME_WORDS):
            return []
        found = [part for full_key, part in self._name_keys.items() if words <= set(full_key.split())]
        return [part for part in found if part.kind == kind] or found

    # -- the main entry point -------------------------------------------------

    def find(self, phrase: object, prefer_kind: str = "", bare: bool = False) -> PartMatch | NoMatch:
        """
        phrase: the words that should name a part, for example "left hamstring" or "the femur bone".
        prefer_kind: used when the user did not say "muscle" or "bone" and a name fits both.
        bare: the phrase came without "show me" / "highlight"; fuzzy matching is then stricter.
        """
        text = normalize(phrase)
        side_said, text = take_side(text)
        kind_said, without_kind = take_kind(text)
        kind = kind_said or prefer_kind
        heard = key(without_kind) or key(text)
        said = re.sub(r"^(?:(?:the|a|an|my|her|his|your|in|on|of|at) )+", "", without_kind or text)   # for the reply

        def result(part: Part, how: str, others: list[str]) -> PartMatch:
            if side_said in part.sides:
                side = side_said
            elif part.sides == ("center",):
                side = "center"
            else:
                side = ""
            return PartMatch(part, side, how, said, others)

        # 1-5: exact lookups, with and without the words "muscle" / "bone"
        for name_key in dict.fromkeys((key(text), key(without_kind))):
            found = self._lookup_forms(name_key, kind)
            if isinstance(found, tuple):
                return result(*found)
            if isinstance(found, NoMatch):
                found.heard = said
                return found
        if not heard:
            return NoMatch("", "unknown")
        if len(heard) > MAX_NAME_CHARS:
            return NoMatch(said[:MAX_NAME_CHARS], "unknown")       # far longer than any name: do not even try

        # 6: fuzzy. First the whole phrase, then word by word.
        minimum = FUZZY_MIN_BARE if bare else FUZZY_MIN
        scores = self._scores(heard)
        if scores and scores[0][0] >= minimum and min(len(heard), len(scores[0][1])) >= FUZZY_MIN_LENGTH:
            part = self._part_for_key(scores[0][1], kind)
            if part:
                return result(part, "fuzzy", [])
        fixed = self._fix_words(heard, minimum)
        if fixed != heard:
            found = self._lookup_forms(fixed, kind)
            if isinstance(found, tuple):
                return result(found[0], "fuzzy", found[2])
        containing = self._containing(fixed, kind)
        if len(containing) == 1:
            return result(containing[0], "fuzzy" if fixed != heard else "exact", [])
        if len(containing) > 1:
            return NoMatch(said, "ambiguous", [part.name for part in containing[:3]], total=len(containing))

        suggestions = []
        for score, candidate in scores:
            part = self._part_for_key(candidate, kind) if score >= SUGGEST_MIN else None
            if part and part.name not in suggestions:
                suggestions.append(part.name)
            if len(suggestions) == 3:
                break
        if len(suggestions) >= 2:
            return NoMatch(said, "unknown", suggestions)
        examples = [name for name in EXAMPLE_PARTS if self.part_named(name)] or [part.name for part in self.parts]
        return NoMatch(said, "unknown", (suggestions + [name for name in examples if name not in suggestions])[:3],
                       close=False)

    def candidates(self, text: object, limit: int = 12) -> list[Part]:
        """A short list of parts that could be what the sentence is about (for the LLM prompt)."""
        words = [word for word in key(text).split() if len(word) > 1][:40]
        scores: dict[Part, float] = {}

        def note(part: Part | None, score: float) -> None:
            if part and score > scores.get(part, 0.0):
                scores[part] = score

        for size in (4, 3, 2, 1):
            for start in range(len(words) - size + 1):
                phrase = " ".join(words[start:start + size])
                found = self._lookup_forms(phrase, "")
                if isinstance(found, tuple):
                    note(found[0], 1.0 + size / 10)
                    for other in found[2]:
                        note(self.part_named(other), 0.9)
                    if found[1] == "region":
                        for name in REGIONS.get(phrase, REGIONS.get(phrase[:-1], [])):
                            note(self.part_named(name), 0.95)
                elif size <= 2 and len(phrase) >= 4:
                    for score, candidate in self._scores(phrase)[:4]:
                        if score >= 0.7:
                            note(self._part_for_key(candidate, ""), score)
                    for part in self._containing(phrase, "")[:6]:
                        note(part, 0.75)
        # the region column: "back of the thigh" finds the hamstrings
        generic = {"front", "back", "deep", "side", "upper", "lower", "inner", "outer", "bones", "bone", "muscle"}
        asked = {word for word in words if len(word) >= 4 and word not in generic}
        for part in self.parts:
            if asked & set(key(part.region).split()):
                note(part, 0.6)
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0].name))
        return [part for part, _score in ranked[:limit]]


# Table keys that are also everyday words; alone they do not make a sentence "about a body part".
_EVERYDAY_WORDS = {
    "back", "head", "face", "core", "index", "pointer", "axis", "atlas", "hammer", "anvil", "stirrup", "temple",
    "hand", "lat", "female", "solace", "humorous", "humorist", "trap", "leg", "arm", "foot", "feet",
}


def mentions_anatomy(text: object) -> bool:
    """True if the sentence seems to be about a body part. Works without a catalog."""
    words = key(text).split()
    if ANATOMY_WORDS & set(words):
        return True
    for size in (3, 2, 1):
        for start in range(len(words) - size + 1):
            phrase = " ".join(words[start:start + size])
            for form in (phrase, phrase[:-1] if phrase.endswith("s") else phrase):
                if form in _EVERYDAY_WORDS:
                    continue
                if form in ALIASES or form in REGIONS or form in MISHEARD or form in DIGITS:
                    return True
            if _VERTEBRA_RE.match(phrase) or _RIB_RE.match(phrase):
                return True
    return False


# ---------------------------------------------------------------------------
# Store: catalogs by catalog_id, in memory and as small JSON files
# ---------------------------------------------------------------------------

class CatalogStore:
    MAX_IN_MEMORY = 32
    MAX_ON_DISK = 64

    def __init__(self, cache_dir: Path | None = None):
        self._cache_dir = cache_dir
        self._catalogs: dict[str, Catalog] = {}
        self._asked: set[str] = set()
        self._lock = threading.Lock()

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir or config.settings.catalog_cache_dir

    @staticmethod
    def safe_id(catalog_id: object) -> str:
        """Only letters, digits, '-' and '_' survive, so the id can be used as a file name."""
        if not isinstance(catalog_id, (str, int)) or isinstance(catalog_id, bool):
            return ""
        return re.sub(r"[^A-Za-z0-9_-]", "", str(catalog_id))[:64]

    def _path(self, safe: str) -> Path:
        return self.cache_dir / f"{safe}.json"

    def get(self, catalog_id: object) -> Catalog | None:
        safe = self.safe_id(catalog_id)
        if not safe:
            return None
        with self._lock:
            if safe in self._catalogs:
                return self._catalogs[safe]
        try:
            raw = json.loads(self._path(safe).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        catalog = Catalog.from_raw(raw)
        if catalog:
            self._remember(safe, catalog)
        return catalog

    def put(self, catalog_id: object, raw: object) -> Catalog | None:
        """Stores the catalog the client sent. Returns it (None if it was not usable)."""
        catalog = Catalog.from_raw(raw)
        safe = self.safe_id(catalog_id)
        if not catalog:
            return None
        if not safe:
            return catalog            # usable for this request, but there is no id to keep it under
        self._remember(safe, catalog)
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            temporary = self._path(safe).with_suffix(".tmp")
            temporary.write_text(json.dumps(catalog.to_raw()), encoding="utf-8")
            temporary.replace(self._path(safe))
            files = sorted(self.cache_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
            for old in files[self.MAX_ON_DISK:]:
                old.unlink(missing_ok=True)
        except OSError as error:
            log.warning("could not write the catalog cache: %r", error)
        return catalog

    def _remember(self, safe: str, catalog: Catalog) -> None:
        with self._lock:
            self._catalogs.pop(safe, None)
            self._catalogs[safe] = catalog
            self._asked.discard(safe)
            while len(self._catalogs) > self.MAX_IN_MEMORY:
                self._catalogs.pop(next(iter(self._catalogs)))

    def may_ask(self, catalog_id: object) -> bool:
        """
        True = answer need_catalog now. The protocol forbids asking twice in a row for the same id,
        so the second time this returns False (and the time after that it may ask again).
        """
        safe = self.safe_id(catalog_id)
        with self._lock:
            if safe in self._asked:
                self._asked.discard(safe)
                return False
            self._asked.add(safe)
            if len(self._asked) > 1000:
                self._asked.clear()
            return True


store = CatalogStore()
