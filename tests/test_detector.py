"""Phase 2 gate: run changepoints.sql against real ClickHouse data and assert every
injected ground-truth cliff is recovered within +/-2 seconds, with at most 2 false
positives per trailer. Also asserts milestone_funnel.sql uses native windowFunnel().

Direct clickhouse-connect use here is acceptable per TASK.md section 9 Phase 2: tests
are not the agent's query path.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = REPO_ROOT / "sql" / "analysis"

MAX_FALSE_POSITIVES = 2
MATCH_TOLERANCE_S = 2


def run_changepoints(clickhouse_client, trailer_id: str) -> list[dict]:
    # The templates bind trailer_id server-side via {trailer_id:String}, exactly
    # as the pipeline runs them, so the test exercises the real query path.
    sql = (SQL_DIR / "changepoints.sql").read_text()
    result = clickhouse_client.query(sql, parameters={"trailer_id": trailer_id})
    return [dict(zip(result.column_names, row)) for row in result.result_rows]


def test_changepoints_recovers_every_injected_cliff(clickhouse_client, ground_truth):
    for trailer_id, spec in ground_truth.items():
        detected = run_changepoints(clickhouse_client, trailer_id)
        detected_seconds = [row["second"] for row in detected]

        unmatched = []
        for cliff in spec["cliffs"]:
            match = any(abs(d - cliff["second"]) <= MATCH_TOLERANCE_S for d in detected_seconds)
            if not match:
                unmatched.append(cliff)

        assert not unmatched, (
            f"{trailer_id}: failed to recover injected cliffs {unmatched} "
            f"(detected seconds: {detected_seconds})"
        )

        false_positives = [
            d
            for d in detected_seconds
            if not any(abs(d - c["second"]) <= MATCH_TOLERANCE_S for c in spec["cliffs"])
        ]
        assert len(false_positives) <= MAX_FALSE_POSITIVES, (
            f"{trailer_id}: too many false positives {false_positives} "
            f"(max {MAX_FALSE_POSITIVES})"
        )


def test_changepoints_false_positive_rate_on_control_trailer(clickhouse_client, ground_truth):
    """demo_control has zero injected cliffs (see ingest/generate.py) -- unlike the
    other trailers, the detector's thresholds were never tuned against this data,
    so this is a genuine (non-circular) false-positive check rather than the
    detector grading its own homework.
    """
    if "demo_control" not in ground_truth:
        pytest.skip("data/ground_truth.json predates demo_control -- run make generate-data load")
    detected = run_changepoints(clickhouse_client, "demo_control")
    assert len(detected) <= MAX_FALSE_POSITIVES, (
        f"demo_control (no injected cliffs) triggered {len(detected)} false positives: {detected}"
    )


def test_changepoints_affected_cohorts_match_ground_truth(clickhouse_client, ground_truth):
    for trailer_id, spec in ground_truth.items():
        detected = run_changepoints(clickhouse_client, trailer_id)
        for cliff in spec["cliffs"]:
            match = next(
                (d for d in detected if abs(d["second"] - cliff["second"]) <= MATCH_TOLERANCE_S),
                None,
            )
            if match is None:
                continue  # already asserted above
            detected_cohorts = set(match["affected_cohorts"])
            expected_cohorts = set(cliff["cohorts"])
            assert detected_cohorts == expected_cohorts, (
                f"{trailer_id} second {cliff['second']}: expected cohorts {expected_cohorts}, "
                f"got {detected_cohorts}"
            )


def test_milestone_funnel_uses_native_window_funnel():
    sql_text = (SQL_DIR / "milestone_funnel.sql").read_text()
    assert "windowFunnel(" in sql_text, "milestone_funnel.sql must use ClickHouse's windowFunnel()"


def test_retention_curve_runs_and_normalizes_to_baseline(clickhouse_client, ground_truth):
    trailer_id = next(iter(ground_truth))
    sql = (SQL_DIR / "retention_curve.sql").read_text()
    result = clickhouse_client.query(sql, parameters={"trailer_id": trailer_id})
    rows = [dict(zip(result.column_names, row)) for row in result.result_rows]
    assert rows, "retention_curve.sql returned no rows"
    second_zero_rows = [r for r in rows if r["second_offset"] == 0]
    assert all(abs(r["retention_fraction"] - 1.0) < 0.15 for r in second_zero_rows)


def test_cohort_divergence_runs(clickhouse_client, ground_truth):
    trailer_id = next(iter(ground_truth))
    sql = (SQL_DIR / "cohort_divergence.sql").read_text()
    result = clickhouse_client.query(sql, parameters={"trailer_id": trailer_id})
    assert result.result_rows, "cohort_divergence.sql returned no rows"


def test_milestone_funnel_returns_monotonic_milestones(clickhouse_client, ground_truth):
    trailer_id = next(iter(ground_truth))
    sql = (SQL_DIR / "milestone_funnel.sql").read_text()
    result = clickhouse_client.query(sql, parameters={"trailer_id": trailer_id})
    rows = dict(result.result_rows)
    assert rows["reached_25pct"] >= rows["reached_50pct"] >= rows["reached_75pct"] >= rows["completed"]
