# Demo Video Walkthrough

One recording, submitted to both hackathons. Hard cap **4:00** (All Things Agentic evaluates only
the first four minutes). English audio or subtitles. Public on YouTube, not unlisted.

Everything below is live. The single most damaging thing you can do is show a slide where a
terminal should be.

---

## Setup, 10 minutes before you record

```bash
cd ~/Workspace/SS/cut-point

make ch-up                  # local ClickHouse
make preflight              # every line PASS, especially vertex:model-available
export PATH="$HOME/google-cloud-sdk/bin:$PATH"

# resume the scheduler ONLY for the recording
gcloud scheduler jobs resume cutpoint-retention-scan-tick --location us-central1
```

**Windows to arrange before you hit record.** Fumbling between tabs on camera reads as
unpreparedness, and you have no seconds to spare.

| # | Window | Pre-loaded with |
|---|---|---|
| 1 | Terminal, large font | `cd` into the repo, cleared |
| 2 | Browser tab | https://dynamitec.github.io/cut-point/app.html |
| 3 | Browser tab | Cloud Run service list (all three services visible) |
| 4 | Browser tab | Pub/Sub subscriptions |
| 5 | Browser tab | Cloud Run logs for `cutpoint-watcher` |
| 6 | Browser tab | Vertex AI, so `gemini-3.5-flash` calls are visible |
| 7 | Editor | `agent/cutpoint_agent/steps/analyst.py` open |

Terminal font at 16pt or larger. A judge watching at 720p cannot read 12pt.

---

## 0:00-0:30 The friction

**Screen:** the trailer playing, then the retention curve on the app page.

> "A studio spends two million dollars on a trailer. Analytics tells them forty percent of
> viewers did not finish it. It does not tell them that the eighteen-to-twenty-four audience left
> at second forty-eight, and it certainly does not tell them what was on screen when they went.
> That second question is the only one an editor can act on. Answering it means joining a
> database to a pair of eyes."

## 0:30-1:00 What it is

**Screen:** `submission/gallery/01_system_architecture.jpg`.

> "CutPoint answers both halves. ClickHouse computes where viewers left, from per-second playback
> events. Gemini 3.5 Flash on Vertex AI watches the ten seconds around each drop and explains
> why. Google ADK holds the pipeline in a fixed order. And it does not wait to be asked: Cloud
> Scheduler ticks Pub/Sub, a watcher checks for new cliffs, and only a genuinely new one starts
> the work."

## 1:00-1:45 Proof of autonomous action

**Screen:** split. Terminal left, `cutpoint-watcher` logs right.

```bash
gcloud pubsub topics publish cutpoint-retention-scan --message '{}'
```

> "That is the only thing a human does in this demo."

Wait for the log line. Point at the fingerprint comparison.

> "The watcher re-ran change-point detection against live data, compared the cliff set to the
> fingerprint in Firestore, and decided whether anything changed. That fingerprint is the
> difference between an agent and a cron job. Without it, every tick would re-diagnose the same
> cliffs and re-spend on Gemini forever."

**Then run the pipeline so the audience sees every step:**

```bash
make demo
```

> "Five steps, in an order fixed by code and never chosen by a model."

## 1:45-2:45 The two payoff beats

This is the part that separates this from a wrapper around an API. Do not rush it.

**Beat one: no model produced a number.** Switch to the editor, `analyst.py`.

> "Step one has no model in it. It reads the cliffs, the funnel and the retention curve straight
> from ClickHouse over a read-only connection. That is not a stylistic preference. It is the
> result of a measurement."

Switch to `tests/test_validator.py`.

> "This step used to be a language model transcribing query results. On a real run it reported
> one cliff, at second two, which does not exist in the database, and it missed all three that
> do. So we took the numbers off it. That test is kept purely as the record of why."

**Beat two: the model still has a job, and its output is checked too.** Switch to the app page,
`demo_001`. Point at the executive summary, then at the provenance strip.

> "Gemini writes this paragraph, from findings it did not compute. Six hundred rows read, three
> cliffs computed by ClickHouse, and zero numbers produced by a model."

> "The first version of that step named the data in its prompt instead of including it. It
> received nothing, and confidently described a poorly rendered CGI explosion and three thousand
> five hundred viewers. Neither exists anywhere in this system. Now the prompt carries the real
> data, and any summary citing a second that was not detected as a cliff is thrown away and
> replaced by a template."

**Then click through the trailers.** This is where four reports earn their keep.

- `demo_002`: *"Different trailer, different footage. Gemini describes a bald attacker striking a
  red-haired protagonist on a snowy slope amidst extreme shaky-cam. It is not repeating itself."*
