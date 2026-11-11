# Protocol v1 — Unreal client ⇄ assistant server

This file is the contract. The same file is in both repositories
(`MHTNewUI-AI-Assistant-Server/docs/PROTOCOL.md` and `MHTNewUI-AI-Assistant-Unreal/docs/PROTOCOL.md`).
Change it in both places, and bump `protocol` when a change is not backwards compatible.

```
Unreal (Mac / iPad, anywhere)                      DGX Spark (Louisiana)
  chat box or held BOT logo                          server.py  :8010
        │  POST /v1/chat   {text, context}  ───────►   rules  ──►  LLM (Ollama)
        │  POST /v1/voice  wav + context    ───────►   Whisper ─► rules ─► LLM
        │  ◄───────  {transcript, reply, actions[]}
        ▼
  runs every action on the avatar, then shows `reply` in the chat box
```

The server decides **what** to do. Unreal does it and knows whether it worked. The server never
touches Unreal directly, so it needs no route back to the client: the client only makes outgoing
HTTP requests, which is what makes it work across the internet.

## Transport

- HTTP/1.1, JSON in UTF-8. Default port **8010**.
- Authentication: if the server was started with `ASSISTANT_API_KEY`, every request except
  `GET /health` must carry `Authorization: Bearer <key>`. Wrong or missing key → `401`.
- The client waits up to 30 s for an answer.
- Errors the client can show: any non-200 answer should have a JSON body `{"detail": "<one sentence>"}`.

## `GET /health`

No authentication. The client calls it when Play starts and for `/status`.

```json
{
  "status": "ok",
  "name": "mht-assistant",
  "version": "0.1.0",
  "protocol": 1,
  "auth_required": true,
  "llm_model": "gemma4:e4b",
  "llm_ready": true,
  "stt_model": "base.en",
  "stt_ready": true
}
```

`llm_ready` false = only the rule-based commands work. `stt_ready` false = `/v1/voice` answers `503`.

## `POST /v1/chat`

```json
{
  "protocol": 1,
  "session_id": "4f0c1c2e9a7b4d6f8e1a2b3c4d5e6f70",
  "text": "show me the left biceps",
  "context": { ... see Context ... },
  "catalog_id": "9b1d5c0e",
  "catalog": { ... see Catalog; only sent when the server asked for it ... }
}
```

- `session_id`: one per Play session. The server may keep a short conversation history per session.
- `catalog_id`: always sent. `catalog`: only in the first request of a session and after the server
  answered `need_catalog`.

## `POST /v1/voice`

`multipart/form-data` with two parts:

| Part | Content |
|---|---|
| `audio` | file `speech.wav`: 16-bit PCM, mono, the microphone's own sample rate (usually 48 000 Hz) |
| `request` | the same JSON object as the body of `/v1/chat`, as a string, without `text` |

The server transcribes the audio and then does exactly what `/v1/chat` does with that text.
`transcript` in the answer is what it heard; the client shows it as the user's chat bubble.

## Answer (both endpoints)

```json
{
  "transcript": "show me the left biceps",
  "reply": "Here is the left biceps brachii.",
  "actions": [
    {"type": "show_part", "kind": "muscle", "name": "Biceps brachii", "side": "left"}
  ],
  "source": "rules",
  "latency_ms": 14,
  "need_catalog": false
}
```

| Field | Meaning |
|---|---|
| `transcript` | The text that was understood (typed text is echoed back). Empty if nothing was heard. |
| `reply` | One or two short sentences for the chat box. Written as if the actions will work. |
| `actions` | Zero or more actions, run by the client in order. See Actions. |
| `source` | `rules`, `llm` or `error`. For logs only. |
| `latency_ms` | Server time for this request. For logs only. |
| `need_catalog` | `true` = the server does not know `catalog_id`. See Catalog. |

What the client does with it:

1. Voice only: shows `transcript` as the user's bubble.
2. Runs the actions in order.
3. Shows `reply`. If an action failed, the client adds one line saying which one and why
   (for example "I could not do that: this avatar has no Jumping clip.").
4. Actions that produce text (`show_part` descriptions, `read_pose_data`) add their own bubbles after `reply`.
5. Keeps each action's result and sends them as `context.last_results` in the next request.

An answer with no actions and a `reply` is a plain conversation turn (a question about anatomy, "hello", ...).

## Context

