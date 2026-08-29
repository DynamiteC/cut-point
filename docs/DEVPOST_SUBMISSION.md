# CutPoint: Devpost Submission Dossier

Everything needed to complete the submission for the **All Things Agentic Hackathon**
(deadline 31 August 2026, 5:00pm PDT). The same codebase is also entered in Agentic Cinema
(Google Cloud, ClickHouse track, deadline 9 September 2026); both entries are disclosed in each.

---

## 1. Submission metadata

| Field | Value |
|---|---|
| Project title | CutPoint |
| Category | **The Taskmaster** |
| Tagline | An agent that watches audience-retention data on its own, and when a trailer develops a new drop-off cliff, diagnoses the exact frames responsible and writes the recut notes. No human asks it to. |
| Gemini model | `gemini-3.5-flash` via **Vertex AI** (`google-genai`, `vertexai=True`, location `global`) |
| Google agent framework | **Google ADK** (`SequentialAgent`, `LlmAgent`, `BaseAgent`, `McpToolset`) and the **GenAI SDK** (`google-genai`) |
| Google Cloud infrastructure | **Cloud Run**, **Pub/Sub**, **Firestore** (qualifying) plus Cloud Scheduler and Cloud Storage (supporting) |
| Repository | https://github.com/DynamiteC/cut-point |
| Build window | 19-31 August 2026, inside the submission period |
| Pre-existing code | None. Open-source libraries only: `google-adk`, `google-genai`, `google-cloud-aiplatform`, `mcp-clickhouse`, `fastapi`, `uvicorn`, `pydantic`, `clickhouse-connect`, `jinja2`, `numpy`. Sample trailer footage is Creative Commons; attribution in `data/videos/ATTRIBUTION.txt`. |

Note on the mandatory technology check: Vertex AI and Cloud Storage are used heavily but are
**not** counted toward the Google Cloud infrastructure requirement, because the rules name Cloud
Run, Cloud SQL, Firestore, GKE and Pub/Sub. CutPoint uses three of those five.

---

## 2. Long-form submission text

### Inspiration

Studios spend millions on trailers and then optimise them on gut feel. Analytics platforms report
view-through rate, which tells an editor that people left but never where or why. The two
questions an editor actually has are:

1. At which exact second did the 18-24 cohort abandon this cut?
2. What was on screen in those frames that made them go?

The first is a database question. The second is a perception question. Nothing joined them, so we
built an agent that does, and then made it run without being asked.

### What it does

Cloud Scheduler ticks a Pub/Sub topic every 15 minutes. The job is created paused, and is
resumed only for a demonstration, because an always-on tick is spend with no reader. A watcher on Cloud Run re-runs
change-point detection over live ClickHouse data, fingerprints the resulting cliff set, and
compares it to the last fingerprint in Firestore. If nothing changed it does nothing, which is
what stops it becoming an expensive cron job. If a genuinely new cliff appeared it publishes to a
second topic, and the pipeline runs end to end with no human involved:

1. **Analyst** reads cliffs, funnel and retention straight from ClickHouse over a `readonly=1`
   connection. It is a deterministic step containing no model call.
2. **Extractor** clips five seconds either side of each cliff with ffmpeg on Cloud Run.
3. **Diagnostician** sends each clip to Gemini 3.5 Flash and asks what is on screen and why it
   cost viewers.
4. **Narrator** has Gemini turn the verified findings into a paragraph an editor can act on,
   with `mcp-clickhouse` available for supporting context. Its output is rejected if it cites a
   second that was not detected as a cliff.
5. **Reporter** writes Director's Notes (JSON, Markdown, self-contained HTML) to Firestore and
   Cloud Storage, with a per-cliff recut action and target range.

### How we built it

- **Google ADK** provides a `SequentialAgent` whose step order is fixed in code. An LLM never
  chooses what runs next; it is used for perception and language only.
- **Model Context Protocol** (`mcp-clickhouse`) mediates all agent-side database access, so the
  agent's query path is read-only by construction.
