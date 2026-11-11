# Server: how it works and how to change it

The contract with the app is [PROTOCOL.md](PROTOCOL.md). This page describes the code behind it.

## How a request is processed

```
POST /v1/chat   ─────────────────────────────►┐
POST /v1/voice  ──► stt.transcribe (Whisper) ─►│ pipeline.handle(text, context, catalog_id, catalog, session_id)
                                               │   1. catalog: store.put(...) if one was sent, else store.get(catalog_id)
                                               │   2. rules.parse(text, context, catalog)
                                               │   3. only if the rules return None: llm.ask(...)
                                               │   4. actions.sanitize(every action)
                                               │   5. sessions.add(...)
                                               ▼
                                {transcript, reply, actions, source, latency_ms, need_catalog}
```

### 0. Before the endpoint (`server.py`, `GateMiddleware`)

Checked before the request body is read:

- The key. If `ASSISTANT_API_KEY` is set, everything except `GET /health` needs `Authorization: Bearer <key>`
  (compared with `hmac.compare_digest`). Otherwise `401 {"detail": "Missing or wrong API key."}`.
- The size. `/v1/voice`: 21 MB for the whole upload (20 MB audio + 1 MB for the `request` part); everything
  else: 2 MB. Larger: `413`. The `Content-Length` header is checked first, then the bytes are counted while
  they arrive.

Uploads are parsed in memory (`MultiPartParser.spool_max_size` is raised above the limit), so the only things
the server writes to disk are the catalog cache and, if `SAVE_LAST_AUDIO=1`, `last_voice.wav`.

The endpoints are plain `def` functions, so FastAPI runs them in worker threads: a "pause" that arrives while
Whisper or the language model is busy with another request is answered at once.

Every request writes one log line:

```
01:34:07 [rules 296ms] voice "Show me the PSO S major." -> [{"type": "show_part", ...}] | Here is the Psoas major. I heard "pso s major".
```

### 1. Speech to text (`assistant/stt.py`, `/v1/voice` only)

faster-whisper with `language="en"`, `beam_size=5`, `vad_filter=True`, decoded from memory. One transcription
at a time (a lock). The model is loaded in a background thread when the server starts.

- faster-whisper not installed: `503` with a `detail`, and `stt_ready: false` in `/health`.
- Empty audio, audio that cannot be decoded, or nothing heard: `200` with
  `"I did not catch that. Hold the logo while you speak."`, no actions, empty transcript.
- `STT_PROMPT` is passed to Whisper as `initial_prompt`. It biases the spelling towards the words in it. On the
  synthetic test clips it fixed "Pause.", "semitendinosus", "lats" and "Bones.", and broke "psoas major" (heard
  as "soleus major", a word in the prompt). So it helps for the names in it and can pull similar names towards
  them. It is off by default; measure it with real voices on the Spark.

### 2. The catalog (`assistant/catalog.py`)

`CatalogStore` keeps catalogs by `catalog_id` in memory and as `<CATALOG_CACHE_DIR>/<id>.json`, so a restart
does not need `need_catalog`. The file name is the id with everything except letters, digits, `-` and `_`
removed. A catalog is cleaned when it arrives: entries without a valid `kind` and `name` are skipped, at most
2000 parts are kept.

`need_catalog: true` is answered only when the catalog is unknown **and** the sentence needs it (the rules found
a part request, or the sentence goes to the language model and mentions a body part). It is never answered
twice in a row for the same id: the second time the reply says the part list is missing.

### 3. Rules (`assistant/rules.py`)

1. `normalize` (`assistant/text.py`): lower case, punctuation removed, spoken numbers to digits
   ("forty five" → `45`, "one and a half" → `1.5`, "L three" → `l 3`, "3rd" → `third`, "2x" → `2 x`).
