# CutPoint Architecture

## Overview

CutPoint is an event-driven agent system on Google Cloud. It fuses per-second trailer
audience-retention analytics (ClickHouse) with Gemini 3.5 Flash frame-level video understanding
(Vertex AI) to produce timestamped "Director's Notes": exactly where a trailer loses viewers,
why, and what to do about it.

The loop: ClickHouse answers WHERE (second 48, cohorts 18-34, 13.5% retention cliff), Gemini
multimodal answers WHY (tone shift / spoiler / pacing collapse in those exact frames), the agent
renders Director's Notes with recut recommendations.

Nothing has to ask for it. Cloud Scheduler ticks a Pub/Sub topic every 15 minutes, a watcher
re-runs cliff detection against live data, and only a genuinely new cliff triggers the pipeline.

## Deployed footprint

| Resource | Name | Access |
|---|---|---|
| Cloud Run | `cutpoint-api` | public reads, OIDC on the paid endpoints |
| Cloud Run | `cutpoint-watcher` | private, Pub/Sub push only |
| Cloud Run | `cutpoint-segment-extractor` | private, invoked by the runtime service account |
| Pub/Sub | `cutpoint-retention-scan`, `cutpoint-analyze` | OIDC push subscriptions |
| Firestore | native mode | reports, jobs, watch fingerprints |
| Cloud Scheduler | `cutpoint-retention-scan-tick` | `*/15 * * * *` |
| Cloud Storage | `<project>-cutpoint-media` | clips and rendered media, no public binding |
| Vertex AI | `gemini-3.5-flash` | location `global` |

Qualifying Google Cloud infrastructure services: **Cloud Run, Pub/Sub, Firestore**.

## Diagram

```mermaid
flowchart TB
    subgraph Trigger["Autonomous trigger"]
        Sched["Cloud Scheduler\n*/15 * * * *"]
        ScanTopic(["Pub/Sub\ncutpoint-retention-scan"])
        Watcher["Cloud Run: cutpoint-watcher\nre-runs changepoints.sql\nfingerprints the cliff set"]
        AnalyzeTopic(["Pub/Sub\ncutpoint-analyze\nack-deadline 600s"])
        Sched --> ScanTopic -- "push + OIDC" --> Watcher
        Watcher -- "only if the fingerprint changed" --> AnalyzeTopic
    end

    subgraph API["Cloud Run: cutpoint-api (FastAPI)"]
        Push[POST /pubsub/analyze]
        Jobs[POST /jobs, GET /jobs/id]
        Analyze[POST /analyze]
        Report[GET /report/id, /html, /trailers]
    end

    subgraph Agent["ADK SequentialAgent: 5 steps, order fixed in code"]
        direction TB
        Analyst["[1] analyst (LlmAgent)\nmcp-clickhouse, read-only\nwrapped: failure is not fatal"]
        Validator["[2] validator\nre-derives EVERY number\nreadonly=1, overrules step 1"]
        Extractor["[3] extractor\nclip +/-5s around each cliff"]
        Diagnostician["[4] diagnostician\ngemini-3.5-flash on the clip"]
        Reporter["[5] reporter\nDirector's Notes JSON, MD, HTML"]
        Analyst --> Validator --> Extractor --> Diagnostician --> Reporter
    end

    subgraph Data["Data plane"]
        CH[(ClickHouse\nraw_playback_events\nmv_second_viewers)]
        Seg["Cloud Run: segment-extractor\nFastAPI + ffmpeg, private"]
        Gemini[[Vertex AI\ngemini-3.5-flash]]
        FS[(Firestore\nreports, jobs, fingerprints)]
        GCS[(Cloud Storage\nclips, rendered HTML)]
    end

    subgraph Ingest["ingest/ write path, clickhouse-connect ONLY here"]
        Gen["generate.py\nsynthetic events + ground truth"]
        Load["load.py\nresumable batch loader"]
        Gen --> Load --> CH
    end

    UI["GitHub Pages UI"] --> Report
    UI --> Jobs
    AnalyzeTopic -- "push + OIDC" --> Push
    Push --> Agent
    Analyze --> Agent
    Watcher -- "fixed SQL, readonly" --> CH
    Watcher --> FS
    Analyst -- "McpToolset, read-only" --> CH
    Validator -- "readonly=1 connection" --> CH
    Extractor -- "HTTP + identity token" --> Seg
    Seg -- "gs:// clip URI" --> GCS
    Diagnostician -- "google-genai, vertexai=True" --> Gemini
    Reporter --> FS
    Reporter --> GCS
```

## The determinism boundary

Only steps 1 and 4 involve a model, and neither can put a number in the report.

