"""Thin REST facade for the frontend (see TASK.md section 8 / docs/frontend-spec.md).

POST /analyze {trailer_id}   -> runs the pipeline synchronously -> {report_id}
GET  /report/{trailer_id}     -> DirectorsNotes JSON
GET  /report/{trailer_id}/html -> rendered HTML
GET  /trailers                -> known trailer ids from data/ground_truth.json
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi import Path as PathParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = REPO_ROOT / "data" / "reports"
GROUND_TRUTH_PATH = REPO_ROOT / "data" / "ground_truth.json"

# trailer_id flows into filesystem paths (data/reports/{trailer_id}.json) and into SQL query
# text built by the analyst LLM (sql/analysis/*.sql templates) -- restrict to a safe charset
# to rule out path traversal and SQL-injection-shaped payloads at the API boundary.
TRAILER_ID_PATTERN = r"^[a-zA-Z0-9_-]{1,64}$"

app = FastAPI(title="CutPoint API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    trailer_id: str = Field(..., pattern=TRAILER_ID_PATTERN)


class AnalyzeResponse(BaseModel):
    report_id: str


def default_pipeline_runner(trailer_id: str) -> dict:
    """Runs the real ADK pipeline synchronously. Overridable via
    app.state.pipeline_runner for tests (see TASK.md Phase 8: pipeline mocked).
    """
    import asyncio

    from agent.run_pipeline import run_live

    return asyncio.run(run_live(trailer_id))


def get_pipeline_runner() -> Callable[[str], dict]:
    return getattr(app.state, "pipeline_runner", default_pipeline_runner)


@app.get("/trailers")
def list_trailers() -> list[str]:
    if not GROUND_TRUTH_PATH.exists():
        return []
    return list(json.loads(GROUND_TRUTH_PATH.read_text()).keys())


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    runner = get_pipeline_runner()
    final_state = runner(req.trailer_id)
    report_path = final_state.get("report_path")
    if not report_path:
        raise HTTPException(status_code=500, detail="pipeline did not produce a report")
    return AnalyzeResponse(report_id=req.trailer_id)


@app.get("/report/{trailer_id}")
def get_report(trailer_id: str = PathParam(..., pattern=TRAILER_ID_PATTERN)) -> dict:
    report_path = REPORTS_DIR / f"{trailer_id}.json"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail=f"no report found for {trailer_id}")
    return json.loads(report_path.read_text())


@app.get("/report/{trailer_id}/html", response_class=HTMLResponse)
def get_report_html(trailer_id: str = PathParam(..., pattern=TRAILER_ID_PATTERN)) -> str:
    html_path = REPORTS_DIR / f"{trailer_id}.html"
    if not html_path.exists():
        from report.render import render_report_from_json

        report_path = REPORTS_DIR / f"{trailer_id}.json"
        if not report_path.exists():
            raise HTTPException(status_code=404, detail=f"no report found for {trailer_id}")
        _, html_path = render_report_from_json(report_path)
    return html_path.read_text()
