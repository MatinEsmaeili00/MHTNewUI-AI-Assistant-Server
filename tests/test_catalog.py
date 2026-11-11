"""Part matching against the real catalog (93 muscles and ligaments, 121 bones) and the catalog store."""

from __future__ import annotations

import json

import pytest

from assistant import config
from assistant.catalog import (
    ALIASES, MISHEARD, REGIONS, RELATED, Catalog, CatalogStore, NoMatch, PartMatch, key, mentions_anatomy,
)


def found(catalog, phrase, **options):
    result = catalog.find(phrase, **options)
    assert isinstance(result, PartMatch), f"{phrase!r} -> {result}"
    return result.part.kind, result.part.name, result.side


def test_fixture_is_the_real_catalog(catalog):
    assert len(catalog.parts) == 214
    assert sum(part.kind == "muscle" for part in catalog.parts) == 93
    assert sum(part.kind == "bone" for part in catalog.parts) == 121


# ---- exact and normalised names ---------------------------------------------------------------------

def test_every_catalog_name_finds_itself(catalog):
    for part in catalog.parts:
        result = catalog.find(part.name)
        assert isinstance(result, PartMatch) and result.part == part, part.name
        result = catalog.find(part.name.upper())
        assert isinstance(result, PartMatch) and result.part == part, part.name


@pytest.mark.parametrize("phrase, name", [
    ("femur", "Femur"),
    ("the Femur bone", "Femur"),
    ("gluteus maximus", "Gluteus maximus"),
    ("GLUTEUS MAXIMUS!", "Gluteus maximus"),
    ("biceps femoris long head", "Biceps femoris (long head)"),
    ("long head of the biceps femoris", "Biceps femoris (long head)"),
    ("short head of the biceps femoris", "Biceps femoris (short head)"),
    ("anterior deltoid", "Deltoid (anterior)"),
    ("deltoid posterior", "Deltoid (posterior)"),
    ("lower trapezius", "Trapezius (lower)"),
    ("adductor magnus and minimus", "Adductor magnus & minimus"),
    ("proximal phalanx of thumb", "Proximal phalanx of the thumb"),
    ("mandible", "Mandible (jaw)"),
    ("frontal", "Frontal bone"),
    ("supinator deep head", "Supinator (deep head)"),
])
def test_names(catalog, phrase, name):
    assert found(catalog, phrase)[1] == name


# ---- the qualifier in parentheses is optional -------------------------------------------------------

@pytest.mark.parametrize("phrase, name, others", [
    ("deltoid", "Deltoid (middle)", {"Deltoid (anterior)", "Deltoid (posterior)"}),
    ("trapezius", "Trapezius (upper)", {"Trapezius (middle)", "Trapezius (lower)"}),
    ("biceps femoris", "Biceps femoris (long head)", {"Biceps femoris (short head)"}),
    ("triceps brachii", "Triceps brachii", {"Triceps brachii (medial head)"}),
    ("supinator", "Supinator", {"Supinator (deep head)"}),
])
def test_default_when_the_qualifier_is_left_out(catalog, phrase, name, others):
    result = catalog.find(phrase)
    assert isinstance(result, PartMatch)
    assert result.part.name == name
    assert set(result.others) == others                    # the reply can mention the other ones


# ---- common English names -----------------------------------------------------------------------------

