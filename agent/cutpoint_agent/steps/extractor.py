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


def _auth_headers(extractor_url: str) -> dict[str, str]:
    """Mint a Google identity token when the extractor is a private Cloud Run
    service. It is deployed --no-allow-unauthenticated because it shells out to
    ffmpeg, so an unauthenticated caller gets 403. Local runs need no header.
    """
    if not extractor_url.startswith("https://") or ".run.app" not in extractor_url:
        return {}
    from google.auth import exceptions as google_exceptions
    from google.auth.transport.requests import Request
    from google.oauth2 import id_token

    try:
        token = id_token.fetch_id_token(Request(), extractor_url)
    except google_exceptions.GoogleAuthError:
        # fetch_id_token needs the metadata server (present on Cloud Run) or a
        # service account key. A developer running `make demo` against the
        # deployed extractor has neither, only user ADC, which cannot mint an
        # ID token for an arbitrary audience. Fall back to the gcloud CLI.
        import subprocess

        token = subprocess.run(
            ["gcloud", "auth", "print-identity-token"],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout.strip()
    return {"Authorization": f"Bearer {token}"}


def run_extraction(
    analysis: AnalysisResult, video_path: str, extractor_url: str, http_client=None
) -> ExtractionResult:
    """Pure, testable extraction logic: one HTTP POST per cliff, no LLM involved."""
    client = http_client or httpx.Client(timeout=30)
    headers = _auth_headers(extractor_url) if http_client is None else {}
    clips: list[ClipRef] = []
    for cliff in analysis.cliffs:
        start_s = max(0, cliff.second - CLIP_WINDOW_S)
        end_s = cliff.second + CLIP_WINDOW_S
        response = client.post(
            f"{extractor_url}/extract",
            json={"video_path": video_path, "start_s": start_s, "end_s": end_s},
            headers=headers,
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

        extractor_url = os.environ.get("SEGMENT_EXTRACTOR_URL", "http://127.0.0.1:8081")
        result = run_extraction(analysis, video_path, extractor_url)

        yield Event(
            author=self.name,
            actions=EventActions(state_delta={"extraction_result": result.model_dump(mode="json")}),
        )
