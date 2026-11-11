# For Claude sessions working in this repository

Start with `docs/HANDOFF.md` (what exists, what was tested where), then `docs/SPARK_NOTES.md` (what was
measured and decided on the DGX Spark) and `docs/TODO.md` (what is open, and for whom).

- The contract with the Unreal client is `docs/PROTOCOL.md`. The same file is in
  `MatinEsmaeili00/MHTNewUI-AI-Assistant-Unreal`. Never change one copy only.
- `git pull` before writing server code: part of it is written on another machine.
- Rules first, language model second: every direct command must work with Ollama stopped.
- Everything the language model returns goes through `assistant/actions.py` before it leaves the server.
- Run the tests before and after a change. They need neither Ollama nor faster-whisper.
- Record what you measured and decided on the Spark in `docs/SPARK_NOTES.md`, and keep `docs/TODO.md` current.
- On the Spark the repository is in `~/DigitalHuman_ai/MHTNewUI-AI-Assistant-Server` and runs as the systemd
  user service `mht-assistant`. After a change: tests, then `systemctl --user restart mht-assistant`.
- `tools/acceptance.py` runs the acceptance table against a running server (`--url` for another machine).
- The server port is 8010. Ports 8000-8002 are used by other things on the machines involved.
- Secrets (`ASSISTANT_API_KEY`) live in `.env`, which is git-ignored. Never commit a key.