@pytest.mark.parametrize("phrase, kind, name", [
    ("biceps", "muscle", "Biceps brachii"),
    ("bicep", "muscle", "Biceps brachii"),
    ("triceps", "muscle", "Triceps brachii"),
    ("hamstring", "muscle", "Biceps femoris (long head)"),
    ("hamstrings", "muscle", "Biceps femoris (long head)"),
    ("quads", "muscle", "Rectus femoris"),
    ("quadriceps", "muscle", "Rectus femoris"),
    ("calf", "muscle", "Gastrocnemius"),
    ("glutes", "muscle", "Gluteus maximus"),
    ("butt", "muscle", "Gluteus maximus"),
    ("pecs", "muscle", "Pectoralis major"),
    ("chest", "muscle", "Pectoralis major"),
    ("traps", "muscle", "Trapezius (upper)"),
    ("ACL", "muscle", "Anterior cruciate ligament"),
    ("PCL", "muscle", "Posterior cruciate ligament"),
    ("MCL", "muscle", "Medial collateral ligament"),
    ("LCL", "muscle", "Lateral collateral ligament"),
    ("hip flexor", "muscle", "Psoas major"),
    ("IT band", "muscle", "Iliotibial tract"),
    ("shin", "muscle", "Tibialis anterior"),
    ("shin bone", "bone", "Tibia"),
    ("thigh bone", "bone", "Femur"),
    ("collarbone", "bone", "Clavicle (collarbone)"),
    ("collar bone", "bone", "Clavicle (collarbone)"),
    ("shoulder blade", "bone", "Scapula (shoulder blade)"),
    ("kneecap", "bone", "Patella (kneecap)"),
    ("jaw", "bone", "Mandible (jaw)"),
    ("breastbone", "bone", "Sternum (breastbone)"),
    ("heel bone", "bone", "Calcaneus"),
    ("tailbone", "bone", "Coccyx"),
    ("pelvis", "bone", "Hip bone (pelvis)"),
    ("hip bone", "bone", "Hip bone (pelvis)"),
    ("upper arm bone", "bone", "Humerus"),
    ("forearm bones", "bone", "Radius"),
    ("skull", "bone", "Frontal bone"),
    ("spine", "bone", "T8 vertebra"),
    ("lower back", "bone", "L3 vertebra"),
    ("third rib", "bone", "Rib 3 (true rib)"),
    ("rib 3", "bone", "Rib 3 (true rib)"),
    ("rib number seven", "bone", "Rib 7 (true rib)"),
    ("floating ribs", "bone", "Rib 11 (floating rib)"),
    ("L3", "bone", "L3 vertebra"),
    ("C5", "bone", "C5 vertebra"),
    ("T10", "bone", "T10 vertebra"),
    ("l three", "bone", "L3 vertebra"),
    ("T-12 vertebra", "bone", "T12 vertebra"),
    ("fifth cervical vertebra", "bone", "C5 vertebra"),
    ("C1", "bone", "Atlas (C1)"),
    ("atlas", "bone", "Atlas (C1)"),
    ("thumb", "bone", "Proximal phalanx of the thumb"),
    ("index finger", "bone", "Proximal phalanx of the index finger"),
    ("tip of the index finger", "bone", "Distal phalanx of the index finger"),
    ("pinky", "bone", "Proximal phalanx of the little finger"),
    ("big toe", "bone", "Proximal phalanx of the big toe"),
    ("pinky toe", "bone", "Proximal phalanx of the little toe"),
    ("1st metacarpal", "bone", "First metacarpal"),
])
def test_common_names(catalog, phrase, kind, name):
    assert found(catalog, phrase)[:2] == (kind, name)


def test_every_table_entry_points_at_real_parts_or_is_declared_missing(catalog):
    """A typo in ALIASES / REGIONS would silently never match; this catches it."""
    for phrase, names in REGIONS.items():
        assert phrase == key(phrase), f"REGIONS key {phrase!r} is not in normalised form"
        for name in names:
            assert catalog.part_named(name), f"REGIONS[{phrase!r}] names {name!r}, which the catalog does not have"
    missing_on_purpose = set(RELATED) | {
        "Calcaneal tendon", "Lateral meniscus", "Plantar aponeurosis",
    }
    for phrase, names in ALIASES.items():
        assert phrase == key(phrase), f"ALIASES key {phrase!r} is not in normalised form"
        for name in names:
            assert catalog.part_named(name) or name in missing_on_purpose, f"ALIASES[{phrase!r}] -> {name!r}"
    for names in RELATED.values():
        for name in names:
            assert catalog.part_named(name), f"RELATED offers {name!r}, which the catalog does not have"
    for heard, meant in MISHEARD.items():
        assert heard == key(heard), f"MISHEARD key {heard!r} is not in normalised form"
        result = catalog.find(meant)                      # a part, or one we know this catalog lacks ("lats")
        assert isinstance(result, PartMatch) or result.reason == "missing", f"MISHEARD[{heard!r}] -> {meant!r}"