Step 1 is an `LlmAgent` that transcribes `mcp-clickhouse` output into a Pydantic schema.
`output_schema` validates the shape of that transcription, not its numbers, so a transposed
digit or an invented cliff passes silently. Step 2 therefore re-runs the same fixed SQL over a
`readonly=1` connection and treats ClickHouse as authoritative for cliffs, `milestone_funnel`
and `overall_retention_end`, recording any divergence in the report as a `ValidationReport`.

On a real run the analyst reported one cliff at second 2, which does not exist in the database,
and missed all three real ones at 48, 23 and 69. The validator corrected all of it.

What this guarantees is provenance and reproducibility, not correctness. A wrong query would be
re-derived wrongly every time. Detector accuracy is evidenced separately, in
`tests/test_detector.py`, against injected ground truth with a false-positive control. Step 1 is
additionally wrapped so that its failure writes an empty result and continues rather than ending
the run, which makes the model a convenience rather than a correctness dependency.

Step 4 is used only for perception: describing what is on screen and proposing why viewers left.
It never decides what runs next.

## Read-only access

Agent-side ClickHouse access goes through `mcp-clickhouse`, which is read-only by construction.
The validator and the watcher run fixed `.sql` files rather than model-authored queries, and use
a connection pinned to `readonly=1` at the session level, so the server itself refuses a write
(verified: ClickHouse error code 164). `clickhouse-connect` in read-write mode is confined to
`ingest/`.

## Why these technology choices

- **ClickHouse** for the analytics plane: per-second, per-cohort retention aggregation over
  tens of millions of heartbeat events is exactly ClickHouse's sweet spot (`AggregatingMergeTree`
  + `uniqState`/`uniqMerge`), and its native `windowFunnel()` function gives a real showcase for
  the milestone funnel query without hand-rolled step counting.
- **mcp-clickhouse** as the ONLY agent-side ClickHouse access path: it is read-only by
  construction, which structurally prevents the agent from ever mutating production data --
  the write path (`ingest/`, `make schema`) is a separate, explicitly privileged path using
  `clickhouse-connect` directly.
- **Google ADK's `SequentialAgent`** for the pipeline: the four steps ALWAYS run in the same
  order. The LLM is never asked "what should I do next" -- it is asked "diagnose this clip" and
  "phrase this recommendation." This is a deliberate simplicity constraint (TASK.md rule 6) that
  makes the system auditable and reproducible for a hackathon demo.
- **Vertex AI Gemini** for multimodal perception: the diagnostician step passes each +/-5s clip
  as a `Part` and asks a structured question ("what happens on screen, why would these cohorts
  specifically leave here"). This is real video-frame reasoning, not metadata guessing.
- **FastAPI segment extractor** as its own service: keeps ffmpeg subprocess management outside
  the agent process, and is independently deployable to Cloud Run for a hosted demo.

## Data flow for one `/analyze` call

1. Frontend calls `POST /analyze {trailer_id}`.
2. `api/main.py` invokes `agent/run_pipeline.py`'s `run_live()`, which builds the ADK
   `root_agent` and runs it against an ADK `InMemoryRunner` session seeded with
   `trailer_id`, `video_path`, `title`, `duration_s`.
3. **analyst**: an `LlmAgent` with the `mcp-clickhouse` toolset. Its instruction pins it to
   executing exactly `sql/analysis/retention_curve.sql`, `changepoints.sql`,
   `cohort_divergence.sql`, `milestone_funnel.sql` for the given `trailer_id`, and returns a
   structured `AnalysisResult` (`output_schema`).
4. **extractor**: a deterministic ADK `BaseAgent` (no LLM call). For each cliff in
   `AnalysisResult.cliffs`, it POSTs to the segment extractor service for a `[second-5, second+5]`
   clip and writes an `ExtractionResult`.
5. **diagnostician**: for each clip, calls Gemini via `google-genai` (`vertexai=True`) with the
   clip bytes as a video `Part` plus a prompt naming the exact drop_pct and affected cohorts, and
   parses a structured `Diagnosis` (on_screen, hypothesis, severity, confidence).
6. **reporter**: merges everything into a `DirectorsNotes` pydantic model, writes
   `data/reports/{trailer_id}.json`, and renders Markdown + a self-contained HTML report (inline
   SVG retention curve, no CDN dependency) via `report/render.py`.
7. The API returns `{"report_id": trailer_id}`; the frontend then polls
   `GET /report/{trailer_id}` for the JSON.

## Determinism boundary

The LLM is used for exactly two things: (1) perception -- describing what a specific clip shows
and hypothesizing why it caused churn, and (2) language -- phrasing recommendations. It is never
used to decide which SQL to run, which clips to extract, or what order pipeline steps execute
in -- that is all fixed by `SequentialAgent` and plain Python control flow. See PROGRESS.md for
the specific implementation note on why `extractor`, `diagnostician`, and `reporter` are
implemented as `BaseAgent` subclasses wrapping independently-testable functions rather than as
`LlmAgent`s with freeform tool selection.
