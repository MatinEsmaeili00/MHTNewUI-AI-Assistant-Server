# MHTNewUI AI Assistant — server

The server behind the BOT chat of the Avatar Injury Prevention app (Unreal Engine). It runs on an NVIDIA DGX
Spark. The app sends what the user typed, or said while holding the BOT logo, together with the avatar's
current state; the server answers with a short reply and a list of actions (switch layer, timeline, camera,
pose estimation, "show me the left biceps"), which the app runs.

```
Unreal (Mac / iPad, anywhere)                      DGX Spark (Louisiana)
  chat box or held BOT logo                          server.py  :8010
        │  POST /v1/chat   {text, context}  ───────►   rules  ──►  LLM (Ollama)
        │  POST /v1/voice  wav + context    ───────►   Whisper ─► rules ─► LLM
        │  ◄───────  {transcript, reply, actions[]}
        ▼
  runs every action on the avatar, then shows `reply` in the chat box
```

| Read | For |
|---|---|
| [docs/HANDOFF.md](docs/HANDOFF.md) | what exists, what was tested where, what to do next on the Spark |
| [docs/TODO.md](docs/TODO.md) | what is done and what is open, for both machines |
| [docs/SPARK_NOTES.md](docs/SPARK_NOTES.md) | what was measured and decided on the Spark |
| [docs/PROTOCOL.md](docs/PROTOCOL.md) | the contract between the app and the server |
| [docs/NETWORK.md](docs/NETWORK.md) | reaching the Spark from another city |
| [docs/SETUP_SPARK.md](docs/SETUP_SPARK.md) | installing and running the server on the Spark, step by step |
| [docs/SERVER.md](docs/SERVER.md) | how a request is processed, how to add a command or a name, how to change the model |

The Unreal side: <https://github.com/MatinEsmaeili00/MHTNewUI-AI-Assistant-Unreal>

## How it answers

1. **Rules** (`assistant/rules.py`). Every direct command is understood by patterns, in about a millisecond,
   with no language model. This is what the demo depends on, and it works with Ollama stopped.
2. **Language model** (`assistant/llm.py`, Ollama). Only for what the rules do not understand: questions about
   anatomy, small talk, vague requests. It must answer in a fixed JSON shape.
3. **Validation** (`assistant/actions.py`). Every action, from the rules or from the model, is checked against
   the protocol before it leaves: unknown action types are dropped, numbers are clamped, a part name is replaced
   by the exact name in the avatar's catalog (or the action is dropped), an action clip must be one the avatar
   has.

Voice is the same after one extra step: faster-whisper turns the WAV into text.

## Quick start

### On the DGX Spark

The full walk-through, including the service, is [docs/SETUP_SPARK.md](docs/SETUP_SPARK.md). In short:

```bash
cd ~/MHTNewUI-AI-Assistant-Server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest tests -q            # must pass before anything else
ollama pull gemma4:e4b
echo "ASSISTANT_API_KEY=$(openssl rand -hex 24)" >> .env
./run.sh                                       # http://<spark>:8010/health
```

### On a laptop (no Ollama, no speech recognition needed)

```bash
python3 -m venv .venv                          # Python 3.10 or newer
.venv/bin/pip install -r requirements-dev.txt  # no faster-whisper: /v1/voice answers 503, text works
.venv/bin/python -m pytest tests -q
FAKE_LLM=1 ./run.sh                            # FAKE_LLM=1: the "LLM" echoes the sentence, no Ollama needed
```

In a second terminal:

```bash
.venv/bin/python tools/client.py --health
.venv/bin/python tools/client.py "show me the left biceps" --catalog
.venv/bin/python tools/client.py "start the pose estimation and read the data" --pose-off
.venv/bin/python tools/client.py clip.wav --catalog        # needs requirements.txt (faster-whisper)
```

`tools/client.py` sends the example context of `docs/PROTOCOL.md` and, with `--catalog`, the real catalog in
`tests/fixtures/catalog.json`. `--url` and `--key` point it at another machine.

## What you can say

All of this is answered by the rules. Words like "hey bot", "please", "can you", "okay" are ignored. Several
commands can be joined with "and", "then" or a comma ("switch to muscles, zoom in a lot and play").