def test_aliases_are_checked_against_the_catalog_that_was_sent():
    """The same words on a different avatar: only names that exist there are returned."""
    small = Catalog.from_raw({"parts": [
        {"kind": "muscle", "name": "Latissimus dorsi", "sides": ["left", "right"], "region": "Back"},
        {"kind": "muscle", "name": "Semitendinosus", "sides": ["left", "right"], "region": "Back of thigh"},
        {"kind": "bone", "name": "Femur", "sides": ["left", "right"], "region": "Thigh"},
    ]})
    assert found(small, "lats")[1] == "Latissimus dorsi"         # exists here, unlike in the fixture
    assert found(small, "hamstring")[1] == "Semitendinosus"      # the first hamstring this catalog has
    assert isinstance(small.find("biceps"), NoMatch)             # no Biceps brachii here
    assert isinstance(small.find("kneecap"), NoMatch)


# ---- parts that are not there ---------------------------------------------------------------------------

@pytest.mark.parametrize("phrase, wanted", [
    ("lats", "Latissimus dorsi"),
    ("latissimus", "Latissimus dorsi"),
    ("achilles", "Achilles tendon"),
    ("achilles tendon", "Achilles tendon"),
    ("abs", "Rectus abdominis"),
])
def test_known_part_that_this_catalog_does_not_have(catalog, phrase, wanted):
    result = catalog.find(phrase)
    assert isinstance(result, NoMatch) and result.reason == "missing"
    assert result.wanted == wanted
    assert 2 <= len(result.suggestions) <= 3
    assert all(catalog.part_named(name) for name in result.suggestions)


def test_rib_and_vertebra_that_do_not_exist(catalog):
    for phrase in ("rib 13", "L7", "C9"):
        result = catalog.find(phrase)
        assert isinstance(result, NoMatch) and result.reason == "missing", phrase


@pytest.mark.parametrize("phrase", [
    "banana", "flux capacitor", "lung", "heart", "brain", "the weather", "camera", "table", "shine", "chain",
    "noise", "angle", "hello", "thanks", "status",
])
def test_no_confident_nonsense(catalog, phrase):
    result = catalog.find(phrase)
    assert isinstance(result, NoMatch), f"{phrase!r} became {getattr(result, 'part', None)}"
    assert 2 <= len(result.suggestions) <= 3                 # still offers names to try
    assert all(catalog.part_named(name) for name in result.suggestions)


def test_close_but_not_close_enough_offers_the_right_names(catalog):
    result = catalog.find("fibia")
    assert isinstance(result, NoMatch) and result.reason == "unknown" and result.close
    assert {"Tibia", "Fibula"} <= set(result.suggestions)


def test_several_parts_fit(catalog):
    result = catalog.find("vastus")
    assert isinstance(result, NoMatch) and result.reason == "ambiguous" and result.total == 3
    assert set(result.suggestions) == {"Vastus intermedius", "Vastus lateralis", "Vastus medialis"}
    many = catalog.find("extensor")
    assert isinstance(many, NoMatch) and many.reason == "ambiguous" and many.total > 3


# ---- speech-recognition misspellings ----------------------------------------------------------------------

