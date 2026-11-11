# MHTNewUI AI Assistant — server

The server behind the BOT chat of the Avatar Injury Prevention app (Unreal Engine). It runs on an NVIDIA DGX
Spark. The app sends what the user typed, or said while holding the BOT logo; the server answers with a short
reply and a list of actions (switch layer, timeline, camera, pose estimation, "show me the left biceps"), which
the app runs.

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
| [docs/PROTOCOL.md](docs/PROTOCOL.md) | the contract between the app and the server |
| [docs/NETWORK.md](docs/NETWORK.md) | reaching the Spark from another city |

**Status, 2026-11-11:** first commit: the contract, the avatar's real catalog of muscle and bone names
(`tests/fixtures/catalog.json`), the hand-off and the network guide. The server code is being written and
tested on another machine and is pushed here when its tests pass; this page is replaced then.

The Unreal side: <https://github.com/MatinEsmaeili00/MHTNewUI-AI-Assistant-Unreal>