| Area | Examples | Action |
|---|---|---|
| Layers | "switch to the muscle layer", "show me the bones", "skeleton", "x-ray", "skin", "back to the body" | `set_layer` |
| Play / pause | "play", "resume", "pause", "stop", "freeze", "restart", "from the beginning", "play it again" | `timeline` |
| Speed | "faster", "slow down a lot", "speed up twice", "play it faster", "half speed", "2x", "double speed", "normal speed", "twice as fast" | `timeline` faster / slower / set_speed |
| Position | "go to the middle", "go to 30 percent", "go to the start", "go to the end", "step forward", "step back", "next frame", "go back 2 seconds" | `timeline` seek / step |
| Markers, recording | "bookmark this", "next marker", "start recording", "stop recording" | `timeline` |
| Zoom | "zoom in", "zoom in a bit", "zoom in a lot", "closer", "farther", "zoom all the way out", "zoom to 50 percent" | `camera` zoom_in / zoom_out / zoom_to |
| Turn | "rotate left", "turn right 90 degrees", "orbit left a little", "turn around", "tilt up" | `camera` orbit |
| View | "from behind", "from the front", "from the left side", "from above", "reset the view" | `camera` view / reset |
| Pose estimation | "activate realtime pose estimation", "stop pose tracking", "hide the overlay", "read the data", "what are the angles" | `pose_estimation`, `read_pose_data` |
| Angles | "what is the right knee angle", "what's the knee angle" | answered from `context.pose.angles`; if pose estimation is off: switch it on and read |
| Muscles and bones | "show me the left biceps", "highlight the femur", "where is the gastrocnemius", "point to the kneecap", "find the L3", "third rib", just "biceps", "clear the highlight" | `show_part`, `clear_selection` |
| Common names | "hamstring", "quads", "calf", "glutes", "pecs", "traps", "ACL", "IT band", "shin bone", "collarbone", "tailbone", "pinky", "big toe" | `show_part` with the catalog name |
| Regions | "the knee", "the shoulder", "the spine", "the skull" | `show_part` with one representative part; the reply says which |
| Follow-up | "the other side", "now the right one" (with a part highlighted) | `show_part` on the other side |
| Action clips | "make her run", "jog", "tennis forehand", "idle", "stop running" | `play_action` (only clips in `context.action.available`) |
| Panels | "open the graphs", "show saved videos", "settings", "close the panel" | `open_panel` |
| Avatar | "next avatar", "switch character", "previous avatar" | `switch_avatar` |
| State | "what layer am I on", "how fast is it playing", "is it recording", "what is selected", "what actions are there", "did that work" | answered from the context, no action |

Not in this avatar's catalog (the reply says so and offers nearby parts): lats, Achilles tendon, abs, meniscus.
A word that is not a part ("show me the fibia") gets "I do not know a part called ..." with the closest names.

Everything else ("what does the deltoid do?", "hello", "what can you do") goes to the language model, which
is asked to answer in two or three sentences without actions. If Ollama does not answer, the reply says so and
lists a few direct commands; `source` is then `error`.

## Configuration

Environment variables, or a `.env` file in this folder (`run.sh` reads it; see `.env.example`).

| Variable | Default | Meaning |
|---|---|---|
| `ASSISTANT_API_KEY` | empty | clients must send `Authorization: Bearer <key>`. Empty = no authentication, and `/health` says `auth_required: false` |
| `PORT` | `8010` | HTTP port (`run.sh` listens on `0.0.0.0`) |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama server |
| `LLM_MODEL` | `gemma4:e4b` | any model from `ollama list`. The Spark's `.env` has `gemma4:12b` |
| `LLM_TIMEOUT_S` | `25` | timeout for one LLM call (the app waits 30 s for the whole answer) |
| `LLM_KEEP_ALIVE` | `1h` | how long Ollama keeps the model in memory after a request. The Spark's `.env` has `24h` |
| `LLM_WARMUP` | `1` | `0` = do not load the model when the server starts |
| `FAKE_LLM` | `0` | `1` = no Ollama: the "LLM" echoes the sentence. For testing the HTTP path |
| `STT_MODEL` | `base.en` | faster-whisper model (`tiny.en`, `base.en`, `small.en`, ...) |
| `STT_DEVICE` | `cpu` | CTranslate2 device |
| `STT_COMPUTE` | `int8` | CTranslate2 compute type |
| `STT_MODEL_DIR` | empty | where the Whisper model is stored. Empty = the Hugging Face cache in the home folder |
| `STT_PROMPT` | empty | words that bias Whisper towards the app's vocabulary (`initial_prompt`). See `.env.example` |
| `SAVE_LAST_AUDIO` | `1` | keep the last uploaded clip as `last_voice.wav` in this folder |
| `CATALOG_CACHE_DIR` | `.cache/catalogs` | where catalogs are cached as JSON, one file per `catalog_id` |
| `SESSION_TURNS` | `6` | how many past turns per session the language model sees |

Fixed limits (in `assistant/config.py`): audio 20 MB, JSON body 2 MB, text 2000 characters, catalog 2000 parts,
8 actions per answer, 200 sessions in memory.

## Repository layout

