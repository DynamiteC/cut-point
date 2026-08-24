# CutPoint — Devpost Submission Dossier

This document contains everything needed to complete and submit CutPoint for the **Google Cloud & Gemini Agentic Hackathon** before the August 31, 5:00 PM PT deadline.

---

## 1. Fast Submission Metadata & Form Answers

| Submission Field | Recommended Value / Answer |
|---|---|
| **Project Title** | **CutPoint** |
| **Elevator Pitch / Tagline** | Deterministic multi-agent intelligence that fuses per-second audience retention analytics (ClickHouse) with Gemini frame-level video perception to pinpoint *where* trailers lose viewers and *why* — emitting automated Director's Notes & recut plans. |
| **Category** | **Taskmaster** *(Autonomous, multi-step analytical & multimodal agent task execution)* |
| **Google Agent Framework** | **Google ADK (Agent Development Kit)** (`google-adk`, `SequentialAgent`, `LlmAgent`, `McpToolset`) |
| **Google AI Models** | **Gemini 2.5 / 1.5 Pro / Flash** via Vertex AI (`google-genai` with `vertexai=True`) |
| **Google Cloud Services** | **Google Cloud Vertex AI** (Gemini Multimodal Inference & Agent Engine), **Google Cloud Run** (Containerized ffmpeg Segment Extractor Service), **Google Cloud Storage** |
| **Start Date** | Hackathon build start date (e.g. *August 15, 2026*) |
| **Code Repository** | GitHub URL (Ensure public, or invite `testing@devpost.com` and `cloudhackathons@google.com`) |
| **Pre-existing Code Disclosures** | Open-source libraries (`fastapi`, `clickhouse-connect`, `google-adk`, `google-genai`, `mcp-clickhouse`, `pydantic`). All agent architecture, ClickHouse analytical SQL queries, pipeline stages, chaos suites, and report generator were built entirely new during the hackathon. Sample trailer video is Creative Commons (Attribution in `data/videos/ATTRIBUTION.txt`). |

---

## 2. Devpost Long-Form Submission Text (Copy & Paste Ready)

### 💡 Inspiration
Film & gaming marketing teams invest millions producing promotional trailers, yet audience drop-off optimization remains governed by subjective "gut feel" and noisy post-launch A/B tests. Standard analytics platforms report high-level view-through rates (VTR), but cannot tell an editor:
1. *Exact timestamped churn*: At which exact second did 18–24 demographic viewers abandon the video?
2. *Causal perception*: What specific visual, narrative, or auditory shift happened on-screen during those frames to trigger that exodus?

We built **CutPoint** to bridge the gap between **high-throughput per-second telemetry** and **multimodal visual understanding**. By pairing ClickHouse's speed with Gemini's frame-level reasoning through a deterministic Google ADK pipeline, CutPoint automates the generation of actionable, timestamped "Director's Notes" and recut strategies.

---

### 🚀 What It Does
CutPoint executes an automated 4-stage pipeline for any video asset:
1. **Analyst (ClickHouse via MCP)**: Analyzes millions of heartbeat events across demographics, computes retention curves, executes native `windowFunnel()` milestone metrics, and performs MAD-based statistical change-point detection to isolate drop-off cliffs (e.g., *Second 47: 22% cliff in 18-24 cohort*).
2. **Extractor (ffmpeg on Cloud Run)**: Extracts frame-accurate video slices (`[cliff - 5s, cliff + 5s]`) around each detected cliff.
3. **Diagnostician (Gemini on Vertex AI)**: Inspects the raw video bytes of each clip, identifying narrative pacing issues, premature spoiler reveals, visual clutter, or jarring tone shifts.
4. **Reporter**: Synthesizes the telemetry anomalies and Gemini's causal hypotheses into a comprehensive **Director's Notes** artifact (JSON, Markdown, and standalone interactive HTML with inline SVG retention graphs).

---

### 🛠️ How We Built It
- **Google Agent Development Kit (ADK)**: Built using ADK `SequentialAgent`, orchestrating a deterministic 4-step pipeline combining `LlmAgent` and specialized `BaseAgent` modules.
- **Model Context Protocol (MCP)**: All telemetry querying is mediated via `mcp-clickhouse` over stdio — enforcing read-only security isolation against the production analytics store.
- **Google Cloud Vertex AI & Gemini**: Multimodal video inference using `google-genai` with `vertexai=True` for frame-by-frame diagnostic reasoning.
- **Google Cloud Run**: Hosts the containerized segment extractor microservice running `ffmpeg` with autoscaling.
- **ClickHouse Cloud**: Optimized `AggregatingMergeTree` tables and materialized views (`uniqState`/`uniqMerge`) handling per-second time-series queries.
- **Resilience & Chaos Engineering**: Validated with a 36-test suite covering simulated network partitions, corrupted video streams, API concurrency stress tests (100 req/s), and mid-pipeline process termination.

---

