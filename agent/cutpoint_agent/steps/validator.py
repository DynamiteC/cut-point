"""Step 2: validator -- re-derive the analytics and overrule the LLM.

The analyst is an LlmAgent that TRANSCRIBES mcp-clickhouse tool output into
AnalysisResult. `output_schema` validates the shape of that transcription, not
its numeric fidelity, so a transposed digit, a dropped cliff or an invented one
passes validation silently. The product's central claim is that ClickHouse
computes the statistics and Gemini only perceives; without this step that claim
rests on the model choosing to copy numbers accurately.

This step runs the same fixed changepoints.sql directly over a readonly=1
connection and treats ClickHouse as authoritative. Divergence is not an error,
it is recorded as evidence in the report.

Determinism note: like the watcher, this is infrastructure running a fixed query
file, not an LLM writing SQL, so it sits outside the mcp-clickhouse boundary.
The connection is pinned readonly at the server, which is a stronger guarantee
than the convention it replaces.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.events.event_actions import EventActions

from agent.cutpoint_agent.schemas import AnalysisResult, CliffPoint, ValidationReport

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CHANGEPOINTS_SQL = REPO_ROOT / "sql" / "analysis" / "changepoints.sql"

# Below this the LLM and ClickHouse are saying the same thing about a cliff.
DROP_PCT_TOLERANCE = 0.005


class EmptyAnalyticsError(RuntimeError):
    """Raised when the analytics read returned nothing at all.

    Without this, zero rows flow through as zero cliffs and the reporter emits a
    confident "no significant retention cliffs detected" -- a clean bill of
    health for a trailer nobody actually measured.
    """


def _valid_id(value: str) -> bool:
    return 0 < len(value) <= 64 and all(c.isalnum() or c in "_-" for c in value)


def query_cliffs(client, trailer_id: str) -> list[CliffPoint]:
    if not _valid_id(trailer_id):
        raise ValueError(f"invalid trailer_id: {trailer_id!r}")
    sql = CHANGEPOINTS_SQL.read_text().replace("{trailer_id}", trailer_id)
    return [
        CliffPoint(
            second=int(r[0]),
            drop_pct=float(r[1]),
            z_score=float(r[2]),
            affected_cohorts=list(r[3]) if len(r) > 3 and r[3] else [],
        )
        for r in client.query(sql).result_rows
    ]


def source_row_count(client, trailer_id: str) -> int:
    if not _valid_id(trailer_id):
        raise ValueError(f"invalid trailer_id: {trailer_id!r}")
    rows = client.query(
        "SELECT count() FROM cutpoint.mv_second_viewers WHERE trailer_id = %(t)s",
        parameters={"t": trailer_id},
    ).result_rows
    return int(rows[0][0]) if rows else 0


def validate(analysis: AnalysisResult, client) -> tuple[AnalysisResult, ValidationReport]:
    """ClickHouse wins. Returns the corrected analysis plus what diverged."""
    rows = source_row_count(client, analysis.trailer_id)
    if rows == 0:
        raise EmptyAnalyticsError(
            f"no retention data for trailer_id={analysis.trailer_id!r} in "
            "cutpoint.mv_second_viewers. Refusing to emit a report claiming no "
            "cliffs were found -- load the data first (make generate-data load)."
        )

    truth = query_cliffs(client, analysis.trailer_id)
    truth_by_second = {c.second: c for c in truth}
    llm_by_second = {c.second: c for c in analysis.cliffs}

    corrected: list[str] = []
    for second, actual in truth_by_second.items():
        claimed = llm_by_second.get(second)
        if claimed is None:
            corrected.append(f"second {second}: missed by the analyst, restored from ClickHouse")
        elif abs(claimed.drop_pct - actual.drop_pct) > DROP_PCT_TOLERANCE:
            corrected.append(
                f"second {second}: drop_pct {claimed.drop_pct:.4f} -> {actual.drop_pct:.4f}"
            )
    for second in llm_by_second:
        if second not in truth_by_second:
            corrected.append(f"second {second}: reported by the analyst, absent from ClickHouse")

    verified = analysis.model_copy(update={"cliffs": truth})
    report = ValidationReport(
        verified=not corrected,
        source_rows=rows,
        llm_cliff_count=len(analysis.cliffs),
        verified_cliff_count=len(truth),
        corrected=corrected,
    )
    return verified, report


class ValidatorAgent(BaseAgent):
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        analysis = AnalysisResult.model_validate(ctx.session.state["analysis_result"])

        from ingest.clickhouse_client import get_readonly_client

        client = get_readonly_client()
        try:
            verified, report = validate(analysis, client)
        finally:
            client.close()

        yield Event(
            author=self.name,
            actions=EventActions(
                state_delta={
                    "analysis_result": verified.model_dump(mode="json"),
                    "validation_report": report.model_dump(mode="json"),
                }
            ),
        )