```
server.py              FastAPI app: GET /health, POST /v1/chat, POST /v1/voice, key check, size limits, log line
assistant/
  config.py            settings from environment variables
  text.py              normalising text (lower case, spoken numbers to digits)
  context.py           safe access to the request's context (every field may be missing)
  catalog.py           the catalog store (memory + JSON cache) and the matching of part names
  rules.py             the rule-based parser
  actions.py           action builders and the validation of every action
  llm.py               the Ollama fallback: prompt, JSON schema, warm-up
  stt.py               faster-whisper, imported only when needed
  pipeline.py          text + context + catalog -> answer (used by both endpoints)
  sessions.py          short conversation history per session, for the LLM
tests/                 pytest; needs neither Ollama nor faster-whisper
  fixtures/catalog.json  the avatar's real catalog: 93 muscles and ligaments, 121 bones
tools/client.py        send a sentence or a .wav to a running server
tools/acceptance.py    run the acceptance table against a running server, with times (--url for another machine)
tools/stt_bench.py     compare speech models on a folder of clips; tools/stt_phrases.txt is its phrase list
run.sh                 starts uvicorn on 0.0.0.0:$PORT (default 8010), reads .env
deploy/mht-assistant.service   systemd user unit
deploy/install-service.sh      installs the unit for the folder the repository is in, and (re)starts it
requirements.txt       the server, with speech recognition
requirements-dev.txt   tests and the text-only server
docs/                  PROTOCOL, HANDOFF, TODO, SPARK_NOTES, NETWORK, SETUP_SPARK, SERVER
```

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests -q
```

716 tests, a few seconds. They need no Ollama and no faster-whisper: the call to Ollama and the transcription
are replaced by fakes, and a test fails if it reaches for Ollama without saying so.

| File | What it checks |
|---|---|
| `tests/test_rules.py` | 220 phrasings and the actions they must give (every example of PROTOCOL.md, the acceptance table of HANDOFF.md, every action type, several commands in one sentence, speech-recognition style input), replies, questions answered from the context, missing or broken context, hostile input |
| `tests/test_catalog.py` | part matching against the real catalog: exact names, common names, misspellings, side, muscle or bone, unknown parts, the catalog store and its cache files |
| `tests/test_actions.py` | validation: unknown types, bad enums, numbers out of range, part names and action clips that do not exist |
| `tests/test_api.py` | the HTTP API: `/health`, the key, `/v1/chat`, the `need_catalog` flow, `/v1/voice` with a fake transcription, 503 without faster-whisper, size limits |
| `tests/test_llm.py` | the LLM path with a fake Ollama: valid answers, invented names, invalid JSON, timeout, Ollama down, the prompt (part names, follow-ups), sides the user did not say, the warm-up retry |

## Status, 2026-11-11

**On the DGX Spark** (later the same day; numbers in [docs/SPARK_NOTES.md](docs/SPARK_NOTES.md)):

- Installed, 716 tests pass, runs as the systemd user service `mht-assistant` on port 8010.
- The language model has now answered for real: `gemma4:12b`, every answer valid against the protocol, about
  1.5 s. The prompt was tuned on 30 questions and vague requests.
- The acceptance table of `docs/HANDOFF.md` passes, text and synthetic speech, through the Tailscale address.
- The Spark and the Mac are on one Tailscale network and reach each other directly.
- Still open: a request from the Mac, the app against the Spark, real voices, a reboot. See
  [docs/TODO.md](docs/TODO.md).

**On the Mac** (earlier):

Written and tested on a Mac (Apple silicon, Python 3.12.16; fastapi 0.162.4, starlette 1.10.0, pydantic 2.14.3,
uvicorn 0.54.0, faster-whisper 1.2.1, ctranslate2 4.9.0, av 17.1.0).

Tested on that Mac:

- The test suite: 698 passed.
- The real server on port 8010 with `tools/client.py`: text requests and the catalog cache on disk.
- Speech recognition with faster-whisper `base.en` on the CPU and 33 synthetic clips made with macOS `say`
  (16-bit mono, 48 kHz, the format the app sends), plus silence, noise, a hum and clicks. Whisper wrote 26 of
  the 33 clips exactly. Of the other 7, the fuzzy name matching recovered 3 ("Tibealis anterior",
  "brachio AD ALICE", "PSO S major"). "Cause" (for "Pause."), "semi-dendenosis" and "lads" (for "lats") gave
  no action in the first run, were added to the tables, and gave the right answer in the second run. "buns"
  (for "Bones.") was added after the last voice run and is covered by a rule test only. Silence and noise gave
  "I did not catch that." About 0.3 s per clip on that machine.
- The same clips with `STT_PROMPT` set (the line in `.env.example`): 31 of 33 exact, but "psoas major" became
  "soleus major" and got no action. It is off by default.
- With the Unreal app, on the same Mac (`127.0.0.1`): 29 typed requests and 3 spoken ones (clips uploaded by
  the app itself), the `need_catalog` repeat over real HTTP, a wrong and a right key. The app ran every action
  that came back. See the Unreal repository's `docs/03-build-and-test.md`.
- Ollama was not installed there. Sentences for the language model got the "not answering" reply
  (`source: error`), which is the intended behaviour with Ollama down.

Not tested by anyone yet:

- **Real voices and a real microphone**: only synthetic speech was used, on both machines.
- **The app against the Spark**: the app has only talked to a server on `127.0.0.1`; the Spark's server has only
  been called from the Spark (through its Tailscale address).
- **A reboot of the Spark** with the service enabled.