The avatar's state when the user spoke. Every field may be missing (an avatar without anatomy layers
sends no `layer`); the server must not fail on a missing field.

```json
{
  "avatar": {"name": "BP_Abby8", "index": 0, "count": 3},
  "layer": "body",
  "timeline": {
    "playing": true, "speed": 1.0, "speed_steps": [0.25, 0.5, 1, 1.5, 2, 3],
    "time": 0.42, "cycle_seconds": 1.1, "recording": false
  },
  "action": {"playing": "", "available": ["Idle", "Running", "Jogging", "TennisForehand"]},
  "panel": "none",
  "camera": {"distance": 300.0, "min": 50.0, "max": 1200.0, "moved": false},
  "pose": {
    "status": "running", "overlay": true, "panel_open": true, "recording": false, "fps": 11.8,
    "person": true, "visible_side": "right",
    "angles": {"right knee": 142.3, "left knee": 95.0, "trunk": 84.1}
  },
  "selected_part": {"kind": "muscle", "name": "Biceps brachii", "side": "left"},
  "chat_open": true,
  "last_results": [{"type": "set_layer", "ok": true, "message": ""}]
}
```

| Field | Values |
|---|---|
| `layer` | `body`, `muscles`, `skeleton` |
| `timeline.time` | 0..1, position in the animation cycle |
| `timeline.speed` | playback multiplier (1 = normal) |
| `action.playing` | name of the action clip playing, `""` = the avatar's own walk / jog / run |
| `action.available` | action names this avatar can play right now |
| `panel` | `none`, `actions`, `pose_estimation`, `graphs`, `timeline`, `saved_videos`, `settings` |
| `camera.distance` | cm from the avatar; `min` = closest, `max` = farthest |
| `pose.status` | `off`, `loading`, `running`, `error` |
| `pose.angles` | degrees, only the valid ones, Sports2D names in lower case (`right knee`, `left hip`, `trunk`, ...). Missing or empty when pose estimation is off or sees nobody. |
| `selected_part` | the muscle or bone that is highlighted now, or missing |
| `last_results` | results of the previous answer's actions |

## Catalog

The names the avatar knows: its muscles and bones. It is long (about 200 names) and never changes
during a session, so it is sent once and then referred to by `catalog_id` (a hash the client computes).

```json
{
  "parts": [
    {"kind": "muscle", "name": "Biceps brachii", "sides": ["left", "right"], "region": "Upper arm"},
    {"kind": "bone", "name": "Femur", "sides": ["left", "right"], "region": "Thigh"},
    {"kind": "bone", "name": "Mandible (jaw)", "sides": ["center"], "region": "Skull"}
  ]
}
```

`kind` is `muscle` or `bone`. `sides` is a subset of `left`, `right`, `center`.

If the server gets a `catalog_id` it has not seen (it was restarted, or it is a new avatar) and the
request has no `catalog`, it answers:

```json
{"transcript": "show me the left biceps", "reply": "", "actions": [], "need_catalog": true,
 "source": "rules", "latency_ms": 3}
```

The client then repeats the request as `POST /v1/chat` with `text` = that `transcript` and with
`catalog` included (so a voice clip is never uploaded twice). The server should only ask when it
actually needs the names (the request mentions a body part), and must never ask twice in a row for
the same `catalog_id`.

## Actions

Every action is an object with a `type`. Unknown types and unknown fields are ignored by the client
(the result is `ok: false`, "unknown action"), so new actions can be added without breaking old clients.

### `set_layer`
Shows one anatomy layer.
```json
{"type": "set_layer", "layer": "muscles"}
```
`layer`: `body` | `muscles` | `skeleton`.

### `timeline`
Controls the animation playback (the bar at the bottom of the screen).
```json
{"type": "timeline", "op": "set_speed", "value": 2}
```

| `op` | `value` | Does |
|---|---|---|
| `play` | – | play |
| `pause` | – | pause |
| `toggle` | – | play if paused, pause if playing |
| `restart` | – | jump to the start (keeps playing if it was) |
| `faster` | steps, default 1 | next higher speed step |
| `slower` | steps, default 1 | next lower speed step |
| `set_speed` | multiplier | nearest speed step to `value` (steps are in `context.timeline.speed_steps`) |
| `seek` | 0..1 | jump to that point of the cycle |
| `step` | seconds, may be negative | step forward / back and pause |
| `next_marker` | – | jump to the next marker (bookmark / recording) |
| `bookmark` | – | drop a bookmark at the current time |
| `record_start` | – | start recording (also starts the pose recording) |
| `record_stop` | – | stop and save the recording |

