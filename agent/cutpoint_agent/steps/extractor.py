"""Step 2: extractor -- deterministic tool step, no LLM decisions. For each cliff,
calls the segment extractor service to pull a +/-5s clip. Wrapped as an ADK
BaseAgent so the pipeline order stays code-enforced (TASK.md section 7 step 2).
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import httpx
from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.events.event_actions import EventActions

from agent.cutpoint_agent.schemas import AnalysisResult, ClipRef, ExtractionResult

CLIP_WINDOW_S = 5


def run_extraction(
    analysis: AnalysisResult, video_path: str, extractor_url: str, http_client=None
) -> ExtractionResult:
    """Pure, testable extraction logic: one HTTP POST per cliff, no LLM involved."""
    client = http_client or httpx.Client(timeout=30)
    clips: list[ClipRef] = []
    for cliff in analysis.cliffs:
        start_s = max(0, cliff.second - CLIP_WINDOW_S)
        end_s = cliff.second + CLIP_WINDOW_S
        response = client.post(
            f"{extractor_url}/extract",
            json={"video_path": video_path, "start_s": start_s, "end_s": end_s},
        )
        response.raise_for_status()
        body = response.json()
        clips.append(
            ClipRef(second=cliff.second, clip_path=body["clip_path"], start_s=start_s, end_s=end_s)
        )
    return ExtractionResult(trailer_id=analysis.trailer_id, clips=clips)


class ExtractorAgent(BaseAgent):
    """Deterministic ADK agent step: reads analysis_result from session state,
    extracts clips via the segment extractor service, writes extraction_result.
    """

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        analysis_dict = ctx.session.state.get("analysis_result")
        if not analysis_dict:
            raise RuntimeError("extractor step requires analysis_result in session state")
        analysis = AnalysisResult.model_validate(analysis_dict)

        video_path = ctx.session.state.get("video_path")
        if not video_path:
            raise RuntimeError("extractor step requires video_path in session state")

        extractor_url = os.environ.get("SEGMENT_EXTRACTOR_URL", "http://localhost:8081")
        result = run_extraction(analysis, video_path, extractor_url)

        yield Event(
            author=self.name,
            actions=EventActions(state_delta={"extraction_result": result.model_dump(mode="json")}),
        )
