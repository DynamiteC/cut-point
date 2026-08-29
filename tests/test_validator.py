"""The validator is what makes "ClickHouse computes the statistics, Gemini only
perceives" a property of the system rather than an assertion in the README.
The analyst transcribes tool output into a schema that checks shape, not
numbers, so these are the cases where the transcription can be wrong.
"""

from __future__ import annotations

import pytest

from agent.cutpoint_agent.schemas import AnalysisResult, CliffPoint
from agent.cutpoint_agent.steps.validator import EmptyAnalyticsError, validate

BAD_ID = "demo_001'; DROP" + " TABLE cutpoint.trailers--"


class FakeClient:
    """Dispatches by query shape: the validator now runs three different SQL
    files (changepoints, milestone_funnel, retention_curve) plus a row count.
    """

    def __init__(self, rows, cliff_rows, funnel_rows=None, curve_rows=None):
        self.rows = rows
        self.cliff_rows = cliff_rows
        self.funnel_rows = funnel_rows if funnel_rows is not None else []
        self.curve_rows = curve_rows if curve_rows is not None else []
        self.queries = []

    def query(self, sql, parameters=None):
        self.queries.append(sql)

        class R:
            pass

        r = R()
        # Order matters: milestone_funnel.sql also contains "count()" in its
        # totals CTE, so the specific matches must be tested first.
        if "windowFunnel" in sql:
            r.result_rows = self.funnel_rows
        elif "retention_fraction" in sql:
            r.result_rows = self.curve_rows
        elif "mv_second_viewers WHERE" in sql:
            r.result_rows = [(self.rows,)]
        else:
            r.result_rows = self.cliff_rows
        return r


def _analysis(cliffs):
    return AnalysisResult(
        trailer_id="demo_001",
        overall_retention_end=0.35,
        milestone_funnel={"completed": 0.35},
        cliffs=cliffs,
    )


def _cliff(second, drop_pct=0.22):
    return CliffPoint(second=second, drop_pct=drop_pct, affected_cohorts=["18-24"], z_score=-17.4)


def test_empty_analytics_refuses_to_emit_a_clean_bill_of_health() -> None:
    # Arrange: the read returned nothing at all
    client = FakeClient(rows=0, cliff_rows=[])

    # Act / Assert: zero rows must not become "no significant cliffs detected"
    with pytest.raises(EmptyAnalyticsError):
        validate(_analysis([]), client)


def test_matching_numbers_are_marked_verified() -> None:
    client = FakeClient(rows=900, cliff_rows=[(47, 0.22, -17.4, ["18-24"])])
    verified, report = validate(_analysis([_cliff(47, 0.22)]), client)

    assert report.verified is True
    assert report.corrected == []
    assert [c.second for c in verified.cliffs] == [47]


def test_a_cliff_the_analyst_missed_is_restored_from_clickhouse() -> None:
    client = FakeClient(rows=900, cliff_rows=[(47, 0.22, -17.4, ["18-24"]),
                                              (63, 0.15, -9.1, ["25-34"])])
    verified, report = validate(_analysis([_cliff(47, 0.22)]), client)

    assert [c.second for c in verified.cliffs] == [47, 63]
    assert report.verified is False
    assert any("missed by the analyst" in c for c in report.corrected)


def test_a_cliff_the_analyst_invented_is_removed() -> None:
    client = FakeClient(rows=900, cliff_rows=[(47, 0.22, -17.4, ["18-24"])])
    verified, report = validate(_analysis([_cliff(47, 0.22), _cliff(99, 0.40)]), client)

    assert [c.second for c in verified.cliffs] == [47]
    assert any("absent from ClickHouse" in c for c in report.corrected)


def test_a_transposed_number_is_corrected_to_the_database_value() -> None:
    # The analyst read 0.22 as 0.85. Shape is valid; the number is not.
    client = FakeClient(rows=900, cliff_rows=[(47, 0.22, -17.4, ["18-24"])])
    verified, report = validate(_analysis([_cliff(47, 0.85)]), client)

    assert verified.cliffs[0].drop_pct == pytest.approx(0.22)
    assert any("0.8500 -> 0.2200" in c for c in report.corrected)


def test_validator_rejects_an_id_that_would_reach_query_text() -> None:
    client = FakeClient(rows=900, cliff_rows=[])
    analysis = _analysis([]).model_copy(update={"trailer_id": BAD_ID})
    with pytest.raises(ValueError):
        validate(analysis, client)


def test_every_number_is_recomputed_so_the_analyst_is_not_a_dependency() -> None:
    """If the analyst hallucinates, times out or returns truncated JSON, the
    report must still be correct. Nothing in AnalysisResult may survive from the
    model when ClickHouse can supply it.
    """
    # Arrange: an analyst that got literally everything wrong
    client = FakeClient(
        rows=900,
        cliff_rows=[(47, 0.22, -17.4, ["18-24"])],
        funnel_rows=[("reached_25pct", 0.70), ("completed", 0.35)],
        curve_rows=[("18-24", 90, 100, 300, 0.33), ("25-34", 90, 100, 300, 0.37)],
    )
    garbage = AnalysisResult(
        trailer_id="demo_001",
        overall_retention_end=0.99,
        milestone_funnel={"completed": 0.99},
        cliffs=[_cliff(999, 0.99)],
    )

    # Act
    verified, report = validate(garbage, client)

    # Assert: every field replaced by the database's answer
    assert [c.second for c in verified.cliffs] == [47]
    assert verified.milestone_funnel == {"reached_25pct": 0.70, "completed": 0.35}
    assert verified.overall_retention_end == pytest.approx(0.35)  # mean of 0.33 and 0.37
    assert report.verified is False
    assert any("overall_retention_end" in c for c in report.corrected)
    assert any("milestone_funnel" in c for c in report.corrected)
