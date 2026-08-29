"""Step 1: analyst -- executes the fixed SQL analysis templates via mcp-clickhouse
and returns a structured AnalysisResult. See TASK.md section 7.

The LlmAgent is wrapped so that its failure cannot end the run. Step 2, the
validator, re-derives every number in AnalysisResult directly from ClickHouse,
so the pipeline does not need this step to succeed -- it needs it to not take
the run down with it. Observed failure: the model pads its structured output
with whitespace and truncates mid-JSON, which raised out of SequentialAgent and
killed a run whose numbers were going to be replaced anyway.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from google.adk.agents import BaseAgent, LlmAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.events.event_actions import EventActions
from google.genai import types

from agent.cutpoint_agent import obs
from agent.cutpoint_agent.config import gemini_model
from agent.cutpoint_agent.mcp import clickhouse_toolset
from agent.cutpoint_agent.prompts import ANALYST_INSTRUCTION
from agent.cutpoint_agent.schemas import AnalysisResult


class ResilientAnalystAgent(BaseAgent):
    """Runs the LlmAgent analyst and absorbs its failure.

    On failure it writes an empty AnalysisResult so the pipeline continues; the
    validator then fills in cliffs, milestone_funnel and overall_retention_end
    from the database. The error is recorded in state as analyst_error rather
    than swallowed, so a degraded run is visible instead of silent.
    """

    analyst: LlmAgent

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        trailer_id = ctx.session.state.get("trailer_id", "")
        try:
            async for event in self.analyst.run_async(ctx):
                yield event
        except Exception as exc:  # noqa: BLE001 -- recorded below, never fatal
            detail = f"{type(exc).__name__}: {exc}"[:500]
            obs.warning(
                "analyst degraded, continuing on database values only",
                step="analyst", error=detail, trailer_id=trailer_id,
            )
            yield Event(
                author=self.name,
                actions=EventActions(
                    state_delta={
                        "analyst_error": detail,
                        "analysis_result": AnalysisResult(
                            trailer_id=trailer_id,
                            overall_retention_end=0.0,
                            milestone_funnel={},
                            cliffs=[],
                        ).model_dump(mode="json"),
                    }
                ),
            )
            return

        if not ctx.session.state.get("analysis_result"):
            # Completed without producing output. Same treatment.
            yield Event(
                author=self.name,
                actions=EventActions(
                    state_delta={
                        "analyst_error": "analyst produced no analysis_result",
                        "analysis_result": AnalysisResult(
                            trailer_id=trailer_id,
                            overall_retention_end=0.0,
                            milestone_funnel={},
                            cliffs=[],
                        ).model_dump(mode="json"),
                    }
                ),
            )


def build_analyst_agent() -> ResilientAnalystAgent:
    return ResilientAnalystAgent(name="analyst", analyst=_build_llm_analyst())


def _build_llm_analyst() -> LlmAgent:
    return LlmAgent(
        name="analyst_llm",
        model=gemini_model(),
        instruction=ANALYST_INSTRUCTION,
        # Query 1 returns thousands of per-second rows. Left unbounded the model
        # rendered them as a table inside a JSON string and ran out of output
        # tokens mid-string, killing the step on invalid JSON. Capping alone made
        # it worse: on a thinking model the reasoning tokens draw from the same
        # budget, so an 8192 cap was spent thinking and the answer truncated at
        # column 50. Give the answer real room and keep thinking short -- this
        # step is mechanical transcription, not a reasoning problem.
        generate_content_config=types.GenerateContentConfig(
            max_output_tokens=32768,
            thinking_config=types.ThinkingConfig(thinking_budget=1024),
        ),
        tools=[clickhouse_toolset()],
        output_schema=AnalysisResult,
        output_key="analysis_result",
    )