2. The sentence is cut at `and`, `then`, `after that` and commas. If every piece is understood, the actions are
   put together in order. If one piece is not, the whole sentence is tried as one command ("adductor magnus
   and minimus"), and if that fails the rules return `None` and the sentence goes to the language model.
3. Each piece becomes its *core*: "hey bot", "please", "can you", "okay", "now", articles and pronouns are
   removed. "hey bot can you please show me the left biceps" → `show left biceps`.
4. `RULES` (the list at the end of the file) is tried in order. The first rule that returns a result wins:

   | Rule | Handles |
   |---|---|
   | `_rule_clear` | "clear the highlight" |
   | `_rule_question` | "what layer am I on", "how fast is it playing", "is it recording", ... (from the context) |
   | `_rule_pose_angle` | "what is the right knee angle". Not "is my knee angle normal for running": with a number in the context, a question about what the angle means (`_JUDGEMENT_RE`) goes to the language model |
   | `_rule_pose` | pose estimation on / off, overlay, "read the data" |
   | `_rule_panel` | open / close panels |
   | `_rule_avatar` | next / previous avatar |
   | `_rule_layer` | body / muscles / skeleton |
   | `_rule_timeline` | recording, bookmark, marker, seek, step, speed, restart, play, pause |
   | `_rule_other_side` | "the other side" when a part is highlighted |
   | `_rule_camera` | view, reset, zoom, orbit |
   | `_rule_action` | action clips |
   | `_rule_part` | muscles and bones |

   Almost every pattern must match the **whole** core (`re.fullmatch`). A sentence that only resembles a command
   is left to the language model instead of doing the wrong thing.

Values the rules use (constants in `rules.py`):

| Said | Sent |
|---|---|
| "zoom in" / "a bit" | `zoom_in` (1 step, no `value`) |
| "zoom in a lot" | `zoom_in`, `value` 3 |
| "zoom in twice" / "3 times" | `value` 2 / 3 |
| "zoom all the way in / out" | `zoom_to` 1 / 0 |
| "faster" / "a bit faster" | `faster` (1 step) |
| "a lot faster" / "faster twice" | `faster`, `value` 2 |
| "play it faster" | `faster`, then `play` |
| "slow motion" / "quarter speed" / "full speed" | `set_speed` 0.5 / 0.25 / the highest of `speed_steps` (3 if unknown) |
| "twice as fast" | `set_speed` current speed × 2 (2 if the context has no speed) |
| "rotate left" / "a little" / "a lot" | `orbit` yaw −45 / −15 / −90. Left = the camera moves to its left (negative yaw) |
| "turn around" | `orbit` yaw 180 |
| "tilt up" | `orbit` pitch 20 |
| "step forward" / "next frame" | `step` 0.1 s / 0.033 s (one frame at 30 fps) |
| "go to the end" | `seek` 1 |

### 4. Matching a part name (`Catalog.find`)

The first hit wins:

1. Names with a regular form: `L3`, `c 5`, "fifth cervical vertebra", "third rib", `rib 7`, "index finger",
   "tip of the pinky", "big toe", "metacarpal 3".
2. Catalog names: the full name, the name without its `(qualifier)`, "qualifier name" ("anterior deltoid"),
   a qualifier that is unique ("jaw", "kneecap", "C1"). If several parts share the name without qualifier,
   the one without a qualifier wins ("Triceps brachii"), else `DEFAULT_QUALIFIER` decides ("deltoid" → middle,
   "trapezius" → upper, "biceps femoris" → long head), and the reply mentions the others.
3. `ALIASES`: other names for one part ("biceps" → Biceps brachii, "shin bone" → Tibia, "ACL").
4. `REGIONS`: regions and muscle groups ("knee", "shoulder", "hamstring", "quads", "spine"). One part is picked
   and the reply says which. With "muscle" or "bone" in the sentence (or the muscle / skeleton layer showing),
   the first name of that kind is taken.
5. `MISHEARD`: what Whisper wrote for some names ("bye steps" → biceps, "humorous" → humerus).
6. Fuzzy matching (`difflib`, plus a rough sound key). Accepted from 0.82 after "show me ..." and from 0.90 for
   a bare word; names shorter than 5 letters are never matched fuzzily ("shine" is not "shin").

All tables are checked against the catalog that was sent. A table entry whose part is not in it is never
returned: "lats" → "This avatar has no Latissimus dorsi ..." with the nearby parts from `RELATED`.

When nothing matches: `I do not know a part called "fibia". The closest names I have are Tibia, Fibula and ...`,
no action. A description instead of a name ("the muscle that lifts the arm", `_DESCRIBED_RE` in `rules.py`) is
not answered that way: it goes to the language model, which has every name of the avatar. When several parts fit ("vastus"): `Which one do you mean: ...?`

Side: "left" / "right" → that side; nothing said → `""`; a part that only exists in the centre → `"center"`.

### 5. The language model (`assistant/llm.py`)

Ollama `POST /api/chat` with `"format": <JSON schema>`, `"think": false` (retried without it if the model
rejects the flag), `temperature 0`, `num_predict 400`, `keep_alive`. The system prompt has, in this order:

1. What the app is, the action list and the reply rules: plain text, at most three sentences; a question gets
   an answer and no action; either act or ask, never both; when the request is clear enough, act on the most
   likely meaning; if the app cannot do it, say so and do nothing else; no offer at the end.
2. **Every muscle and bone name of the avatar** (about 4200 characters for the real one), so that a described
   part ("the bone that protects the brain at the front") can be answered with a name the avatar has.
3. The current state as compact JSON and the available action clips.
4. "Closest to what the user said": at most 12 parts from `Catalog.candidates` (exact and fuzzy hits for the
   words of the sentence, parts whose region matches), then the highlighted part, then the parts the last
   turns were about, so "ok show it to me" after a question about a muscle still has that muscle.

Parts 1 and 2 are the same for every request of an avatar and come first: Ollama keeps the evaluated prompt
and only reads what follows the first difference. `test_names_come_before_what_changes_per_request` guards
that order.

The last `SESSION_TURNS` turns of the session are sent as chat history.

After the answer (`llm.ask`): a `side` of `left` or `right` in a `show_part` is removed unless the sentence has
a side word (`left`, `right`, `other`, `opposite`, `both`, `same`). The model tends to pick a side nobody asked
for; `""` lets the app take the side nearer to the camera.

If Ollama cannot be reached, times out, or answers something that is not JSON: `source: "error"` and a reply
that says the language model is not answering and lists a few direct commands.

`FAKE_LLM=1` replaces the model with an echo (`(fake LLM) You said: ...`).

At startup the model is loaded in a background thread (`llm.warm_up`). If Ollama is not up yet, as can happen
after a reboot, the load is tried again every 10 seconds for two minutes.

Verified on the Spark on 2026-11-11 with `gemma4:e4b` and `gemma4:12b`: both accept the schema, and every
answer of about 90 requests was valid JSON in the right shape. What each model got wrong, and the times, are
in `docs/SPARK_NOTES.md`. The tests still use a fake Ollama (`tests/test_llm.py`).

### 6. Validation (`assistant/actions.py`, `sanitize`)

Every action goes through it, from the rules and from the model:

| | |
|---|---|
| unknown `type`, not an object | dropped |
| enum values (`layer`, `op`, `view`, `panel`, `direction`) | a few synonyms are repaired ("bones" → `skeleton`), anything else dropped |
| `faster` / `slower` | steps 1..5; `value` is left out when it is 1 |
| `set_speed` | 0.05..10; dropped without a positive number |
| `seek`, `zoom_to` | 0..1 (a value between 1 and 100 is read as a percentage) |
| `step` | −10..10 s, default 0.1, dropped when 0 |
| `zoom_in` / `zoom_out` | steps 1..10 |
| `orbit` | yaw −360..360, pitch −90..90; dropped when both are 0 |
| `show_part` | `name` must resolve to a catalog part (through the same matching as above); `kind` is taken from the catalog; `side` fixed; dropped when there is no such part or no catalog |
| `play_action` | `name` must be one of `context.action.available` (case, spaces and "-ing" do not matter); else dropped |
| `read_pose_data` | `angles` reduced to Sports2D names; "knee" → right knee and left knee |
| fields the protocol does not have | removed |
| more than 8 actions | the rest is cut |

When an action of the model is dropped, the model's reply is not trusted either (it was written as if the action
would work): the reply is replaced or extended by a sentence that says what is really the case.

