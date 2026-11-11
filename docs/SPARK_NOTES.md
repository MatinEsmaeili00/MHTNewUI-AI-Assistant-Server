# Notes from the DGX Spark

What was measured and decided on the Spark. The Mac session reads this; keep it current.
What is still open: `docs/TODO.md`.

Session of 2026-11-11 (night, Central time), continuing from `docs/HANDOFF.md`.

## In short

- The server is installed, runs as a service on port 8010, and passes the acceptance table.
- The language model answers for real now. `gemma4:12b` is used, not `gemma4:e4b`.
- The Spark and the Mac are on the same Tailscale network. The app's address for the server is
  `http://100.118.119.14:8010`.
- **The app on the Mac works against the Spark.** Between 02:48 and 02:52 it sent 28 requests, typed and
  spoken; all were answered, none failed. See "The app against the Spark".
- Speech recognition on the Spark was measured with synthetic voices only. `base.en` stays for now; the
  vocabulary prompt (`STT_PROMPT`) raised it from 86 % to 94 % at the same speed and is the next thing to
  switch on.
- `docs/PROTOCOL.md` did not change. The Unreal side needs no change.

## The machine

| | |
|---|---|
| Name, system | `spark-99eb`, Ubuntu 24.04.4 LTS, Linux 6.17.0-1018-nvidia, aarch64, 20 cores |
| GPU | NVIDIA GB10, driver 580.159.03 (`nvidia-smi` reports no separate memory: it is shared with the system) |
| Memory, disk | 119 GB, about 100 GB free at the start; 3.4 TB free on `/` |
| Python | 3.12.3 (`python3 -m venv` worked without installing anything) |
| Ollama | 0.33.2, system service, enabled, listening on `127.0.0.1:11434` |
| Models in Ollama | `gemma4:e4b` (was there), `gemma4:12b` (pulled in this session, 8.0 GB) |
| Ports taken | 22, 8002 (a uvicorn of another project), 8765, 11434 (Ollama), 11000, 18789, 631. **8010 was free.** |
| Campus address | `130.39.93.164` (the hand-off had `130.39.94.33` from the earlier project) |
| `sudo` | asks for a password. This session had none: everything below was done without root, except Tailscale, which the user installed. |
| Sleep | automatic suspend is off (`sleep-inactive-ac-type 'nothing'`); the machine had been up for 46 days |

Packages in `.venv`: fastapi 0.162.4, starlette 1.10.0, pydantic 2.14.3, uvicorn 0.54.0, faster-whisper 1.2.1,
ctranslate2 4.9.0, av 17.1.0. The same versions as on the Mac.

`ctranslate2.get_cuda_device_count()` is 0: the wheel for aarch64 is built without CUDA, so Whisper runs on the
CPU here. `STT_DEVICE=cuda` does not work with this install.

## Where things are

| | |
|---|---|
| Repository | `~/DigitalHuman_ai/MHTNewUI-AI-Assistant-Server` (not `~/MHTNewUI-AI-Assistant-Server` as the docs assumed) |
| Settings | `.env` there, mode 600, git-ignored. It has the key, `LLM_MODEL=gemma4:12b`, `LLM_KEEP_ALIVE=24h`; the rest is as in `.env.example` |
| Service | `~/.config/systemd/user/mht-assistant.service`, written by `deploy/install-service.sh` |
| Log | `journalctl --user -u mht-assistant -f` |
| Whisper models | `~/.cache/huggingface/hub` |

## Tests

`698 passed` on the first run, before any change: nothing behaves differently on the Spark. `716 passed` after
the changes of this session.

## Language model

### Does the JSON schema work, and how fast?

The check of the hand-off (`"format": {schema}`, `"think": false`, temperature 0, "Say hello in five words"):

| Model | First call (loads the model) | Next call | Answer |
|---|---|---|---|
| `gemma4:e4b` | 8.4 s | 0.27 s | `{"reply": "Hello there, how are you?"}` |
| `gemma4:12b` | about 16 s | not measured alone | valid |

Both accept `"think": false` and the schema. In about 90 requests that reached the model through the server no answer was
invalid JSON or the wrong shape, and `source: "error"` never happened.

### Which model

The same 27 sentences (questions, vague requests, things the app cannot do, a four-turn conversation) were sent
to both, with the same prompt.