- **Vertex AI** serves Gemini 3.5 Flash. In the cloud the clip is passed by `gs://` URI rather
  than inline bytes, which keeps whole videos out of the service's memory.
- **Cloud Run** hosts three services from two images. The API and watcher share one image and
  differ only by `APP_MODULE`.
- **Pub/Sub** decouples detection from diagnosis, with OIDC push subscriptions and a 600-second
  ack deadline covering a full pipeline run.
- **Firestore** holds reports, job state and the watch fingerprints that make the loop idempotent.
- **ClickHouse** does the statistics: `AggregatingMergeTree` with `uniqState`/`uniqMerge` for
  per-second unique viewers, MAD-based robust z-scores for change-point detection, and native
  `windowFunnel()` for the milestone funnel.

### Challenges

**The model was wrong about the data, and we could prove it.** The analyst step began as an
`LlmAgent` transcribing tool output into a schema. `output_schema` validates the shape of a
transcription, not its numbers. On a real run it reported a single cliff at second 2, which does
not exist in the database, and missed all three real ones at 48, 23 and 69:

```
llm cliffs claimed : 1
db-verified cliffs : 3
second 48 / 23 / 69 : missed by the analyst, restored from ClickHouse
second 2            : reported by the analyst, absent from ClickHouse
```

**The model invented findings the moment we stopped feeding it data.** The narrator's first
version named the session-state keys in its instruction instead of interpolating them, so ADK
passed it nothing and it confidently described a "poorly rendered CGI explosion" and "3,525
viewers" -- neither in the diagnoses, and viewer counts appear nowhere in the system. The prompt
now interpolates the real data and a grounding check rejects any summary citing a second that
was not detected as a cliff.

**Gemini refused to answer, and it was our fault.** We sent a clip covering trailer seconds 43 to
53 while telling the model "describe second 48". The clip's own timeline starts at 00:00, so the
model correctly replied that it could not describe a moment outside the footage, and that refusal
was written into the report as a diagnosis. Making the prompt clip-relative fixed it.

**Deploying broke assumptions that worked locally.** The extractor returned a filesystem path
from its own container that the diagnostician, a different Cloud Run service, then tried to open.
Every extraction was being discarded. `gcloud run deploy --source` falls back to `.gitignore`
when no `.gcloudignore` exists, so a file the build needed was silently never uploaded. Google's
frontend intercepts `/healthz` and answers 404 before the container sees it. And an unreachable
database returned 5xx to a Pub/Sub push, which Pub/Sub retries, turning one scheduled tick into a
billing loop.

### What we learned

Fusing an OLAP database with a multimodal model creates a specific failure mode worth naming: the
model is fluent about numbers it did not compute. The fix is not a better prompt, it is an
architectural boundary. Let the database own every number, let the model own perception and
language, and make the pipeline survive the model failing entirely. Ours does: if the analyst
hallucinates, times out or returns truncated JSON, the report is still correct.

### What is next

- Veo-generated replacement shots and Lyria alternative music beds for the highest-severity cliff
- Connectors for YouTube Studio, Twitch and TikTok telemetry instead of synthetic events
- Pre-testing alternative cuts against synthetic audience personas before release

---

## 3. Built with

`google-adk`, `google-genai`, `vertex-ai`, `gemini`, `cloud-run`, `pub-sub`, `firestore`,
`cloud-scheduler`, `cloud-storage`, `clickhouse`, `model-context-protocol`, `python`, `fastapi`,
`ffmpeg`, `pydantic`, `docker`, `time-series`, `olap`, `video-analytics`

---

## 4. Demo video (4 minutes)

See [demo-video-script.md](demo-video-script.md). It must show, unedited:

1. The friction: an editor who knows viewers left but not where or why.
2. The architecture diagram.
3. A live run: publish to `cutpoint-retention-scan`, the watcher logging a fingerprint diff, the
   Firestore job moving to done, and the rendered Director's Notes, with no human step between.
4. Proof it runs on Google Cloud: the Cloud Run dashboard, the Pub/Sub subscriptions, and Vertex
   AI logs.
5. The validation block in the report, showing the model was overruled by the database.
