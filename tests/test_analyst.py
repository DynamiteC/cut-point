"""The analyst reads every number straight from ClickHouse.

It used to be an LlmAgent that transcribed query results into a schema. On a real
run it reported a cliff at second 2 that does not exist in the database and missed
all three that do (see tests/test_validator.py for that comparison, kept as the
evidence for why this step is no longer a model). These tests pin the property
that replaced it: no model sits between the database and the report.
"""

from __future__ import annotations

import pytest

from agent.cutpoint_agent.steps.analyst import analyze
from agent.cutpoint_agent.steps.validator import EmptyAnalyticsError
from tests.test_validator import FakeClient


def test_every_number_comes_from_the_database() -> None:
    # Arrange
    client = FakeClient(
        rows=900,
        cliff_rows=[(47, 0.22, -17.4, ["18-24"]), (63, 0.15, -9.1, ["25-34"])],
        funnel_rows=[("reached_25pct", 0.70), ("completed", 0.35)],
        curve_rows=[("18-24", 90, 100, 300, 0.33), ("25-34", 90, 100, 300, 0.37)],
    )

    # Act
    analysis, provenance = analyze(client, "demo_001")

    # Assert
    assert [c.second for c in analysis.cliffs] == [47, 63]
    assert analysis.milestone_funnel == {"reached_25pct": 0.70, "completed": 0.35}
    assert analysis.overall_retention_end == pytest.approx(0.35)
    assert provenance.source_rows == 900
    assert provenance.llm_cliff_count == 0, "no model contributed a number"
    assert provenance.corrected == []


def test_an_empty_read_refuses_rather_than_reporting_no_cliffs() -> None:
    client = FakeClient(rows=0, cliff_rows=[])
    with pytest.raises(EmptyAnalyticsError):
        analyze(client, "demo_001")


def test_an_id_that_would_reach_query_text_is_rejected() -> None:
    from tests.test_validator import BAD_ID

    client = FakeClient(rows=900, cliff_rows=[])
    with pytest.raises(ValueError):
        analyze(client, BAD_ID)
