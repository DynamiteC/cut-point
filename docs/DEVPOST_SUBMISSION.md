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
| Google agent framework | **Google ADK** (`SequentialAgent`, `LlmAgent`, `BaseAgent`, `McpToolset`); the **GenAI SDK** (`google-genai`) is the model client for the direct Vertex AI calls, not a second agent framework |
| Google Cloud infrastructure | **Cloud Run**, **Pub/Sub**, **Firestore** (qualifying) plus Cloud Scheduler and Cloud Storage (supporting) |
| Repository | https://github.com/DynamiteC/cut-point |
| Build window | 19-31 August 2026, inside the submission period |
| Pre-existing code | None. Open-source libraries only: `google-adk`, `google-genai`, `google-cloud-aiplatform`, `mcp-clickhouse`, `fastapi`, `uvicorn`, `pydantic`, `clickhouse-connect`, `jinja2`, `numpy`. Sample trailer footage is Creative Commons; attribution in `data/videos/ATTRIBUTION.txt`. |

Note on the mandatory technology check: CutPoint uses Vertex AI and Cloud Storage often. But it
does not count them toward the Google Cloud infrastructure requirement. The rules name Cloud Run,
Cloud SQL, Firestore, GKE and Pub/Sub. CutPoint uses three of those five: Cloud Run, Firestore
and Pub/Sub.

---

## 2. Long-form submission text

### Inspiration

Studios spend millions on trailers. Then they change the trailers on instinct. Analytics tools
report the view-through rate. That number tells an editor that people stopped watching. It does
not tell the editor where they stopped, or why. An editor has two questions:

1. At which second did the 18-24 group stop watching this cut?
2. What was on the screen in those frames?

The first question is a database question. The second question is a perception question. No tool
answered both. So we built an agent that answers both. The agent also runs on its own. No person
starts it.

### What it does

Cloud Scheduler sends a message to a Pub/Sub topic every 15 minutes. The scheduler job starts in
the paused state. We resume it only for a demonstration, because a tick with no reader only costs
money. A watcher on Cloud Run then does three things. It runs the change-point detection again on
the live ClickHouse data. It makes a fingerprint of the set of cliffs. It compares that
fingerprint with the last fingerprint in Firestore. If the fingerprint did not change, the
watcher stops. This is what keeps it from becoming an expensive cron job. If a new cliff appeared,
the watcher sends a message to a second topic. The pipeline then runs from start to end. No person
is involved:

1. **Analyst** reads the cliffs, the funnel and the retention directly from ClickHouse. It uses a
   `readonly=1` connection. This step uses no model.
2. **Extractor** cuts a clip five seconds before and after each cliff. It uses ffmpeg on Cloud Run.
3. **Diagnostician** sends each clip to Gemini 3.5 Flash. It asks what is on the screen, and why
   the clip lost viewers.
4. **Narrator** uses Gemini to write the verified findings into one paragraph. An editor can act
   on that paragraph. The step can also read `mcp-clickhouse` for more context. The system rejects
   the paragraph if it names a second that is not a detected cliff.
5. **Reporter** writes the Director's Notes to Firestore and Cloud Storage, in JSON, Markdown and
   self-contained HTML. Each cliff gets a recut action and a target range.

### How we built it

- **Google ADK** gives us a `SequentialAgent`. The code sets the step order. No model chooses the
  next step. The model does perception and language only.
- **Model Context Protocol** (`mcp-clickhouse`) handles all database access from the agent. This
  access path is read-only by design.
- **Vertex AI** serves Gemini 3.5 Flash. In the cloud, the system sends the clip as a `gs://` URI,
  not as inline bytes. This keeps whole videos out of the service memory.
- **Cloud Run** runs three services from two images. The API and the watcher share one image. They
  differ only in the `APP_MODULE` value.
- **Pub/Sub** separates detection from diagnosis. It uses OIDC push subscriptions and a 600-second
  ack deadline. This deadline covers one full pipeline run.
- **Firestore** stores the reports, the job state and the watch fingerprints. The fingerprints
  make the loop safe to repeat.
