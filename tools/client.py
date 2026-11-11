#!/usr/bin/env python3
"""
Command-line client for the assistant server: sends a sentence or a .wav file the way the Unreal
client does and prints the answer.

    python tools/client.py "show me the left biceps" --catalog
    python tools/client.py clip.wav --catalog
    python tools/client.py --health
    python tools/client.py "what is the right knee angle" --context my_context.json
    python tools/client.py "pause" --url http://spark.example.org:8010 --key SECRET

--catalog sends tests/fixtures/catalog.json with the request (the real client sends the catalog in
the first request of a session). Without it only `catalog_id` is sent; if the server then answers
`need_catalog`, this tool repeats the request with the catalog, like the real client.
The API key can also come from the environment variable ASSISTANT_API_KEY.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

import requests

REPO_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG = REPO_DIR / "tests" / "fixtures" / "catalog.json"

# The example context of docs/PROTOCOL.md.
SAMPLE_CONTEXT = {
    "avatar": {"name": "BP_Abby8", "index": 0, "count": 3},
    "layer": "body",
    "timeline": {
        "playing": True, "speed": 1.0, "speed_steps": [0.25, 0.5, 1, 1.5, 2, 3],
        "time": 0.42, "cycle_seconds": 1.1, "recording": False,
    },
    "action": {"playing": "", "available": ["Idle", "Running", "Jogging", "TennisForehand"]},
    "panel": "none",
    "camera": {"distance": 300.0, "min": 50.0, "max": 1200.0, "moved": False},
    "pose": {
        "status": "running", "overlay": True, "panel_open": True, "recording": False, "fps": 11.8,
        "person": True, "visible_side": "right",
        "angles": {"right knee": 142.3, "left knee": 95.0, "trunk": 84.1},
    },
    "chat_open": True,
    "last_results": [],
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a sentence or a .wav file to the assistant server.")
    parser.add_argument("input", nargs="?", help='a sentence ("zoom in") or the path of a .wav file')
    parser.add_argument("--url", default=os.getenv("ASSISTANT_URL", "http://localhost:8010"))
    parser.add_argument("--key", default=os.getenv("ASSISTANT_API_KEY", ""), help="API key (Bearer token)")
    parser.add_argument("--catalog", action="store_true", help="send the catalog with the request")
    parser.add_argument("--catalog-file", default=str(DEFAULT_CATALOG), help="catalog JSON to use")
    parser.add_argument("--context", help="JSON file with the context (default: the PROTOCOL.md example)")
    parser.add_argument("--pose-off", action="store_true", help="sample context with pose estimation off")
    parser.add_argument("--session", default=uuid.uuid4().hex, help="session_id (default: a new one)")
    parser.add_argument("--health", action="store_true", help="only call GET /health")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    url = args.url.rstrip("/")
    headers = {"Authorization": f"Bearer {args.key}"} if args.key else {}

    if args.health:
        response = requests.get(f"{url}/health", timeout=args.timeout)
        return show(response)
    if not args.input:
        parser.error("give a sentence or a .wav file (or --health)")

    context = json.loads(Path(args.context).read_text(encoding="utf-8")) if args.context else dict(SAMPLE_CONTEXT)
    if args.pose_off:
        context["pose"] = {"status": "off", "overlay": False, "panel_open": False, "recording": False}

    catalog_text = Path(args.catalog_file).read_text(encoding="utf-8")
    catalog = json.loads(catalog_text)
    body = {
        "protocol": 1,
        "session_id": args.session,
        "context": context,
        "catalog_id": hashlib.sha1(catalog_text.encode("utf-8")).hexdigest()[:8],
    }
    if args.catalog:
        body["catalog"] = catalog

    is_audio = args.input.lower().endswith(".wav") and Path(args.input).is_file()
    if is_audio:
        with open(args.input, "rb") as audio:
            response = requests.post(
                f"{url}/v1/voice",
                headers=headers,
                files={"audio": ("speech.wav", audio, "audio/wav")},
                data={"request": json.dumps(body)},
                timeout=args.timeout,
            )
    else:
        response = requests.post(f"{url}/v1/chat", headers=headers, json={**body, "text": args.input},
                                 timeout=args.timeout)
    status = show(response)

    # Like the real client: the server does not know this catalog -> repeat as /v1/chat with it.
    if response.status_code == 200 and response.json().get("need_catalog"):
        print("-> need_catalog: repeating as /v1/chat with the catalog")
        again = {**body, "text": response.json().get("transcript", ""), "catalog": catalog}
        status = show(requests.post(f"{url}/v1/chat", headers=headers, json=again, timeout=args.timeout))
    return status


def show(response: requests.Response) -> int:
    try:
        print(json.dumps(response.json(), indent=2, ensure_ascii=False))
    except ValueError:
        print(response.text)
    if response.status_code != 200:
        print(f"HTTP {response.status_code}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
