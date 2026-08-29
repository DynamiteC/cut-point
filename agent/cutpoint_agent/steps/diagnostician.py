"""Step 3: diagnostician -- multimodal Gemini call per clip via Vertex AI. LLM is
used only for perception (what happens on screen, causal hypothesis) -- the loop
over clips and the pipeline position are code-enforced, not LLM-decided.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncGenerator
from pathlib import Path

from google import genai
from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.events.event_actions import EventActions
from google.genai import types

from agent.cutpoint_agent.config import gemini_model
from agent.cutpoint_agent.prompts import diagnostician_prompt
from agent.cutpoint_agent.schemas import AnalysisResult, Diagnosis, ExtractionResult
from ingest.errors import MissingCredentialError


def _clip_part(clip_path: str) -> types.Part:
    """Reference the clip by URI in the cloud, by bytes locally.

    The extractor runs as its own Cloud Run service with its own ephemeral disk,
    so a local path it returns does not exist in this container -- read_bytes on
    it discarded every extraction and failed every diagnosis. When the extractor
    is backed by a bucket it returns a gs:// URI, which Vertex reads directly.
    That also keeps the whole clip out of this process's memory.
    """
    if clip_path.startswith("gs://"):
        return types.Part.from_uri(file_uri=clip_path, mime_type="video/mp4")
    return types.Part.from_bytes(data=Path(clip_path).read_bytes(), mime_type="video/mp4")


DIAGNOSIS_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "on_screen": {"type": "STRING"},
        "hypothesis": {"type": "STRING"},
        "severity": {"type": "INTEGER"},
        "confidence": {"type": "NUMBER"},
    },
    "required": ["on_screen", "hypothesis", "severity", "confidence"],
}


# A video inference that never returns holds one of only two pipeline slots
# until Cloud Run's request timeout kills the whole run. Bound it explicitly.
GEMINI_TIMEOUT_MS = int(os.environ.get("CUTPOINT_GEMINI_TIMEOUT_MS", "120000"))


def build_genai_client() -> genai.Client:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
    if not project:
        raise MissingCredentialError("GOOGLE_CLOUD_PROJECT")
    return genai.Client(
        vertexai=True,
        project=project,
        location=location,
        http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS),
    )


def diagnose_clip(client: genai.Client, model: str, clip_path: str, second: int,
                   drop_pct: float, cohorts: list[str], start_s: int = 0,
                   end_s: int | None = None) -> Diagnosis:
    """Pure, testable diagnosis logic for a single clip. `client` is injectable so
    tests can pass a mock and never touch Vertex AI.
    """
    video_part = _clip_part(clip_path)
    prompt = diagnostician_prompt(second, drop_pct, cohorts, start_s, end_s)

    response = client.models.generate_content(
        model=model,
        contents=[video_part, prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=DIAGNOSIS_RESPONSE_SCHEMA,
        ),
    )
    parsed = json.loads(response.text)
    raw_severity = parsed.get("severity", 3)
    severity = max(1, min(5, int(raw_severity))) if isinstance(raw_severity, (int, float)) else 3
    raw_confidence = parsed.get("confidence", 0.8)
    confidence = max(0.0, min(1.0, float(raw_confidence))) if isinstance(raw_confidence, (int, float)) else 0.8
    return Diagnosis(
        second=second,
        on_screen=parsed.get("on_screen", ""),
        hypothesis=parsed.get("hypothesis", ""),
        severity=severity,
        confidence=confidence,
    )


def run_diagnostics(
    analysis: AnalysisResult, extraction: ExtractionResult, client: genai.Client, model: str
) -> tuple[list[Diagnosis], list[dict]]:
    """Diagnose every clip. One clip failing must not lose the other findings.

    The chaos suite claims per-clip blast-radius containment, but a raising
    diagnose_clip aborted the whole run and discarded the diagnoses already
    obtained, along with all the extraction spend behind them. Failures are now
    collected and reported instead of thrown away.

    Returns (diagnoses, failures) so the reporter can state coverage honestly
    rather than implying every cliff was examined.
    """
    cliff_by_second = {c.second: c for c in analysis.cliffs}
    diagnoses: list[Diagnosis] = []
    failures: list[dict] = []
    for clip in extraction.clips:
        cliff = cliff_by_second.get(clip.second)
        if cliff is None:
            failures.append({"second": clip.second, "error": "no matching cliff in analysis"})
            continue
        try:
            diagnoses.append(
                diagnose_clip(
                    client,
                    model,
                    clip.clip_path,
                    clip.second,
                    cliff.drop_pct,
                    cliff.affected_cohorts,
                    clip.start_s,
                    clip.end_s,
                )
            )
        except Exception as exc:  # noqa: BLE001 -- deliberate per-clip isolation
            # Blind by design: any failure diagnosing ONE clip (timeout, 5xx,
            # corrupt media, safety block) must be contained to that clip.
            failures.append(
                {"second": clip.second, "error": f"{type(exc).__name__}: {exc}"[:300]}
            )

    if extraction.clips and not diagnoses:
        # Every clip failed. That is not a report, it is an outage.
        raise RuntimeError(
            f"all {len(extraction.clips)} clip diagnoses failed: {failures[:3]}"
        )
    return diagnoses, failures


class DiagnosticianAgent(BaseAgent):
    """Reads analysis_result and extraction_result from session state, calls
    Gemini via Vertex AI once per clip, writes diagnoses.
    """

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        analysis = AnalysisResult.model_validate(ctx.session.state["analysis_result"])
        extraction = ExtractionResult.model_validate(ctx.session.state["extraction_result"])

        client = build_genai_client()
        model = gemini_model()
        diagnoses, failures = run_diagnostics(analysis, extraction, client, model)

        yield Event(
            author=self.name,
            actions=EventActions(
                state_delta={
                    "diagnoses": [d.model_dump(mode="json") for d in diagnoses],
                    "diagnosis_failures": failures,
                }
            ),
        )
