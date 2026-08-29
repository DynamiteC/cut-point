# Demo Video Script

One recording, submitted to both hackathons. Hard cap 4:00 (All Things Agentic evaluates only
the first four minutes). English audio or subtitles. Public on YouTube, not unlisted.

Everything below must be **live and unedited**. The single most damaging thing you can do is
show a slide where a terminal should be.

## Before you hit record

```bash
# 1. local stack up, data loaded
make ch-up && make verify-data
make preflight                     # every line PASS, especially vertex:model-available

# 2. clear the previous report so the run is visibly fresh
rm -f data/reports/demo_001.*

# 3. resume the scheduler ONLY for the recording, then pause it again after
gcloud scheduler jobs resume cutpoint-retention-scan-tick --location us-central1
```

Browser tabs, pre-loaded and logged in: Cloud Run service list, the `cutpoint-watcher` logs
page, Pub/Sub subscriptions, Vertex AI. Terminal font large enough to read at 720p.

---

## 0:00-0:30 The friction

Screen: a trailer playing, then a retention curve with a visible cliff.

> "A studio spends two million dollars on a trailer. Analytics tells them 40% of viewers
> didn't finish it. It does not tell them that the 18-to-24 audience left at second 48, and it
> certainly doesn't tell them what was on screen when they went. That second question is the
> only one an editor can act on. Answering it means joining a database to a pair of eyes."

## 0:30-1:00 What it is

Screen: `submission/gallery/01_system_architecture.jpg`.

> "CutPoint is an agent that answers both halves. ClickHouse computes where viewers left, from
> per-second playback events. Gemini 3.5 Flash on Vertex AI watches the ten seconds around each
> drop and explains why. Google ADK holds the pipeline in a fixed order. And it doesn't wait to
> be asked: Cloud Scheduler ticks Pub/Sub, a watcher checks for new cliffs, and only a genuinely
> new one starts the work."

## 1:00-2:20 The proof of action (the most important 80 seconds)

Screen: split, terminal left, `cutpoint-watcher` logs right.

```bash
gcloud pubsub topics publish cutpoint-retention-scan --message '{}'
```

> "That is the only thing a human does in this demo."

Let the watcher log appear. Point at the fingerprint comparison.

> "The watcher re-ran change-point detection, compared the cliff set against the fingerprint in
> Firestore, and found a new one. It published to a second topic. Nothing else needed a human."

Cut to the pipeline running (`make demo` locally, so the audience sees every step).

Then the payoff. Two beats, in this order.

**Beat one: no model produced a number.** Open `agent/cutpoint_agent/steps/analyst.py`.

> "Step one has no model in it. It reads the cliffs, the funnel and the retention curve straight
> from ClickHouse over a read-only connection. That is not a stylistic preference, it is the
> result of a measurement."

Open `tests/test_validator.py` and the committed evidence.

> "This step used to be a language model transcribing query results. On a real run it reported
> one cliff, at second two, which does not exist in the database, and missed all three that do.
> So we took the numbers off it. That test is kept purely as the record of why."

**Beat two: the model still has a job, and it is checked too.** Show the executive summary in
the rendered report.

> "Gemini writes this paragraph, from findings it did not compute. The first version of that step
> named the data in its prompt instead of including it, so it received nothing and invented a
> poorly rendered CGI explosion and three thousand five hundred viewers. Neither exists anywhere
> in this system. Now the prompt carries the real data, and any summary citing a second that was
> not detected as a cliff is thrown away and replaced by a template."

Open the rendered HTML. Scroll the retention curve, one cliff card, Gemini's description and the
recut recommendation.

## 2:20-3:05 Proof it runs on Google Cloud

Screen: the Google Cloud Console. Move quickly, do not narrate every click.

- Cloud Run service list: `cutpoint-api`, `cutpoint-watcher`, `cutpoint-segment-extractor`, with
  a `.run.app` URL visible.
- Pub/Sub: both topics and the two push subscriptions.
- Firestore: the `cutpoint_reports` and `cutpoint_watch` collections with real documents.
- Vertex AI logs showing the `gemini-3.5-flash` calls.
- Terminal: `curl https://cutpoint-api-nlfe4x5pnq-uc.a.run.app/trailers` returning JSON, then
  `curl -X POST .../analyze` returning **401**.

> "The read endpoints are public. The endpoints that cost money require a signed Google identity
> token from an allowlisted service account, because each one runs a Gemini video inference per
> cliff. There is a concurrency cap and a daily budget ceiling behind that."

## 3:05-3:40 The engineering (ClickHouse emphasis)

Screen: `sql/analysis/changepoints.sql` and the ClickHouse client.

> "The detection is not a threshold. It is a median-absolute-deviation z-score over the
> second-to-second delta, so it is robust to noisy counts, and cohort attribution only credits
> cohorts that actually contributed to the drop. Per-second unique viewers come from an
> AggregatingMergeTree with uniqState and uniqMerge, and the milestone funnel is native
> windowFunnel. Nine point nine million events, answered in milliseconds."

Optionally: `demo_control`, the trailer with no injected cliff, returning zero. The control that
proves the detector is not just finding what it was told to find.

## 3:40-4:00 Close

> "CutPoint turns a two-week manual review into an agent that notices on its own, checks its own
> model's arithmetic against the database, and writes the recut notes. Everything you saw is on
> Cloud Run, and the repository has a five-command local setup and a scripted deploy."

---

## After recording

```bash
gcloud scheduler jobs pause cutpoint-retention-scan-tick --location us-central1
```

Leaving it enabled wakes the watcher 96 times a day on your billing account.

## Checklist

- [ ] Under 4:00
- [ ] Live terminal output, not slides
- [ ] A `.run.app` URL visible on screen
- [ ] Cloud Run dashboard, Pub/Sub, and Vertex AI logs all shown
- [ ] Both payoff beats shown: no model in the numeric path, and the grounding check
- [ ] The 401 shown, and explained as deliberate
- [ ] Public on YouTube, English audio or subtitles
- [ ] Scheduler paused again afterwards