| | `gemma4:e4b` | `gemma4:12b` |
|---|---|---|
| Time per answer | 0.45 to 1.4 s | 0.8 to 1.8 s; the longest answer of the acceptance run took 2.2 s, and one short answer ("who are you?") took 4.4 s once |
| "where does the gastrocnemius attach?" | "to the tibia and fibula" (wrong) | "originates from the femur ..., attaches to the calcaneus" (right) |
| "which muscles are used when running?" | played the Running clip and asked "Would you like me to do that now?" | named the muscles, no action |
| "is my right knee angle normal for running?" | "I cannot provide a diagnosis" | says the number and that it is a common range |
| Follows "either act or ask" | mostly | in every case tried |

**Decision: `LLM_MODEL=gemma4:12b`** in the Spark's `.env`. It is right about anatomy where the smaller one was
not, and it stays under the two seconds the hand-off set for most answers (see the table). The default in the
code and in `.env.example` is still `gemma4:e4b`, so a machine without the larger model works as before.

`LLM_KEEP_ALIVE=24h`: loading `gemma4:12b` takes about 16 s, which the first question after an idle hour would
have paid. `ollama ps` showed it as 2.9 GB on the GPU. `gemma4:e4b` is still installed (9.6 GB on disk); see
`docs/TODO.md` before removing it.

Not tried: `gemma4:26b`, `gemma4:31b` (16 to 20 GB downloads). They would fit in memory; whether they stay
under two seconds is unknown.

### What the first real answers showed, and what was changed

With the prompt as it came from the Mac, `gemma4:e4b`:

- acted and asked in the same answer ("I want to see what is under the skin": switched to muscles and asked
  "Which one would you like to see?");
- added actions to questions ("what does the gastrocnemius do?" highlighted it; "where does it attach?"
  highlighted two bones);