@pytest.mark.parametrize("phrase, name", [
    ("bye steps", "Biceps brachii"),
    ("by steps", "Biceps brachii"),
    ("try steps", "Triceps brachii"),
    ("gastrocnemious", "Gastrocnemius"),
    ("gastrocnemius muscle", "Gastrocnemius"),
    ("soleous", "Soleus"),
    ("femer", "Femur"),
    ("tibea", "Tibia"),
    ("humorous", "Humerus"),
    ("humerous", "Humerus"),
    ("patela", "Patella (kneecap)"),
    ("calcaneous", "Calcaneus"),
    ("sartorious", "Sartorius"),
    ("semitendinosis", "Semitendinosus"),
    ("infraspinatis", "Infraspinatus"),
    ("brachio radialis", "Brachioradialis"),
    ("gluteus medias", "Gluteus medius"),
    ("ileacus", "Iliacus"),
    ("coxix", "Coccyx"),
    ("scapular", "Scapula (shoulder blade)"),
    ("so as", "Psoas major"),
    ("semi-dendenosis", "Semitendinosus"),        # heard by Whisper base.en from a synthetic voice
    ("Tibealis anterior", "Tibialis anterior"),   # same
])
def test_misheard(catalog, phrase, name):
    assert found(catalog, phrase)[1] == name


def test_a_bare_word_needs_a_closer_match_than_show_me(catalog):
    assert found(catalog, "femer")[1] == "Femur"                        # after "show me the ..."
    assert isinstance(catalog.find("femer", bare=True), NoMatch)        # the word alone: too risky
    assert found(catalog, "gastrocnemious", bare=True)[1] == "Gastrocnemius"


# ---- side ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("phrase, side", [
    ("left biceps", "left"),
    ("right biceps", "right"),
    ("biceps", ""),
    ("the biceps on the left side", "left"),
    ("right hand side femur", "right"),
    ("left and right biceps", ""),
    ("both biceps", ""),
    ("sternum", "center"),
    ("left sternum", "center"),              # a centre-only part stays "center"
    ("L3", "center"),
    ("right jaw", "center"),
    ("left hand", "left"),
])
def test_side(catalog, phrase, side):
    assert found(catalog, phrase)[2] == side


# ---- muscle or bone ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("phrase, kind, name", [
    ("shoulder", "muscle", "Deltoid (middle)"),
    ("shoulder bone", "bone", "Scapula (shoulder blade)"),
    ("thigh", "muscle", "Rectus femoris"),
    ("thigh muscle", "muscle", "Rectus femoris"),
    ("thigh bone", "bone", "Femur"),
    ("bones of the thigh", "bone", "Femur"),
    ("hip", "bone", "Hip bone (pelvis)"),
    ("hip muscle", "muscle", "Gluteus medius"),
    ("shin", "muscle", "Tibialis anterior"),
    ("shin bone", "bone", "Tibia"),
    ("calf bone", "bone", "Fibula"),
    ("neck muscle", "muscle", "Trapezius (upper)"),
    ("chest bone", "bone", "Sternum (breastbone)"),
])
def test_the_words_muscle_and_bone_decide_the_kind(catalog, phrase, kind, name):
    assert found(catalog, phrase)[:2] == (kind, name)


def test_prefer_kind_is_used_when_the_user_did_not_say(catalog):
    assert found(catalog, "thigh", prefer_kind="bone")[:2] == ("bone", "Femur")
    assert found(catalog, "thigh", prefer_kind="muscle")[:2] == ("muscle", "Rectus femoris")
    assert found(catalog, "thigh muscle", prefer_kind="bone")[:2] == ("muscle", "Rectus femoris")


def test_a_name_that_is_both_a_muscle_and_a_bone():
    both = Catalog.from_raw({"parts": [
        {"kind": "muscle", "name": "Tensor", "sides": ["left", "right"]},
        {"kind": "bone", "name": "Tensor", "sides": ["center"]},
    ]})
    assert found(both, "tensor muscle") == ("muscle", "Tensor", "")
    assert found(both, "left tensor bone") == ("bone", "Tensor", "center")
    assert found(both, "tensor")[0] == "muscle"                      # first in the catalog
    assert found(both, "tensor", prefer_kind="bone")[0] == "bone"


# ---- candidates for the LLM prompt ---------------------------------------------------------------------------------

