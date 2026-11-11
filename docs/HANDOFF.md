# Hand-off: what exists, what the Spark session does

Written 2026-11-11 on the Mac that has the Unreal project. Read this first on the DGX Spark.

## The whole thing in five lines

- An Unreal Engine app (the "Avatar Injury Prevention" viewer: a MetaHuman with body / muscle / skeleton layers,
  an animation timeline, real-time pose estimation, and a chat box with an animated BOT logo) sends what the user
  types, or says while holding the BOT logo, to a server.
- The server (this repository, on the DGX Spark) turns it into a short reply plus a list of actions.
- Unreal runs the actions (switch layer, play / pause / speed, camera, pose estimation, "show me the left biceps")
  and writes the reply into the chat box.
- The two sides only share `docs/PROTOCOL.md`. Read it completely before touching code.
- The app will be used in Los Angeles while the Spark stays in Louisiana: see `docs/NETWORK.md`.

## The two repositories

| Repository | What | Where it runs |
|---|---|---|
| `MatinEsmaeili00/MHTNewUI-AI-Assistant-Server` (this one) | the server: rules, speech recognition, language model | DGX Spark |
| `MatinEsmaeili00/MHTNewUI-AI-Assistant-Unreal` | the Unreal side: C++ source, docs, test notes | the Mac / iPad with the app |

Both are private: `gh auth login` on the Spark before cloning.

## State of the Unreal side (done, on the Mac)

Built with Unreal 5.8 and tested in Play-In-Editor on 2026-11-11, without any server:

- `UIHPCAssistantComp` on the player controller: builds the request (context + catalog), queues requests one at a
  time, runs every action type of the protocol, shows replies and failures in the chat box.
- Every action type was run from the console (`Assistant.Do ...`) and checked on screen or in the log.
- Holding the BOT logo turns it red, records the microphone, uploads a 16-bit mono WAV to `/v1/voice` on release.
  A short press still just opens / closes the chat.
- A click on a muscle or bone writes its description into the chat box (the old popup is off).
- The avatar's catalog is 214 names: 93 muscles and ligaments, 121 bones (`tests/fixtures/catalog.json` is that
  exact catalog).
- Server address and key are set in the app's chat box: `/server http://<host>:8010`, `/key <text>`, `/status`.

What the Unreal side has NOT been tested against yet: a real server over the network, real speech through
Whisper, and the language model. That is the point of the work below.

## State of the server

The server code is being written and tested on the Mac (text path, rules, catalog matching, HTTP API, tests with
the real catalog; speech recognition with synthetic clips). It is pushed to this repository when its tests pass.
**Run `git pull` before writing any server code**, so two sessions do not write the same files.
`git log` tells you whether it has arrived: look for `server.py` and the `assistant/` package.

Planned layout (see `README.md` and `docs/SERVER.md` once they are in the repository):

```
server.py            FastAPI: GET /health, POST /v1/chat, POST /v1/voice, bearer key
assistant/           config, catalog (name matching), rules (fast parser), llm (Ollama), actions (validation),
                     stt (faster-whisper), pipeline, sessions
tests/               pytest, runs without Ollama and without faster-whisper
tools/client.py      send a text or a .wav to a running server
run.sh               starts uvicorn on 0.0.0.0:8010
deploy/              systemd user unit
```

What could not be done on the Mac and is yours on the Spark:

1. Anything with **Ollama** (the Mac has none): the language-model fallback has only been run against a fake.
2. **Whisper on the Spark's hardware**: model size, speed, accuracy on anatomical words.
3. The **network** between Los Angeles and the Spark.
4. Running it as a **service**.

## Phase 1 — can start now, needs no server code

Do these in order. Ask the user when a step needs them (a browser login, a choice).

1. Machine check. Write the results into `docs/SPARK_NOTES.md` (create it):
   `uname -a`, `python3 --version`, `nvidia-smi`, `ollama --version`, `ollama list`, free disk, `ss -ltnp` (which
   ports are taken: 8000-8002 were used by earlier projects on this machine; this server uses **8010**).
2. Language model. The default is `gemma4:e4b` (it was already on this Spark for the RosOrinPro project).
   `ollama pull` it if missing. Check that structured output works and how long it takes:
   ```bash
   curl -s http://localhost:11434/api/chat -d '{"model":"gemma4:e4b","stream":false,"think":false,
     "options":{"temperature":0},"format":{"type":"object","properties":{"reply":{"type":"string"}},
     "required":["reply"]},"messages":[{"role":"user","content":"Say hello in five words."}]}'
   ```
   If a larger model is installed and answers in under about two seconds, note it as a candidate.