- used another action when it could not do what was asked ("make her do a backflip" played Idle);
- ended most answers with an offer, some for things that do not exist ("an action clip demonstrating proper
  warm-up exercises");
- chose a side nobody said (`"side": "right"` for "the muscle runners pull at the back of the thigh").

Changes in `assistant/llm.py`:

1. Six numbered rules in the system prompt: a question gets an answer and no action; either act or ask; act on
   the most likely meaning when it is clear enough; if the app cannot do it, say so and do nothing else; no
   offer at the end; how to talk about a joint angle.
2. Every muscle and bone name of the avatar is in the prompt, before the parts that change per request. Before,
   the model saw at most 12 candidates and could not name a part the user only described. With it: "the bone
   that protects the brain at the front" gives Frontal bone, "the muscle you use to stand on your toes" gives
   Gastrocnemius and Soleus. It costs 0.1 to 0.4 s per answer.
3. A side from the model is dropped unless the sentence has a side word.
4. The candidate list also has the highlighted part and the parts of the last turns, so "ok show it to me"
   after "what does the gastrocnemius do?" works.
5. The warm-up at startup retries for two minutes (the server can be up before Ollama after a reboot).

Changes in `assistant/rules.py` (both send a sentence to the model that the rules used to half-answer):

- "is my right knee angle normal for running?" was answered "The right knee is at 142 degrees." It now goes to
  the model when the angle is in the context. With pose estimation off it still switches it on and reads.
- "show me the muscle that lifts the arm" was answered `I do not know a part called "lifts arm"`. A description
  after "muscle / bone / part that ..." now goes to the model.

And in `assistant/catalog.py`: the reply "For the in upper arm I am showing ..." lost its "in".

Still wrong after the changes:

- "show me the muscle that lifts the arm" gives Biceps brachii with both models. The deltoid is in the list
  ("Deltoid (middle)"); the model does not choose it.
- "which bone is the funny bone? show me" shows the Ulna and explains that it is really a nerve. Fair, but it
  is the model's own knowledge, not the app's text.
- The model answers anatomy from memory. `docs/TODO.md` has the idea of sending the app's descriptions.

### Acceptance table

`tools/acceptance.py --url http://100.118.119.14:8010 --extra`, against the service, `gemma4:12b`:

- The 17 phrases of the table: all pass. 16 are answered by the rules in 0 to 13 ms. "what does the deltoid
  do" is answered by the model in 1.6 s: "The deltoid is the main muscle of the shoulder. It is responsible for
  lifting the arm forward, backward, and out to the side."
- Five more sentences for the model (`--extra`): all pass, 1.3 to 2.2 s.
- A missing key and a wrong key: `401`.

## Speech recognition

Everything here is **synthetic speech**: 36 phrases (`tools/stt_phrases.txt`: the acceptance table and the
anatomical names) spoken by six Piper voices (four American, two British; 22 kHz, 16-bit mono), 216 clips. No
person has spoken to the server yet. A synthetic voice can also say a Latin name wrongly, which then counts
against the speech model.

"Right" means: the transcript gives the same actions as typing the phrase (`tools/stt_bench.py`). All on the
CPU, `int8`, beam 5, the settings of `assistant/stt.py`.

| Model | Prompt | Right, of 216 | Transcript exact | Seconds per clip, median / slowest |
|---|---|---|---|---|
| `base.en` | none | 185 (86 %) | 155 | 0.38 / 3.23 |
| `base.en` | the `STT_PROMPT` line of `.env.example` | 203 (94 %) | 183 | 0.39 / 1.82 |
| `small.en` | none | 192 (89 %) | 165 | 0.94 / 1.23 |
| `small.en` | the same line | 209 (97 %) | 195 | 1.05 / 1.98 |

Still being measured when this was committed: `medium.en`, `large-v3-turbo`, `distil-large-v3`, and `base.en` with a prompt that also has "psoas major", "patella" and "brachioradialis".
`medium.en` is slow on this CPU: an earlier run had not finished its 216 clips after six minutes.

The `base.en` row without a prompt was measured with nothing else running; the other rows while the app was
being tried against the server.

Through the real endpoint (`/v1/voice`, the service, `base.en`, five clips over the Tailscale address): 400 to
510 ms per request, all five right, including "Show me the gastric neemius" which the name matching turned into
Gastrocnemius.

What `base.en` got wrong (31 of 216), by kind:

- Short commands in some voices: "Pause." heard as "Huzz.", "Play." as "Bye.", "Bones." as "Once" and
  "Close.", "Zoom in." as "2 men", "Half speed." as "Have speed." (three voices).
- Latin names: "psoas major" in five of six voices ("sewers major", "solas major"), "patella" in three
  ("pay teller"), "semitendinosus" in three, "brachioradialis" in two, "sartorius", "biceps femoris", "tibia",
  "soleus" once or twice each.
- Small slips that break a whole-sentence rule: "highlights the femur", "rid the data", "Open the grass",
  "Next to Avatar".

None of these were added to the `MISHEARD` table: they come from synthetic voices, and a table entry should
come from what real people's speech turns into.

**Decision so far: `STT_MODEL=base.en` stays**, and `STT_PROMPT` is still off in the Spark's `.env`.

- A larger model alone is not the answer: `small.en` gains 3 points and takes 2.5 times as long.
- Both together are best so far: `small.en` with the prompt, 97 %, about one second per clip. Whether three
  points over `base.en` with the prompt are worth 0.65 s on every spoken command is for real voices to decide.
- The prompt is: 8 points for nothing. On the Mac it had broken "psoas major" ("soleus major"). Here "psoas
  major" fails without the prompt in five of six voices and with it in four of six, so the prompt costs
  nothing on that name; it just does not fix it, because the name is not in the prompt.
- What the prompt still misses (13 of 216): "psoas major" (4), single short words in one voice each ("Pause.",
  "Play.", "Bones.", "Half speed."), and small slips ("rid the data", "Highlighter femur", "Open the grass",
  "Next to avatar").
- Next: the run with the longer prompt, then switch `STT_PROMPT` on in `.env` and listen to how real voices do
  (`docs/TODO.md`).

## The service

`deploy/install-service.sh` (new) writes the unit with the real folder and starts it. State:

- `systemctl --user is-enabled mht-assistant`: enabled. Active since 02:36.
- `loginctl show-user xrstudio`: `Linger=yes` (it already was), so the service does not need a login.
- `tailscaled` and `ollama` are enabled system services.
- **Not tested: a reboot or a logout.** The machine runs other projects and was not restarted.

## The link

| | |
|---|---|
| Tailscale on the Spark | 1.104.6, installed and signed in by the user |
| The Spark | `spark-99eb`, `100.118.119.14`, `spark-99eb.tail6962d3.ts.net` |
| The Mac | `internaladmins-macbook-pro`, `100.65.128.57` |
| Path between them | direct, not relayed (`tailscale ping`: "via 167.96.161.161:41641 in 9ms") |
| Ping, Spark to Mac | 5 of 5 answered, 6 to 56 ms, 32 ms on average |
| Relay if a direct path fails | Dallas |
| MagicDNS | on: `http://spark-99eb:8010` works too |

From the server's log (`100.65.128.57` is the Mac):

```
02:40:21  GET  /health    200      seven times within a second: the app's /status or Play start
02:40:21  POST /v1/chat   401
02:40:40  POST /v1/chat   401
02:43:36  POST /v1/voice  401      the held logo: a voice clip was uploaded
```

So the app reached the Spark, by text and by voice, and was refused only for the key. The user had the key at
02:44; the accepted requests are in the next section.

Where the Mac was at that moment (Louisiana or Los Angeles) is not known here. Measure again from Los Angeles:
`tools/acceptance.py --url http://100.118.119.14:8010` on the Mac prints the round trip next to the server time.

The server listens on every interface, so `http://130.39.93.164:8010` answers inside the campus network too.
The key guards `/v1/chat` and `/v1/voice`; `/health` is open. Whether a firewall is on could not be checked
(`ufw status` needs sudo).

## The app against the Spark (phase 3 of the hand-off)

From the server's log, 02:48:27 to 02:51:46: the app on the Mac, over Tailscale, `gemma4:12b`, `base.en`
without a prompt. Server time only; what the app showed and the round trip are for the Mac side to add.

| | Requests | Server time |
|---|---|---|
| Typed, answered by the rules | 17 | 0 to 16 ms |
| Typed, answered by the language model | 5 | 1.8 to 2.3 s |
| Spoken, answered by the rules | 5 | 400 to 616 ms |
| Spoken, answered by the language model | 1 | 3.0 s |
| Failed (`source: error`, 4xx, 5xx) after the key was set | 0 | |

- Typed: the acceptance phrases (layer, biceps, pause, faster, half speed, zoom out a lot, from behind, reset,
  hamstring, femur, "make her run", pose estimation on and read, the knee angle, pose estimation off) and
  "stop pose estimation and show the body and reset the view" as three actions.
- "what is the right knee angle" was answered "The right knee is at 16 degrees.": a live number from the
  app's pose estimation, not the 142 of the test context.
- Spoken, as Whisper wrote them: "Play.", "Pause", "Show me the right hamstring.", "Show me the left quadriceps
  and zoom in.", "What does the Gluteus Maximus do when I run?", "Activate real-time pose estimation and read
  the data." All six gave the right answer. Whether a person spoke them or the Mac session played synthetic
  clips is not known here.
- The language model: "what does the deltoid do", "hello, what can you do", "which muscles work the hardest
  when running", "show me the muscles and then tell me what the soleus is for" (switched the layer and
  answered), "can you make the avatar bigger" (zoomed in three steps and said it cannot change the size).
