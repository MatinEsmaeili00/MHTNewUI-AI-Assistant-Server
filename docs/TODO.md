# To do

One list for both machines. Newest state first; tick a line when it is done and say where it was checked.
Details and numbers: `docs/SPARK_NOTES.md`. How things are meant to work: `docs/HANDOFF.md`.

Last update: 2026-11-11, on the DGX Spark.

## Done on the Spark (2026-11-11)

- [x] Machine check: Ubuntu aarch64, Python 3.12.3, Ollama 0.33.2, port 8010 free (8002 and 8765 are taken).
- [x] Repository cloned to `~/DigitalHuman_ai/MHTNewUI-AI-Assistant-Server`, environment installed,
      `av` is 17.1.0.
- [x] Tests: 698 passed as they came from the Mac; 716 after the changes below.
- [x] `.env` with a new API key (mode 600, not in git).
- [x] Language model: `gemma4:e4b` accepts the JSON schema; `gemma4:12b` pulled, compared and chosen.
- [x] Acceptance table of `docs/HANDOFF.md`: all 17 phrases pass (`tools/acceptance.py`), with the server
      reached through its Tailscale address.
- [x] Language-model path tuned: prompt rules (answer or act, never both; no unasked actions), every part
      name in the prompt, a side only when the user said one, follow-ups without a name.
- [x] Two rule changes: "is my knee angle normal ..." and "the muscle that lifts the arm" go to the language
      model instead of being half-answered by the rules.
- [x] Speech recognition measured on 216 synthetic clips (36 phrases, 6 voices), several models.
- [x] Service: systemd user unit `mht-assistant`, enabled, running; linger is on; the model warm-up retries
      when Ollama starts later than the server.
- [x] Tailscale on the Spark (`spark-99eb`, `100.118.119.14`), same account as the Mac
      (`internaladmins-macbook-pro`, `100.65.128.57`). The two see each other directly, 6 to 56 ms.
- [x] `/v1/chat`, `/v1/voice` and the key check (401) verified through the Tailscale address, from the Spark.
- [x] **The app on the Mac against the Spark**, 02:48 to 02:52: 28 requests, typed and spoken, rules and
      language model, all answered (`docs/SPARK_NOTES.md`, "The app against the Spark").

## Open: needs a person

- [ ] **Round trip from the Mac.** The server's side of the app's requests is in the notes; the network's
      share is not. In the server repository on the Mac (`git pull`):
      `ASSISTANT_API_KEY=<key> .venv/bin/python tools/acceptance.py --url http://100.118.119.14:8010 --extra`
      and write the round-trip column into `docs/SPARK_NOTES.md` ("The link"). Again from Los Angeles.
- [ ] **The rest of the table in the app.** Not seen in the log yet: "show me the skeleton", "zoom in", and
      most of the table by voice. The key for another device: `grep '^ASSISTANT_API_KEY=' .env` on the Spark.
- [ ] **Real voices.** Everything spoken so far was synthetic. Record the phrases of
      `tools/stt_phrases.txt` (the app keeps the last clip as `last_voice.wav` on the Spark; or record WAV files
      named `NN_<name>.wav`) and run `tools/stt_bench.py`. Decide the speech model from that, not from the
      synthetic clips.
- [ ] **Reboot check.** After the next reboot of the Spark: `curl localhost:8010/health` and
      `tailscale status` without logging in first. Not done because the machine runs other projects.
- [ ] **Tailscale key expiry.** A device is signed out after 180 days by default. In the Tailscale admin page,
      Machines, `spark-99eb`: "Disable key expiry", or the link stops in May 2027.
- [ ] **Firewall.** The server also answers on the campus address (`130.39.93.164:8010`). To allow only
      Tailscale: `sudo ufw status`, then `sudo ufw allow in on tailscale0 to any port 8010 proto tcp` (and make
      sure SSH stays allowed before enabling `ufw`).
- [ ] **iPad / packaged build.** Needs `https://`: `sudo tailscale serve --bg 8010` on the Spark, HTTPS
      certificates enabled in the Tailscale admin page, then `/server https://spark-99eb.tail6962d3.ts.net`.
      And `NSMicrophoneUsageDescription` in the app's Info.plist. Not started.

## Open: server (Spark session)

- [ ] Finish the speech comparison (`medium.en`, `large-v3-turbo`, `distil-large-v3`, the longer prompt), put the rows into `docs/SPARK_NOTES.md`, then switch `STT_PROMPT`
      on in `.env` and restart the service.
- [ ] Language-model times on a quiet machine: the 1.8 to 3.0 s seen with the app were measured while the
      speech benchmark was running.
- [ ] Speech on the GPU. The `ctranslate2` wheel for aarch64 has no CUDA, so Whisper runs on the CPU. Only worth
      doing if real voices need a model that is too slow on the CPU.
- [ ] Answers from the app's own text. The model answers anatomy from memory. Sending the atlas descriptions
      with the catalog (a protocol addition, both repositories) would make it answer from the app's text.
- [ ] Described parts are not always right: "the muscle that lifts the arm" gives Biceps brachii, not the
      deltoid. Better with the descriptions above.
- [ ] Spoken answers (`/v1/speak`). Piper ran well on the Spark for the test clips and would do; needs a
      protocol addition and a small client change.
- [ ] Follow-ups ("now the other side", "zoom in on it") were only tried with a fixed context. Check them with
      the app, which sends `context.selected_part`.
- [ ] `gemma4:e4b` is still installed (9.6 GB). Remove it with `ollama rm gemma4:e4b` unless the RosOrinPro
      project uses it.

## Open: Unreal side (Mac session)

- [ ] `git pull` in the server repository. `docs/PROTOCOL.md` did not change, so the app needs no change.
- [ ] After the first session against the Spark: add to `docs/SPARK_NOTES.md` what the app showed (latency in
      the chat, voice, `need_catalog` once per session).
- [ ] The project's own repository and Perforce: the changes are still uncommitted there (see the Mac
      session's summary).
