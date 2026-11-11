#!/usr/bin/env python3
"""
Runs the acceptance table of docs/HANDOFF.md ("Phrases that must work") against a running server and
prints one line per phrase: PASS / FAIL, who answered (rules or llm), the server time and the round trip.

    python tools/acceptance.py                                   # the server on this machine
    python tools/acceptance.py --url http://100.x.y.z:8010       # from the Mac, across the network
    python tools/acceptance.py --extra                           # also the language-model sentences

The key comes from --key or the environment variable ASSISTANT_API_KEY. The round-trip column minus the
server column is the network. Exit code 0 = every phrase of the table passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from client import DEFAULT_CATALOG, SAMPLE_CONTEXT  # noqa: E402


def has(action_type: str, **fields):
    """A check: the answer has an action of this type with these fields."""
    def check(answer: dict) -> bool:
        return any(
            action.get("type") == action_type and all(action.get(k) == v for k, v in fields.items())
            for action in answer["actions"]
        )
    return check


def all_of(*checks):
    return lambda answer: all(check(answer) for check in checks)


def no_action_reply_has(*words: str):
    return lambda answer: not answer["actions"] and all(w.lower() in answer["reply"].lower() for w in words)


def spoken_answer(answer: dict) -> bool:
    """A language-model answer to a question: no action, one to three sentences."""
    return answer["source"] == "llm" and not answer["actions"] and 20 <= len(answer["reply"]) <= 600


# (sentence, check, pose estimation off in the context)
TABLE = [
    ("switch to the muscle layer", has("set_layer", layer="muscles"), False),
    ("show me the skeleton", has("set_layer", layer="skeleton"), False),
    ("pause", has("timeline", op="pause"), False),
    ("play", has("timeline", op="play"), False),
    ("make it faster", has("timeline", op="faster"), False),
    ("half speed", has("timeline", op="set_speed", value=0.5), False),
    ("zoom in", has("camera", op="zoom_in"), False),
    ("zoom out a lot", has("camera", op="zoom_out", value=3), False),
    ("look from behind", has("camera", op="view", value="back"), False),
    ("reset the view", has("camera", op="reset"), False),
    ("activate realtime pose estimation and read the data",
     all_of(has("pose_estimation", enabled=True), has("read_pose_data")), True),
    ("what is the right knee angle", no_action_reply_has("142"), False),
    ("show me the left biceps", has("show_part", kind="muscle", name="Biceps brachii", side="left"), False),
    ("highlight the femur", has("show_part", kind="bone", name="Femur"), False),
    ("show me the hamstring", has("show_part", name="Biceps femoris (long head)"), False),
    ("what does the deltoid do", spoken_answer, False),
    ("make her run", has("play_action", name="Running"), False),
]

# Sentences the rules do not cover: they must come back from the language model, valid and short.
EXTRA = [
    ("hello, what can you do?", spoken_answer, False),
    ("why do runners get shin splints?", spoken_answer, False),
    ("which muscle bends the elbow?", lambda a: a["source"] == "llm" and len(a["reply"]) >= 20, False),
    ("is my right knee angle normal for running?", lambda a: a["source"] == "llm" and len(a["reply"]) >= 20, False),
    ("can you point out the muscle runners pull at the back of the thigh",
     lambda a: a["source"] == "llm" and (has("show_part")(a) or len(a["reply"]) >= 20), False),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the acceptance table against a running server.")
    parser.add_argument("--url", default=os.getenv("ASSISTANT_URL", "http://localhost:8010"))
    parser.add_argument("--key", default=os.getenv("ASSISTANT_API_KEY", ""))
    parser.add_argument("--extra", action="store_true", help="also send the language-model sentences")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    url = args.url.rstrip("/")
    headers = {"Authorization": f"Bearer {args.key}"} if args.key else {}
    catalog_text = Path(DEFAULT_CATALOG).read_text(encoding="utf-8")
    catalog_id = hashlib.sha1(catalog_text.encode("utf-8")).hexdigest()[:8]
    session = uuid.uuid4().hex

    started = time.perf_counter()
    health = requests.get(f"{url}/health", timeout=args.timeout).json()
    print(f"{url}  health {round((time.perf_counter() - started) * 1000)} ms  "
          f"llm_ready={health.get('llm_ready')} stt_ready={health.get('stt_ready')} "
          f"llm={health.get('llm_model')} stt={health.get('stt_model')}")

    failed = 0
    first = True
    for sentence, check, pose_off in TABLE + (EXTRA if args.extra else []):
        context = dict(SAMPLE_CONTEXT)
        if pose_off:
            context["pose"] = {"status": "off", "overlay": False, "panel_open": False, "recording": False}
        body = {"protocol": 1, "session_id": session, "text": sentence, "context": context, "catalog_id": catalog_id}
        if first:                       # like the app: the catalog goes with the first request of a session
            body["catalog"] = json.loads(catalog_text)
            first = False
        started = time.perf_counter()
        response = requests.post(f"{url}/v1/chat", headers=headers, json=body, timeout=args.timeout)
        round_trip = round((time.perf_counter() - started) * 1000)
        if response.status_code != 200:
            print(f"FAIL  HTTP {response.status_code}  {sentence!r}  {response.text[:120]}")
            failed += 1
            continue
        answer = response.json()
        ok = bool(check(answer))
        failed += not ok
        actions = json.dumps(answer["actions"], ensure_ascii=False)
        print(f"{'PASS' if ok else 'FAIL'}  {answer['source']:5} server {answer['latency_ms']:5} ms  "
              f"round trip {round_trip:5} ms  {sentence!r}\n      -> {actions}  | {answer['reply']}")
    print(f"\n{'ALL PASSED' if not failed else f'{failed} FAILED'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
