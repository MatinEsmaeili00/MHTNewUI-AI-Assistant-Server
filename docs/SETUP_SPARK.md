# Setting up the server on the DGX Spark

Follow this from top to bottom. Every step has a check; do not go on when a check fails.

Written on a Mac. **None of these steps has been run on the Spark yet.** If a step does not work as written,
fix this page and note what happened in `docs/SPARK_NOTES.md`.

Assumed: Ubuntu on aarch64, Python 3.12, Ollama installed, the repository cloned to
`~/MHTNewUI-AI-Assistant-Server`. If it is somewhere else, use that path everywhere, including in
`deploy/mht-assistant.service`.

## 1. The machine

```bash
cd ~/MHTNewUI-AI-Assistant-Server
git pull
python3 --version            # 3.10 or newer; written and tested with 3.12
ollama --version
ss -ltnp | grep -E ':(8010|11434)\b'
```

Check: port 11434 is listening (Ollama). Port 8010 is **not** listening. If something already uses 8010,
choose another port and put `PORT=<number>` in `.env` (step 4); the app then needs that port in `/server`.

## 2. Python environment

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```

If `python3 -m venv` fails with "ensurepip is not available": `sudo apt install python3-venv`, then again.

Check:

```bash
.venv/bin/python -c "import fastapi, faster_whisper, av; print(av.__version__)"     # 17.1.0
```

`av` must be 17.1.0: faster-whisper 1.2.1 passes an argument that PyAV 18 removed, and every voice request
fails with it. The pin is in `requirements.txt`.

## 3. Tests

```bash
.venv/bin/python -m pytest tests -q
```

Check: `698 passed`. The tests need neither Ollama nor the Whisper model. If one fails here and passed on the
Mac, that is a finding: write it into `docs/SPARK_NOTES.md` before fixing it.

## 4. Settings

```bash
cp .env.example .env
echo "ASSISTANT_API_KEY=$(openssl rand -hex 24)" >> .env
chmod 600 .env
grep ASSISTANT_API_KEY .env
```

The last `ASSISTANT_API_KEY=` line in `.env` wins (the empty one from `.env.example` comes first; delete it if
you like). `.env` is git-ignored; never commit it. The user types the key into the app once: `/key <text>`.

`run.sh` reads `.env` as a shell file: a value with spaces needs quotes (`STT_PROMPT="..."`).

## 5. The language model

```bash
ollama list                          # is gemma4:e4b there?
ollama pull gemma4:e4b               # if not
```

Check that the model accepts a JSON schema and see how long it takes:

```bash
time curl -s http://localhost:11434/api/chat -d '{"model":"gemma4:e4b","stream":false,"think":false,
  "options":{"temperature":0},"format":{"type":"object","properties":{"reply":{"type":"string"}},
  "required":["reply"]},"messages":[{"role":"user","content":"Say hello in five words."}]}'
```

Check: the answer has `"content":"{\"reply\": ...}"`. If the model answers `does not support thinking`, that is
fine: the server retries without the `think` flag by itself.

To use another model, put `LLM_MODEL=<name>` in `.env`.

## 6. First start, in a terminal

```bash
./run.sh
```

Expected in the first seconds:

```
Loading LLM gemma4:e4b ...
Loading Whisper base.en (cpu, int8) ...
LLM gemma4:e4b ready.
Whisper base.en ready.
```

The first start downloads the Whisper model (about 150 MB for `base.en`) into `~/.cache/huggingface`, so it
needs internet once.

In a second terminal:

```bash
cd ~/MHTNewUI-AI-Assistant-Server
curl -s localhost:8010/health
```

Check: `"status":"ok"`, `"auth_required":true`, `"llm_ready":true`, `"stt_ready":true`.

- `llm_ready` false: Ollama is not running or the model is missing. The server log has the reason. The direct
  commands still work.
- `stt_ready` false: faster-whisper is not installed, or the model could not be downloaded.
- `auth_required` false: the key is not in `.env`, or `run.sh` was not started from this folder.

## 7. Try it

```bash
export ASSISTANT_API_KEY=$(grep '^ASSISTANT_API_KEY=' .env | tail -1 | cut -d= -f2)

# rules (milliseconds, "source": "rules")
.venv/bin/python tools/client.py "show me the left biceps" --catalog
.venv/bin/python tools/client.py "switch to the muscle layer"
.venv/bin/python tools/client.py "play it faster"
.venv/bin/python tools/client.py "start the pose estimation and read the data" --pose-off
.venv/bin/python tools/client.py "what is the right knee angle"
.venv/bin/python tools/client.py "show me the hamstring"
.venv/bin/python tools/client.py "make her run"

# language model ("source": "llm"): this path has never run against a real model before
.venv/bin/python tools/client.py "what does the deltoid do?"
.venv/bin/python tools/client.py "hello, what can you do?"