- **ClickHouse** does the statistics. It uses `AggregatingMergeTree` with `uniqState`/`uniqMerge`
  for the count of unique viewers per second. It uses MAD-based z-scores for change-point
  detection. It uses the native `windowFunnel()` for the milestone funnel.

### Challenges

**The model was wrong about the data, and we could show it.** The analyst step was an `LlmAgent`
at first. It copied the tool output into a schema. `output_schema` checks the shape of the copy.
It does not check the numbers. On a real run, the model reported one cliff at second 2. Second 2
is not a cliff in the database. The model also missed all three real cliffs, at seconds 48, 23
and 69:

```
llm cliffs claimed : 1
db-verified cliffs : 3
second 48 / 23 / 69 : missed by the analyst, restored from ClickHouse
second 2            : reported by the analyst, absent from ClickHouse
```

**The model invented findings when we stopped giving it data.** The first narrator named the
session-state keys in its instruction. It did not read the values. So ADK gave it no data. The
model then described a "poorly rendered CGI explosion" and "3,525 viewers". Neither is in the
diagnoses. The system has no viewer counts at all. We changed the prompt to read the real values.
A grounding check now rejects any summary that names a second that is not a detected cliff.

**Gemini refused to answer, and the cause was our prompt.** We sent a clip for trailer seconds 43
to 53. We told the model to describe second 48. But the clip timeline starts at 00:00. So the
model correctly said that it cannot describe a moment outside the clip. The system wrote that
refusal into the report as a diagnosis. We made the prompt use clip-relative time. This fixed the
problem.

**Deployment broke things that worked on a local machine.** The extractor returned a file path
from its own container. The diagnostician is a different Cloud Run service. It tried to open that
path and failed. So every extraction was lost. Also, `gcloud run deploy --source` uses
`.gitignore` when there is no `.gcloudignore`. So the build did not upload a file that it needed.
Also, the Google frontend catches `/healthz` and returns 404 before the container sees the
request. Also, an unreachable database returned a 5xx response to a Pub/Sub push. Pub/Sub retries
a 5xx. So one scheduled tick became a billing loop. We fixed each of these.

### Accomplishments that we are proud of

- **No model produces a number.** ClickHouse computes every statistic: the MAD z-scores, the
  cohort divergence, the funnels. Gemini only diagnoses cliffs the database has already proven.
- **Fail loud, never corrupt.** The architecture surfaces every failure as a specific error and
  writes no partial report. Chaos tests validate this across four failure scenarios.
- **Autonomous Director's Notes.** The watcher completes the workflow with no human step, and a
  concurrency cap and a daily budget ceiling bound the cost.

### What we learned

When you join an OLAP database with a multimodal model, one failure mode appears: the model
speaks with confidence about numbers that it did not compute. A better prompt does not fix this.
An architectural boundary fixes it. Let the database own every number. Let the model own
perception and language. Make the pipeline work even when the model fails completely. Ours does.
If the analyst gives a wrong answer, times out, or returns broken JSON, the report is still
correct. Separating workflow determinism from generative perception is what makes the agent
reliable.

### What is next

- Add Veo replacement shots and Lyria music beds for the cliff with the highest severity.
- Add connectors for YouTube Studio, Twitch and TikTok data, in place of synthetic events.
- Test alternative cuts against synthetic audience personas before release.

---

## 3. Built with

`google-adk`, `google-genai`, `vertex-ai`, `gemini`, `cloud-run`, `pub-sub`, `firestore`,
`cloud-scheduler`, `cloud-storage`, `clickhouse`, `model-context-protocol`, `python`, `fastapi`,
`ffmpeg`, `pydantic`, `docker`, `time-series`, `olap`, `video-analytics`

---

## 4. Demo video (4 minutes)

See [demo-video-script.md](demo-video-script.md). The video must show these items, with no edits
between them:

1. The problem: an editor knows that viewers left, but not where or why.
2. The architecture diagram.
3. A live run. Send a message to `cutpoint-retention-scan`. Show the watcher logging the
   fingerprint difference. Show the Firestore job move to done. Show the rendered Director's
   Notes. Show no human step between these.
4. Proof that it runs on Google Cloud: the Cloud Run dashboard, the Pub/Sub subscriptions, and
   the Vertex AI logs.
5. The validation block in the report. It shows that the database overruled the model.