3. Python. `python3 -m venv .venv`. When `requirements.txt` is in the repository:
   `.venv/bin/pip install -r requirements.txt`. Known trap from the earlier project: `faster-whisper 1.2.1`
   needs `av==17.1.0` (PyAV 18 breaks voice); the pin is in `requirements.txt`.
4. Network: follow `docs/NETWORK.md`. It needs the user for the Tailscale login. Write down the name or address
   the Mac will use.
5. Key: `openssl rand -hex 24`, put it in `.env` as `ASSISTANT_API_KEY=...` (`.env` is git-ignored). Give it to
   the user: they type `/key <text>` in the app once.

## Phase 2 — when the server code is in the repository

1. `git pull`, install requirements, run the tests exactly as `README.md` says. All must pass before you change
   anything. If one fails on the Spark and passed on the Mac, that is a finding: write it down, then fix it.
2. Start the server, `curl localhost:8010/health`, and run the examples with `tools/client.py`.
3. Speech: record real clips of the phrases in the table below (or synthesize them) and send them to `/v1/voice`.
   Check the transcript of the anatomical names especially ("gastrocnemius", "sartorius", "biceps femoris").
   Try `STT_MODEL=small.en` if `base.en` gets the names wrong, and compare speed. Whisper accepts an
   `initial_prompt`: a short list of the catalog's names there is the first thing to try for accuracy.
4. Language model: send the sentences the rules do not cover (questions about anatomy, vague requests, several
   commands in one sentence). Check every answer is valid against the protocol, short, and fast enough. Tune the
   prompt in `assistant/llm.py`. Never let the model's output reach the client without the validation in
   `assistant/actions.py`.
5. Service: `deploy/` has a systemd user unit. Enable it with linger so it survives a logout and a reboot.
6. Tell the user the address and the key. In the app: `/server http://<address>:8010`, `/key <text>`, `/status`.

Phrases that must work (the first column of acceptance):

| Say or type | Expected |
|---|---|
| switch to the muscle layer | `set_layer` muscles |
| show me the skeleton | `set_layer` skeleton |
| pause / play / make it faster / half speed | `timeline` |
| zoom in / zoom out a lot / look from behind / reset the view | `camera` |
| activate realtime pose estimation and read the data | `pose_estimation` on + `read_pose_data` |
| what is the right knee angle | the number from `context.pose.angles`, no action |
| show me the left biceps | `show_part` muscle "Biceps brachii" left |
| highlight the femur | `show_part` bone "Femur" |
| show me the hamstring | `show_part` "Biceps femoris (long head)" |
| what does the deltoid do | a two-sentence answer, no action |
| make her run | `play_action` Running |

## Phase 3 — together with the Unreal side

The user (or the Mac session) runs the app, types `/status`, then goes through the table above by typing and by
voice. Things to watch on the server log: latency per request, `source` (rules or llm), `need_catalog` happening
once per session at most.

## Rules for changing things

- `docs/PROTOCOL.md` is the contract and exists in both repositories. A change to it is a change to both, in the
  same hour, with `protocol` bumped if old clients would break. Adding an optional field or a new action type is
  not a break (the client ignores what it does not know).
- Rules first, language model second. Every command in the table above must work with Ollama stopped.
- The server never trusts the model: names are forced to the catalog, numbers are clamped, unknown action types
  are dropped.
- The server is reachable from outside the machine: keep the key check, keep the size limits, never execute or
  write anything that comes from a request.
- Keep `docs/SPARK_NOTES.md` up to date with what you measured and decided. The Mac session reads it.

## Ideas for "the rest", after the table above works

- Spoken answers: the client has an event for every reply (`OnAssistantReply`) and the logo already animates
  while "speaking"; a `/v1/speak` endpoint returning audio would complete the voice loop (needs a protocol
  addition and a small client change).
- Use the muscle descriptions: the catalog has names only. The atlas text (what it does, when running, injuries)
  could be sent too, so the model answers from the app's own text instead of from memory.
- Multi-turn: "now the other side", "zoom in on it" need `context.selected_part` and the session history.
- Reading pose data in words: "is my knee angle normal for running?" from `context.pose.angles`.