### `camera`
```json
{"type": "camera", "op": "zoom_in", "value": 2}
```

| `op` | Fields | Does |
|---|---|---|
| `zoom_in` | `value`: steps, default 1 | each step brings the camera 25 % closer |
| `zoom_out` | `value`: steps, default 1 | each step moves it 33 % farther |
| `zoom_to` | `value`: 0..1 | 0 = farthest, 1 = closest |
| `orbit` | `yaw`, `pitch`: degrees | turns the camera around the avatar; +yaw = the camera moves to its right, +pitch = up |
| `view` | `value`: `front`, `back`, `left`, `right`, `top` | looks at the avatar from that side (`left` = the avatar's left side) |
| `reset` | – | back to the starting view |

### `pose_estimation`
Switches the real-time pose estimation (Sports2D overlay and angles).
```json
{"type": "pose_estimation", "enabled": true, "show_panel": true}
```
`enabled`: true / false. `show_panel` (optional, default: same as `enabled`): open / close the Pose
Estimation side panel. `overlay` (optional): show / hide the skeleton overlay without stopping the analysis.

### `read_pose_data`
The client writes the current joint angles into the chat box. If pose estimation was only just
switched on, the client waits (up to 10 s) for the first analysed frame.
```json
{"type": "read_pose_data", "angles": ["right knee", "left knee"]}
```
`angles` (optional): only these. Without it: the main joint angles.
If `context.pose.angles` already has what the user asked for, the server can simply answer in `reply`
and not send this action.

### `show_part`
Shows one muscle or bone: switches to its layer, highlights it, turns the camera to it and writes its
description into the chat box.
```json
{"type": "show_part", "kind": "muscle", "name": "Biceps brachii", "side": "left"}
```
`kind`: `muscle` | `bone`. `name`: exactly as in the catalog. `side`: `left` | `right` | `center` | `""`
(`""` = the user did not say; the client takes the side nearer to the camera).
Optional: `describe` (default true), `focus` (default true: move the camera).

### `clear_selection`
Removes the highlight.
```json
{"type": "clear_selection"}
```

### `play_action`
Plays one of the avatar's action clips.
```json
{"type": "play_action", "name": "Running"}
```
`name`: one of `context.action.available`. `Idle` = back to the avatar's own animation.

### `open_panel`
```json
{"type": "open_panel", "panel": "graphs"}
```
`panel`: `none` (close) | `actions` | `pose_estimation` | `graphs` | `timeline` | `saved_videos` | `settings`.

### `switch_avatar`
```json
{"type": "switch_avatar", "direction": "next"}
```
`direction`: `next` | `previous`.

## Action results

After running an action the client keeps `{ "type", "ok", "message" }` and sends the list as
`context.last_results` with the next request. `message` is empty on success and one sentence on
failure. The server can use it to answer "did that work?" or to avoid repeating a failing action.

## Examples

| The user says | `actions` |
|---|---|
| "switch to the muscle layer" | `[{"type":"set_layer","layer":"muscles"}]` |
| "show me the bones" | `[{"type":"set_layer","layer":"skeleton"}]` |
| "pause" | `[{"type":"timeline","op":"pause"}]` |
| "play it faster" | `[{"type":"timeline","op":"faster"}, {"type":"timeline","op":"play"}]` |
| "half speed" | `[{"type":"timeline","op":"set_speed","value":0.5}]` |
| "zoom in a lot" | `[{"type":"camera","op":"zoom_in","value":3}]` |
| "show me from behind" | `[{"type":"camera","op":"view","value":"back"}]` |
| "start the pose estimation and read the data" | `[{"type":"pose_estimation","enabled":true}, {"type":"read_pose_data"}]` |
| "what is the right knee angle?" (pose running) | `[]`, `reply`: "The right knee is at 142 degrees." |
| "show me the left hamstring" | `[{"type":"show_part","kind":"muscle","name":"Biceps femoris (long head)","side":"left"}]` |
| "highlight the femur" | `[{"type":"show_part","kind":"bone","name":"Femur","side":""}]` |
| "make her run" | `[{"type":"play_action","name":"Running"}]` |
| "what does the deltoid do?" | `[]`, `reply`: a short answer |
