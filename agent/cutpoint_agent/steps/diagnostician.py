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

from agent.cutpoint_agent.prompts import diagnostician_prompt
from agent.cutpoint_agent.schemas import AnalysisResult, Diagnosis, ExtractionResult
from ingest.errors import MissingCredentialError

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


def build_genai_client() -> genai.Client:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    if not project:
        raise MissingCredentialError("GOOGLE_CLOUD_PROJECT")
    return genai.Client(vertexai=True, project=project, location=location)


def diagnose_clip(client: genai.Client, model: str, clip_path: str, second: int,
                   drop_pct: float, cohorts: list[str]) -> Diagnosis:
    """Pure, testable diagnosis logic for a single clip. `client` is injectable so
    tests can pass a mock and never touch Vertex AI.
    """
    video_bytes = Path(clip_path).read_bytes()
    video_part = types.Part.from_bytes(data=video_bytes, mime_type="video/mp4")
    prompt = diagnostician_prompt(second, drop_pct, cohorts)

    response = client.models.generate_content(
        model=model,
        contents=[video_part, prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=DIAGNOSIS_RESPONSE_SCHEMA,
        ),
    )
    parsed = json.loads(response.text)
    return Diagnosis(
        second=second,
        on_screen=parsed["on_screen"],
        hypothesis=parsed["hypothesis"],
        severity=parsed["severity"],
        confidence=parsed["confidence"],
    )


def run_diagnostics(
    analysis: AnalysisResult, extraction: ExtractionResult, client: genai.Client, model: str
) -> list[Diagnosis]:
    cliff_by_second = {c.second: c for c in analysis.cliffs}
    diagnoses = []
    for clip in extraction.clips:
        cliff = cliff_by_second[clip.second]
        diagnoses.append(
            diagnose_clip(
                client, model, clip.clip_path, clip.second, cliff.drop_pct, cliff.affected_cohorts
            )
        )
    return diagnoses


class DiagnosticianAgent(BaseAgent):
    """Reads analysis_result and extraction_result from session state, calls
    Gemini via Vertex AI once per clip, writes diagnoses.
    """

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        analysis = AnalysisResult.model_validate(ctx.session.state["analysis_result"])
        extraction = ExtractionResult.model_validate(ctx.session.state["extraction_result"])

        client = build_genai_client()
        model = os.environ.get("GEMINI_MODEL", "gemini-3-flash")
        diagnoses = run_diagnostics(analysis, extraction, client, model)

        yield Event(
            author=self.name,
            actions=EventActions(
                state_delta={"diagnoses": [d.model_dump(mode="json") for d in diagnoses]}
            ),
        )