- `demo_control`: *"And this one is the control. Zero cliffs were injected into its data, the
  detector finds none, and the agent says so plainly instead of inventing a finding. That is how
  you know the detector is not simply finding whatever it was told to find."*

## 2:45-3:25 Proof it runs on Google Cloud

**Screen:** Google Cloud Console. Move briskly, do not narrate every click.

- Cloud Run: `cutpoint-api`, `cutpoint-watcher`, `cutpoint-segment-extractor`, `.run.app` URL visible
- Pub/Sub: both topics, both push subscriptions
- Firestore: `cutpoint_reports` and `cutpoint_watch` with real documents
- Vertex AI logs: the `gemini-3.5-flash` calls

**Then, in the terminal:**

```bash
curl https://cutpoint-api-nlfe4x5pnq-uc.a.run.app/trailers
curl -X POST https://cutpoint-api-nlfe4x5pnq-uc.a.run.app/analyze \
  -H 'content-type: application/json' -d '{"trailer_id":"demo_001"}'
```

> "Reading is public, which is why that web page works with no credential. Running costs money,
> so it returns 401. Each run is a Gemini video inference per cliff. It needs a signed Google
> identity token from an allowlisted service account, and behind that there is a concurrency cap
> and a daily budget ceiling."

## 3:25-3:50 The engineering

**Screen:** `sql/analysis/changepoints.sql`.

> "Detection is not a threshold. It is a median-absolute-deviation z-score over the
> second-to-second delta, so it is robust to noisy counts, and cohort attribution only credits
> cohorts that actually contributed to the drop. Per-second unique viewers come from an
> AggregatingMergeTree using uniqState and uniqMerge, and the milestone funnel is native
> windowFunnel. Nine point nine million events, answered in milliseconds."

## 3:50-4:00 Close

> "CutPoint turns a two-week manual review into an agent that notices on its own, keeps its own
> model away from the arithmetic, and writes the recut notes. Everything you saw is deployed on
> Cloud Run, and the repository has a five-command local setup and a scripted deploy."

---

## Immediately after recording

```bash
gcloud scheduler jobs pause cutpoint-retention-scan-tick --location us-central1
```

Leaving it enabled wakes the watcher 96 times a day on your billing account.

---

## Checklist

- [ ] Under 4:00
- [ ] Live terminal output, never a slide standing in for one
- [ ] A `.run.app` URL visible on screen at least once
- [ ] Cloud Run dashboard, Pub/Sub, Firestore and Vertex AI logs all shown
- [ ] Both payoff beats delivered: no model in the numeric path, and the grounding check
- [ ] `demo_control` shown finding nothing
- [ ] The 401 shown and explained as deliberate
- [ ] Audio levels checked on the first 10 seconds before recording the whole thing
- [ ] Public on YouTube, English audio or subtitles
- [ ] Scheduler paused again

---

## Recording tools

**Recommended: [OBS Studio](https://obsproject.com)** (free, open source, no watermark, no time
limit). It is worth the twenty minutes of setup for one reason: **scenes**. You pre-build
"Terminal", "Browser", "Console", "Editor" as switchable scenes and change between them with a
hotkey, instead of alt-tabbing on camera. It also captures your microphone and system audio on
separate tracks, so you can fix a level afterwards without re-recording.

Minimum viable OBS setup for this video:
1. Settings, Output, Recording Quality "High", format `mp4`, encoder "Apple VT H264 Hardware".
2. One Display Capture source. Add a second scene per window you listed above.
3. Add a Mic/Aux input. Speak a test line and keep the meter peaking around -12dB.
4. On macOS you must grant Screen Recording permission in System Settings, Privacy and Security,
   and restart OBS once. Capturing system audio also needs a virtual device such as
   [BlackHole](https://existential.audio/blackhole/) (free), though for narration over a screen
   recording your microphone alone is usually enough.

**Simplest alternative: the built-in recorder.** `Cmd+Shift+5`, choose "Record Entire Screen",
and under Options pick your microphone. Zero setup and completely adequate if you are willing to
alt-tab between windows on camera. This is the right choice if you are recording in the next hour.

**Editing:** [DaVinci Resolve](https://www.blackmagicdesign.com/products/davinciresolve) is free
and far more than you need; iMovie ships with macOS and will handle trims and titles fine. If the
take is clean, do not edit at all. An unedited take is explicitly what the judging criteria ask
for.

**Avoid:** Kap is excellent but aimed at short silent clips; Loom's free tier caps length and
adds branding; Screen Studio is genuinely good but paid.

One practical note: record a thirty-second throwaway first and watch it back. Font too small and
audio too quiet are the two failures that force a full re-record, and both are obvious in
thirty seconds.
