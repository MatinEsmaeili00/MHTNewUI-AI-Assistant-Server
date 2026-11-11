"""
Text normalisation shared by the rules and the catalog.

`normalize("Show me the LEFT biceps, please!")` -> "show me the left biceps please"

Spoken numbers become digits ("forty five" -> "45", "one and a half" -> "1.5", "L three" -> "l 3")
and "3rd" becomes "third", so one set of patterns covers typed and spoken text.
"""

from __future__ import annotations

import re
import unicodedata

UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
    "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12,
}
ORDINAL_WORDS = {number: word for word, number in ORDINALS.items()}

# "the other one", "this one", "the left one": here "one" is not a number.
_ONE_IS_A_PRONOUN_AFTER = {
    "other", "this", "that", "which", "left", "right", "same", "next", "previous", "last", "first", "another",
    "no", "the", "any", "every", "each",
}


def _ordinal_digits(match: re.Match) -> str:
    number = int(match.group(1))
    return ORDINAL_WORDS.get(number, match.group(1))


def _number_at(tokens: list[str], i: int) -> tuple[float | None, int]:
    """Reads a spoken number that starts at tokens[i]. Returns (value, tokens used) or (None, 0)."""
    word = tokens[i]
    following = tokens[i + 1] if i + 1 < len(tokens) else ""
    after_that = tokens[i + 2] if i + 2 < len(tokens) else ""

    # "one hundred eighty", "a hundred and eighty", "two hundred"
    if following == "hundred" and (word in UNITS or word in ("a", "an")):
        value = (UNITS.get(word, 1) or 1) * 100
        used = 2
        rest = i + 2
        if rest < len(tokens) and tokens[rest] == "and":
            rest += 1
        tail, tail_used = _below_hundred(tokens, rest)
        if tail is not None:
            value += tail
            used = rest - i + tail_used
        return value, used

    # "one eighty" = 180, "three sixty" = 360
    if word in UNITS and 1 <= UNITS[word] <= 9 and following in TENS:
        value = UNITS[word] * 100 + TENS[following]
        if after_that in UNITS and 1 <= UNITS[after_that] <= 9:
            return value + UNITS[after_that], 3
        return value, 2

    return _below_hundred(tokens, i)


def _below_hundred(tokens: list[str], i: int) -> tuple[float | None, int]:
    if i >= len(tokens):
        return None, 0
    word = tokens[i]
    following = tokens[i + 1] if i + 1 < len(tokens) else ""
    if word in TENS:
        if following in UNITS and 1 <= UNITS[following] <= 9:
            return TENS[word] + UNITS[following], 2
        return TENS[word], 1
    if word in UNITS:
        return UNITS[word], 1
    return None, 0


def _format(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def words_to_numbers(text: str) -> str:
    tokens = text.split()
    out: list[str] = []
    i = 0
    while i < len(tokens):
        word = tokens[i]
        previous = out[-1] if out else ""
        if word == "one" and previous in _ONE_IS_A_PRONOUN_AFTER and (i + 1 == len(tokens) or tokens[i + 1] == ","):
            out.append(word)
            i += 1
            continue
        value, used = _number_at(tokens, i)
        if value is None:
            out.append(word)
            i += 1
        else:
            out.append(_format(value))
            i += used
    text = " ".join(out)

    # "1 and a half" -> 1.5, "0 point 5" -> 0.5, "point 5" -> 0.5
    text = re.sub(r"\b(\d+) and a half\b", lambda m: _format(int(m.group(1)) + 0.5), text)
    text = re.sub(r"\b(\d+) point (\d+)\b", r"\1.\2", text)
    text = re.sub(r"\bpoint (\d+)\b", r"0.\1", text)
    return text


def normalize(text: object, commas: bool = False) -> str:
    """Lower case, no punctuation, digits for numbers. With commas=True, sentence breaks stay as ' , '."""
    if not isinstance(text, str):
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = text.replace("°", " degrees ").replace("%", " percent ").replace("&", " and ")
    text = text.replace("'", "").replace("’", "").replace("`", "")
    text = re.sub(r"(?<=\d)[.,](?=\d)", "\x00", text)          # keep the dot inside "1.5" (and "1,5")
    text = re.sub(r"[,;.!?\n]+", " , ", text)
    text = text.replace("\x00", ".")
    text = re.sub(r"[^a-z0-9., ]", " ", text)
    text = re.sub(r"\bper cent\b", "percent", text)
    text = re.sub(r"\b(\d+)(?:st|nd|rd|th)\b", _ordinal_digits, text)
    text = re.sub(r"(?<=\d)(?=(?:x|percent|degrees?|deg|seconds?|secs?|frames?)\b)", " ", text)   # "2x" -> "2 x"
    text = words_to_numbers(re.sub(r"\s+", " ", text).strip())
    if not commas:
        text = text.replace(",", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip(", ").strip() if commas else text


def to_float(value: object, default: float | None = None) -> float | None:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return number


def tidy_number(value: float, digits: int = 3):
    """2.0 -> 2, 0.5 -> 0.5 (what the protocol examples look like)."""
    value = round(float(value), digits)
    return int(value) if value.is_integer() else value


def join_names(names: list[str], word: str = "and") -> str:
    names = [name for name in names if name]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + f" {word} " + names[-1]