## How to add a command

1. Find the rule function it belongs to in `assistant/rules.py` (or write a new `_rule_...` and add it to
   `RULES`; the order matters, earlier rules win).
2. Write the pattern for the **core** form: no articles, no pronouns, no "please". "bookmark this frame" is
   `bookmark frame`. Use `_full(pattern, core)`.
3. In a pattern with a repeated group, no alternative may be the beginning of another one (`pose` next to
   `pose estimation`): that makes the pattern slow on hostile input. `test_hostile_text_is_answered_quickly`
   guards a few known cases.
4. Return `Result([A.something(...)], "Reply sentence.")` using the builders in `assistant/actions.py`.
5. Add the phrasings to `CASES` in `tests/test_rules.py` and run the tests. The test also checks that what the
   rule builds passes validation unchanged.

A new action **type** is a protocol change: add it to `docs/PROTOCOL.md` in both repositories first, then to
`ACTION_TYPES`, a builder and `_clean_one` in `actions.py`, to `SYSTEM_PROMPT` and `ANSWER_SCHEMA` in `llm.py`,
and to the tests.

## How to add a name

All in `assistant/catalog.py`. Keys are lower case without "the", "of", "and" (the form `key()` produces):

| The user says ... | Add to |
|---|---|
| another name for one part ("funny bone") | `ALIASES`: `"funny bone": ["Humerus"]` |
| a region or a group ("lower leg") | `REGIONS`: the parts in order of preference, muscles and bones mixed |
| something Whisper writes for a name ("bye steps") | `MISHEARD`: `"bye steps": "biceps"` (the right side is matched again) |
| a part many people ask for that the avatar may not have | `ALIASES` with the real name, and `RELATED` for what to offer instead |
| which one of "X (a)", "X (b)" is meant by "X" | `DEFAULT_QUALIFIER` |

`tests/test_catalog.py::test_every_table_entry_points_at_real_parts_or_is_declared_missing` fails when an entry
has a typo or a key that is not in normalised form. Add a line to the parametrised tests next to it.

Words that Whisper gets wrong for a command (not a part) go where the command is: `_PAUSE_MISHEARD`, the `_POSE`
pattern ("post estimation"), `_LAYER_WORDS` ("buns").

## How to change the model

Language model: `LLM_MODEL=<name from ollama list>` in `.env`, restart. `ollama pull <name>` first. The model
must support Ollama's structured output (`format` with a JSON schema). Check after a change:

```bash
.venv/bin/python tools/client.py "what does the deltoid do?" --catalog      # source must be "llm"
.venv/bin/python tools/client.py "can you point out the muscle runners pull at the back of the thigh" --catalog
```

`llm_ready` in `/health` is true after the warm-up or a call worked and false after one failed. The prompt is
`SYSTEM_PROMPT` in `assistant/llm.py`; the shape the model must answer in is `ANSWER_SCHEMA`.

Speech model: `STT_MODEL=small.en` (or any faster-whisper model) in `.env`, restart. It is downloaded on first
use. `STT_DEVICE` / `STT_COMPUTE` are passed to CTranslate2 as they are.
