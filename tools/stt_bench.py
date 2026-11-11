#!/usr/bin/env python3
"""
Measures speech recognition on a folder of clips: how often does a spoken phrase give the same
actions as the typed phrase, and how long does a clip take?

    python tools/stt_bench.py CLIPS_DIR --models base.en small.en
    python tools/stt_bench.py CLIPS_DIR --models small.en --prompt          # with STT_PROMPT of .env.example
    python tools/stt_bench.py CLIPS_DIR --models small.en --device cuda --compute float16

CLIPS_DIR has clips named `NN_<anything>.wav`, where NN is the line number of the phrase in the phrase
list, starting at 00 (`12_matin.wav` = somebody saying line 12, "Show me the left biceps."). The phrase list
is `phrases.txt` in CLIPS_DIR if there is one, else tools/stt_phrases.txt (36 phrases: the acceptance table
and the anatomical names). Record real voices the same way and run this again.

It does not need a running server: it loads faster-whisper itself and runs the transcript through the
rules (assistant/rules.py) with the catalog of tests/fixtures/catalog.json. A phrase the rules do not
understand when typed is skipped (it would go to the language model).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from assistant import rules  # noqa: E402
from assistant.catalog import Catalog  # noqa: E402
from client import SAMPLE_CONTEXT  # noqa: E402


def env_example_prompt() -> str:
    """The commented STT_PROMPT line of .env.example."""
    for line in (REPO_DIR / ".env.example").read_text(encoding="utf-8").splitlines():
        match = re.match(r'#\s*STT_PROMPT="(.+)"\s*$', line)
        if match:
            return match.group(1)
    return ""


def outcome(text: str, catalog: Catalog):
    """What the rules do with a sentence, in a form that can be compared."""
    result = rules.parse(text, SAMPLE_CONTEXT, catalog)
    if result is None:
        return None
    return json.dumps(result.actions, sort_keys=True) if result.actions else "reply: " + result.reply


def plain(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", text.lower()).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare speech-recognition models on a folder of clips.")
    parser.add_argument("clips", help="folder with phrases.txt and NN_*.wav")
    parser.add_argument("--models", nargs="+", default=["base.en"])
    parser.add_argument("--phrases", default="", help="phrase list (default: CLIPS_DIR/phrases.txt, else "
                                                      "tools/stt_phrases.txt)")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute", default="int8")
    parser.add_argument("--prompt", action="store_true", help="use the STT_PROMPT line of .env.example")
    parser.add_argument("--prompt-text", default="", help="use this text as the prompt")
    parser.add_argument("--beam", type=int, default=5)
    parser.add_argument("--show-all", action="store_true", help="print every clip, not only the wrong ones")
    args = parser.parse_args()

    from faster_whisper import WhisperModel

    clips_dir = Path(args.clips)
    phrase_file = Path(args.phrases) if args.phrases else clips_dir / "phrases.txt"
    if not phrase_file.is_file():
        phrase_file = Path(__file__).resolve().parent / "stt_phrases.txt"
    phrases = [line.strip() for line in phrase_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    catalog = Catalog.from_raw(json.loads((REPO_DIR / "tests" / "fixtures" / "catalog.json").read_text("utf-8")))
    expected = [outcome(phrase, catalog) for phrase in phrases]
    clips = sorted(clip for clip in clips_dir.glob("*.wav") if re.match(r"\d+_", clip.name))
    prompt = args.prompt_text or (env_example_prompt() if args.prompt else "")
    skipped = [phrase for phrase, want in zip(phrases, expected) if want is None]
    if skipped:
        print("Skipped (the rules do not understand them when typed):", skipped)

    for name in args.models:
        started = time.perf_counter()
        model = WhisperModel(name, device=args.device, compute_type=args.compute)
        load_s = time.perf_counter() - started
        exact = same = total = 0
        times: list[float] = []
        wrong: list[str] = []
        for clip in clips:
            index = int(clip.name.split("_")[0])
            if index >= len(phrases) or expected[index] is None:
                continue
            started = time.perf_counter()
            segments, _info = model.transcribe(str(clip), language="en", beam_size=args.beam, vad_filter=True,
                                               initial_prompt=prompt or None)
            heard = " ".join(segment.text.strip() for segment in segments).strip()
            times.append(time.perf_counter() - started)
            total += 1
            is_exact = plain(heard) == plain(phrases[index])
            is_same = outcome(heard, catalog) == expected[index]
            exact += is_exact
            same += is_same
            if args.show_all or not is_same:
                wrong.append(f"  {'ok  ' if is_same else 'MISS'} {clip.name:28} said {phrases[index]!r} heard {heard!r}")
        times.sort()
        label = f"{name} ({args.device}, {args.compute}{', prompt' if prompt else ''})"
        print(f"\n{label}: loaded in {load_s:.1f} s, {total} clips")
        print(f"  same actions as typing: {same}/{total} ({100 * same / max(total, 1):.0f} %)   "
              f"transcript exact: {exact}/{total}")
        if times:
            print(f"  seconds per clip: median {times[len(times) // 2]:.2f}, slowest {times[-1]:.2f}")
        print("\n".join(wrong), flush=True)        # one model can take minutes: show each as it finishes
    return 0


if __name__ == "__main__":
    sys.exit(main())