def test_candidates_are_a_short_relevant_list(catalog):
    names = [part.name for part in catalog.candidates("what does the deltoid do when I lift my arm?")]
    assert 0 < len(names) <= 12
    assert {"Deltoid (middle)", "Deltoid (anterior)", "Deltoid (posterior)"} <= set(names)
    names = [part.name for part in catalog.candidates("that muscle at the back of the thigh")]
    assert "Biceps femoris (long head)" in names
    assert catalog.candidates("tell me a joke") == []


def test_mentions_anatomy():
    for text in ("show me the left biceps", "what does the deltoid do", "where is my hamstring", "L3",
                 "the third rib", "which muscle is that", "my pinky toe"):
        assert mentions_anatomy(text), text
    for text in ("pause", "zoom in", "go back", "what can you do", "play it faster", "hello there"):
        assert not mentions_anatomy(text), text


# ---- building a catalog from what the client sent ---------------------------------------------------------------------

def test_bad_catalogs_are_rejected_or_cleaned(monkeypatch):
    for raw in (None, [], "x", {}, {"parts": "x"}, {"parts": []}, {"parts": [1, "a", None]}):
        assert Catalog.from_raw(raw) is None
    cleaned = Catalog.from_raw({"parts": [
        {"kind": "muscle", "name": "  Biceps   brachii ", "sides": ["left", "up", 3], "region": 7},
        {"kind": "organ", "name": "Liver", "sides": ["center"]},
        {"kind": "bone", "name": "", "sides": ["center"]},
        {"kind": "bone", "name": "Femur"},
        {"kind": "bone", "name": "Femur", "sides": ["left"]},        # duplicate
        {"name": "No kind"},
    ]})
    assert [(part.kind, part.name, part.sides) for part in cleaned.parts] == [
        ("muscle", "Biceps brachii", ("left",)),
        ("bone", "Femur", ("center",)),
    ]
    monkeypatch.setattr(config.settings, "max_catalog_parts", 5)
    capped = Catalog.from_raw({"parts": [{"kind": "bone", "name": f"Bone {n}", "sides": ["center"]} for n in range(50)]})
    assert len(capped.parts) == 5


# ---- the store --------------------------------------------------------------------------------------------------------

def test_store_keeps_catalogs_in_memory_and_on_disk(tmp_path, catalog_raw):
    first = CatalogStore(tmp_path)
    assert first.get("9b1d5c0e") is None
    assert first.put("9b1d5c0e", catalog_raw) is not None
    assert len(first.get("9b1d5c0e").parts) == 214
    assert (tmp_path / "9b1d5c0e.json").is_file()

    after_restart = CatalogStore(tmp_path)                           # a new process: memory is empty
    assert len(after_restart.get("9b1d5c0e").parts) == 214
    assert after_restart.get("other") is None


def test_store_file_name_cannot_leave_the_cache_folder(tmp_path, catalog_raw):
    store = CatalogStore(tmp_path / "cache")
    assert store.safe_id("../../etc/passwd") == "etcpasswd"
    assert store.safe_id("a/b\\c:d e.json") == "abcdejson"
    assert store.safe_id(None) == "" and store.safe_id({"x": 1}) == "" and store.safe_id("../..") == ""
    store.put("../../outside", catalog_raw)
    store.put("..", catalog_raw)                                      # nothing left of the id: not stored
    written = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file())
    assert written == ["cache/outside.json"]
    assert len("x" * 500) > len(store.safe_id("x" * 500)) == 64


def test_store_ignores_a_broken_cache_file(tmp_path):
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "empty.json").write_text(json.dumps({"parts": []}), encoding="utf-8")
    store = CatalogStore(tmp_path)
    assert store.get("broken") is None and store.get("empty") is None


def test_store_does_not_ask_twice_in_a_row(tmp_path, catalog_raw):
    store = CatalogStore(tmp_path)
    assert store.may_ask("abc") is True
    assert store.may_ask("abc") is False                               # never twice in a row for one id
    assert store.may_ask("abc") is True
    assert store.may_ask("other") is True
    store.put("abc", catalog_raw)
    assert store.get("abc") is not None
