"""Step 1: analyst -- deterministic retention analytics.

This step used to be an LlmAgent that queried ClickHouse through mcp-clickhouse
and transcribed the results into AnalysisResult. Measurement killed that design:
on a real run it reported a cliff at second 2 that does not exist in the
database and missed all three that do, and separately it padded its structured
output with whitespace and truncated mid-JSON. A validator step existed purely to
overrule it, which meant every field it produced was discarded.

So the numbers are now read directly. No model sits between the database and the
report. The model still has a job in this pipeline -- steps 3 and 4 -- but it is
perception and language, never arithmetic.

The historical comparison is preserved in validator.validate() and its tests,
because "we measured our own model and removed it from the numeric path" is the
reason this pipeline looks the way it does.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.events.event_actions import EventActions

from agent.cutpoint_agent import obs
from agent.cutpoint_agent.schemas import AnalysisResult, ValidationReport
from agent.cutpoint_agent.steps.validator import (
    EmptyAnalyticsError,
    query_cliffs,
    query_funnel,
    query_retention_end,
    source_row_count,
)


def analyze(client, trailer_id: str) -> tuple[AnalysisResult, ValidationReport]:
    """Read every number the report needs, straight from ClickHouse."""
    rows = source_row_count(client, trailer_id)
    if rows == 0:
        raise EmptyAnalyticsError(
            f"no retention data for trailer_id={trailer_id!r} in "
            "cutpoint.mv_second_viewers. Refusing to emit a report claiming no "
            "cliffs were found -- load the data first (make generate-data load)."
        )

    cliffs = query_cliffs(client, trailer_id)
    analysis = AnalysisResult(
        trailer_id=trailer_id,
        overall_retention_end=query_retention_end(client, trailer_id) or 0.0,
        milestone_funnel=query_funnel(client, trailer_id),
        cliffs=cliffs,
    )
    provenance = ValidationReport(
        verified=True,
        source_rows=rows,
        llm_cliff_count=0,
        verified_cliff_count=len(cliffs),
        corrected=[],
    )
    return analysis, provenance


class AnalystAgent(BaseAgent):
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        trailer_id = ctx.session.state["trailer_id"]

        from ingest.clickhouse_client import get_readonly_client

        client = get_readonly_client()
        try:
            analysis, provenance = analyze(client, trailer_id)
        finally:
            client.close()

        obs.info(
            "analytics read",
            trailer_id=trailer_id,
            source_rows=provenance.source_rows,
            cliffs=len(analysis.cliffs),
        )
        yield Event(
            author=self.name,
            actions=EventActions(
                state_delta={
                    "analysis_result": analysis.model_dump(mode="json"),
                    "validation_report": provenance.model_dump(mode="json"),
                }
            ),
        )


def build_analyst_agent() -> AnalystAgent:
    return AnalystAgent(name="analyst")