### 🧗 Challenges We Ran Into
- **LLM Non-Determinism vs Pipeline Reliability**: Giving an LLM open-ended tool selection across a data pipeline introduces hallucinated parameters and variable execution order. We solved this by adopting a **deterministic orchestration boundary**: Google ADK's `SequentialAgent` guarantees the execution sequence, while Gemini is reserved strictly for perception and language generation.
- **Data Security Isolation**: Preventing the autonomous agent from performing destructive SQL queries on live telemetry. We enforced strict read-only guarantees by routing all database operations through the Model Context Protocol (`mcp-clickhouse`).
- **Frame-Accurate Video Alignment**: Aligning millisecond-level telemetry spikes with video keyframes required building an independent ffmpeg microservice with bounded memory limits.

---

### 🏆 Accomplishments We're Proud Of
- **Zero Hallucination on Analytics**: ClickHouse computes raw statistical truths (MAD z-scores, cohort divergence, funnels), guaranteeing that Gemini only diagnoses real, mathematically proven audience drop-offs.
- **Production-Grade Resilience**: Built with "fail loud, never corrupt" architecture, validated by automated chaos tests and zero data corruption guarantees.
- **Autonomous Director's Notes**: Delivers studio-quality editorial feedback and recut plans in under 2 minutes.

---

### 📚 What We Learned
- Fusing high-speed OLAP databases (ClickHouse) with Google Cloud Vertex AI creates a new paradigm: **Data-Grounded Perception Agents**.
- Separating workflow determinism from generative AI perception is the gold standard for enterprise agent reliability.

---

### 🔮 What's Next for CutPoint
- **Automated Video Re-editing (Veo Integration)**: Automatically generate generative B-roll replacements for flagged low-retention scenes using Google Veo.
- **Live Streaming & OTT Telemetry Ingestion**: Direct connectors for YouTube Studio Analytics, Twitch, and TikTok Video Performance APIs.
- **A/B Recut Prediction**: Pre-testing alternative cuts against synthetic audience agent personas before public release.

---

## 3. "Built With" Technology Tags
`google-adk`, `google-cloud-vertex-ai`, `gemini-multimodal`, `google-cloud-run`, `clickhouse`, `model-context-protocol-mcp`, `python`, `fastapi`, `ffmpeg`, `pytest`, `docker`

---

## 4. Spin-Up & Testing Instructions for Judges

### Option A: Local Quickstart (Smoke & Fixture Mode)
Judges can verify the entire pipeline, test suite, and report generation in under 60 seconds without cloud keys:

```bash
# 1. Clone repo & install dependencies
git clone <YOUR_REPO_URL>
cd cut-point
uv sync

# 2. Run full automated test & chaos validation suite (36 tests)
uv run pytest -v

# 3. Generate a sample Director's Notes HTML report from verified fixtures
uv run python scripts/render_fixture_report.py
# Open data/reports/fixture_report.html in any browser
```

### Option B: Live Pipeline Execution (GCP + ClickHouse)
To run live inference with Gemini on Vertex AI:

```bash
# 1. Configure environment variables in .env
cp .env.example .env
# Set GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION, and ClickHouse credentials

# 2. Authenticate with Google Cloud
gcloud auth application-default login
gcloud config set project <YOUR_PROJECT_ID>

# 3. Provision schema, ingest data, and run the live demo
make schema
make generate-data load
make demo

# 4. View generated report
open data/reports/demo_001.html
```

---

## 5. Demo Video Recording Checklist & Script (Under 4 Minutes)

- [ ] **0:00 - 0:15 (Hook)**: State the problem immediately: *"Marketing teams guess why viewers drop off trailers. CutPoint tells you WHERE they left with ClickHouse and WHY using Gemini video reasoning."*
- [ ] **0:15 - 1:15 (Agent in Action)**: Show `make demo` running. Point out the real-time ClickHouse change-point detection, clip extraction, and Gemini diagnosing frame-level issues.
- [ ] **1:15 - 2:15 (The Output)**: Walk through `data/reports/demo_001.html`. Show the retention curve, the second-47 drop-off cliff, Gemini's visual hypothesis, and the recut recommendation.
- [ ] **2:15 - 3:00 (GCP Backend Proof)**: 
  - Show the **Google Cloud Console** (Vertex AI / Cloud Run / Agent Engine logs).
  - Show the `SequentialAgent` Google ADK architecture diagram.
- [ ] **3:00 - 3:30 (Resilience & Wrap Up)**: Mention the 36-test automated suite and chaos testing passes.

---

## 6. Social Media Share Post (#AllThingsAgenticHackathon)

### On X (Twitter) & LinkedIn:
> 🎬 Excited to submit **CutPoint** to the #AllThingsAgenticHackathon!
> 
> CutPoint is an AI agent that pairs per-second audience retention analytics (@ClickHouse) with @GoogleCloud #Gemini multimodal frame perception to show video editors exactly WHERE their trailer loses viewers and WHY.
> 
> ⚡ Deterministic pipeline built with @Google's Agent Development Kit (ADK) + MCP
> 🧠 Frame-accurate video understanding via Vertex AI
> 📊 Auto-generated Director's Notes & recut plans
> 
> Check out our repo and demo! 🚀
> [Link to GitHub / Devpost]