- `need_catalog` never appeared: the app sent its catalog with the first request, and it is cached as
  `.cache/catalogs/fb03245fda39.json`.
- These language-model times are higher than the 1.3 to 2.2 s measured before: the speech benchmark was
  using the CPU at the same time. Measure again on a quiet machine before quoting a number.

## Files added or changed in this session

| File | |
|---|---|
| `assistant/llm.py` | prompt rules, all names in the prompt, side fix, parts of earlier turns, warm-up retry |
| `assistant/rules.py` | judgement about an angle and described parts go to the model |
| `assistant/catalog.py` | reply wording |
| `server.py` | warm-up with retries |
| `tests/test_llm.py`, `tests/test_rules.py` | 18 more tests, two adapted to the new prompt |
| `tools/acceptance.py` | new: the acceptance table against a running server, with times |
| `tools/stt_bench.py`, `tools/stt_phrases.txt` | new: compare speech models on a folder of clips |
| `deploy/install-service.sh` | new: install the unit for the real folder |
| `docs/SPARK_NOTES.md`, `docs/TODO.md` | new |
| `docs/NETWORK.md`, `docs/SETUP_SPARK.md`, `docs/SERVER.md`, `docs/HANDOFF.md`, `README.md`, `CLAUDE.md`, `.env.example` | brought up to date |
