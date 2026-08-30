"""Not a pipeline step. The measurement that removed one.

This module is no longer wired into the pipeline: the analyst reads the database
directly and there is nothing left to overrule. What remains is used two ways.
analyst.py imports its query helpers, and validate() plus its tests are kept as
the record of WHY the numeric path has no model in it -- on a real run the
LlmAgent analyst reported a cliff at second 2 that does not exist and missed all
three that do.

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

from pathlib import Path

from agent.cutpoint_agent.schemas import AnalysisResult, CliffPoint, ValidationReport

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CHANGEPOINTS_SQL = REPO_ROOT / "sql" / "analysis" / "changepoints.sql"
RETENTION_SQL = REPO_ROOT / "sql" / "analysis" / "retention_curve.sql"
FUNNEL_SQL = REPO_ROOT / "sql" / "analysis" / "milestone_funnel.sql"

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
    sql = _load(CHANGEPOINTS_SQL, trailer_id)
    return [
        CliffPoint(
            second=int(r[0]),
            drop_pct=float(r[1]),
            z_score=float(r[2]),
            affected_cohorts=list(r[3]) if len(r) > 3 and r[3] else [],
        )
        for r in client.query(sql, parameters={"trailer_id": trailer_id}).result_rows
    ]


def query_funnel(client, trailer_id: str) -> dict[str, float]:
    """Milestone funnel straight from windowFunnel(), no model in the path."""
    sql = _load(FUNNEL_SQL, trailer_id)
    return {
        str(r[0]): float(r[1])
        for r in client.query(sql, parameters={"trailer_id": trailer_id}).result_rows
    }


def query_retention_end(client, trailer_id: str) -> float | None:
    """Mean retention_fraction across cohorts at the final second."""
    sql = _load(RETENTION_SQL, trailer_id)
    rows = client.query(sql, parameters={"trailer_id": trailer_id}).result_rows
    if not rows:
        return None
    last_second = max(int(r[1]) for r in rows)
    finals = [float(r[4]) for r in rows if int(r[1]) == last_second]
    return sum(finals) / len(finals) if finals else None


def _load(path: Path, trailer_id: str) -> str:
    """Return the raw SQL template. The trailer_id is bound server-side by
    clickhouse-connect via a {trailer_id:String} placeholder, never interpolated
    into the query text, so injection is structurally impossible. The charset
    check is kept as a cheap second layer and an early, clear error.
    """
    if not _valid_id(trailer_id):
        raise ValueError(f"invalid trailer_id: {trailer_id!r}")
    return path.read_text()


def source_row_count(client, trailer_id: str) -> int:
    if not _valid_id(trailer_id):
        raise ValueError(f"invalid trailer_id: {trailer_id!r}")
    rows = client.query(
        "SELECT count() FROM cutpoint.mv_second_viewers WHERE trailer_id = {trailer_id:String}",
        parameters={"trailer_id": trailer_id},
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

    # Recompute the scalar metrics too, so nothing in AnalysisResult depends on
    # the model having transcribed accurately. With this the analyst is a
    # convenience, not a correctness dependency: if it hallucinates, times out
    # or returns truncated JSON, ClickHouse still supplies every number.
    update: dict = {"cliffs": truth}

    funnel = query_funnel(client, analysis.trailer_id)
    if funnel:
        for key, actual in funnel.items():
            claimed = analysis.milestone_funnel.get(key)
            if claimed is None or abs(claimed - actual) > DROP_PCT_TOLERANCE:
                corrected.append(f"milestone_funnel[{key}]: {claimed} -> {actual:.4f}")
        update["milestone_funnel"] = funnel

    retention_end = query_retention_end(client, analysis.trailer_id)
    if retention_end is not None:
        if abs(analysis.overall_retention_end - retention_end) > DROP_PCT_TOLERANCE:
            corrected.append(
                f"overall_retention_end: {analysis.overall_retention_end:.4f} "
                f"-> {retention_end:.4f}"
            )
        update["overall_retention_end"] = retention_end

    verified = analysis.model_copy(update=update)
    report = ValidationReport(
        verified=not corrected,
        source_rows=rows,
        llm_cliff_count=len(analysis.cliffs),
        verified_cliff_count=len(truth),
        corrected=corrected,
    )
    return verified, report