# a wrong key must give 401
.venv/bin/python tools/client.py "pause" --key wrong
```

Check against the table "Phrases that must work" in `docs/HANDOFF.md`. For the language model: `source` is
`llm`, `reply` is two or three plain sentences, `actions` is empty for a question. Write the latency
(`latency_ms`) into `docs/SPARK_NOTES.md`. If `source` is `error`, the server log has the reason.

Voice needs a WAV file (16-bit mono; any sample rate):

```bash
sudo apt install -y espeak-ng                  # only to make a test clip; a real recording is better
espeak-ng -w /tmp/clip.wav "show me the left biceps"
.venv/bin/python tools/client.py /tmp/clip.wav
```

Check: `transcript` is what was said and `actions` has the `show_part`. With `SAVE_LAST_AUDIO=1` (the default)
the last clip the server received is `last_voice.wav` in this folder: listen to it when the app's voice
commands are misheard.

On the Mac, with synthetic clips, `base.en` got these wrong: "Pause." (heard "Cause"), "Bones." ("buns"),
"semitendinosus", "tibialis anterior", "brachioradialis", "psoas major", "lats". The rules now understand what
it wrote for each of them, but real voices will produce other spellings. Two things to try, in this order
(details in `docs/SERVER.md`):

1. `STT_PROMPT` (the commented line in `.env.example`): fixed most of them, broke "psoas major".
2. `STT_MODEL=small.en`: slower, more accurate.

Spellings that keep coming back go into `MISHEARD` in `assistant/catalog.py`.

Stop the server with Ctrl+C before the next step.

## 8. Run it as a service

A systemd **user** unit, so it needs no root and runs as you.

```bash
mkdir -p ~/.config/systemd/user
cp deploy/mht-assistant.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now mht-assistant
systemctl --user status mht-assistant --no-pager
curl -s localhost:8010/health
```

Keep it running after you log out, and start it at boot:

```bash
sudo loginctl enable-linger "$USER"
loginctl show-user "$USER" | grep Linger       # Linger=yes
```

Check: log out, log in again (or reboot), then `curl -s localhost:8010/health` still answers.

The unit starts `run.sh` in `~/MHTNewUI-AI-Assistant-Server`. If the repository is somewhere else, change
`WorkingDirectory` and `ExecStart` in `~/.config/systemd/user/mht-assistant.service` and run
`systemctl --user daemon-reload` again.

Ollama must be running too. After a reboot: `systemctl status ollama` (it is a system service when installed
with the official script). If the server started before Ollama, `llm_ready` is false until the first question
that needs the model; that question loads it.

## 9. Logs and everyday commands

```bash
journalctl --user -u mht-assistant -f          # follow the log
journalctl --user -u mht-assistant --since "10 min ago"
systemctl --user restart mht-assistant         # after git pull or a change in .env
systemctl --user stop mht-assistant
```

One line per request, for example:

```
14:02:11 [rules 3ms] chat "show me the left biceps" -> [{"type": "show_part", ...}] | Here is the left Biceps brachii.
14:02:40 [llm 1240ms] voice "What does the deltoid do?" -> [] | The deltoid lifts the arm ...
```

`rules` = answered without the model; `llm` = the model answered; `error` = the model did not answer;
`need_catalog` instead of the actions = the server asked the app for the catalog (at most once per session).

After `git pull`:

```bash
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest tests -q
systemctl --user restart mht-assistant
```

## 10. Reaching it from outside

`docs/NETWORK.md`. The server already listens on `0.0.0.0:8010`. From the other machine:

```bash
curl -s http://<address>:8010/health
```

Then in the app: `/server http://<address>:8010`, `/key <the key>`, `/status`.

## Troubleshooting

| What you see | Cause |
|---|---|
| `run.sh: .venv/bin/activate: No such file` | step 2 was not done in this folder |
| `address already in use` | another process has the port: `ss -ltnp \| grep 8010`; or the service is already running |
| every voice request fails, log has `metadata_errors` | PyAV is not 17.1.0: `.venv/bin/pip install av==17.1.0` |
| `/v1/voice` answers 503 | faster-whisper missing (`pip install -r requirements.txt`) or the model could not be loaded (log) |
| voice works, the transcript is wrong | listen to `last_voice.wav`; then `STT_PROMPT` or `STT_MODEL=small.en` |
| `source: "error"` for every question | Ollama not running, model not pulled, or slower than `LLM_TIMEOUT_S` (25 s) |
| the first question after a pause takes long | the model was unloaded: raise `LLM_KEEP_ALIVE` (default `1h`) |
| 401 from the app | the key in the app (`/key`) is not the one in `.env`; restart the server after changing `.env` |
| 401 on `/health` | not from this server: `/health` never needs a key |
| the app gets `need_catalog` on every request | the catalog the app sends is empty or not in the protocol's form; the log line shows `need_catalog` |
