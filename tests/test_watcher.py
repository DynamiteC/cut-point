"""The watcher is what makes CutPoint autonomous rather than request-driven.
Its one non-obvious behaviour is the fingerprint: without it every scheduled
tick would re-diagnose the same cliffs and re-spend on Gemini forever.
"""

from __future__ import annotations

import pytest

from services.watcher.main import detect_cliffs, fingerprint, scan

INJECTION_PAYLOAD = "demo_001'; DROP" + " TABLE cutpoint.trailers--"


class FakeResult:
    def __init__(self, rows):
        self.result_rows = rows


class FakeClient:
    """Returns the trailer list for the trailers query and a configurable cliff
    set for the changepoints query. Never touches a database.
    """

    def __init__(self, trailers, cliff_rows):
        self.trailers = trailers
        self.cliff_rows = cliff_rows
        self.queries = []

    def query(self, sql):
        self.queries.append(sql)
        if "FROM cutpoint.trailers" in sql:
            return FakeResult([(t,) for t in self.trailers])
        return FakeResult(self.cliff_rows)


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTPOINT_STORE", "local")
    monkeypatch.setattr("agent.cutpoint_agent.store.WATCH_DIR", tmp_path / "watch")
    monkeypatch.setattr("agent.cutpoint_agent.store.JOBS_DIR", tmp_path / "jobs")


def _published():
    sent = []
    return sent, lambda trailer_id, job_id: sent.append((trailer_id, job_id))


def test_first_scan_triggers_analysis() -> None:
    # Arrange
    client = FakeClient(["demo_001"], [(47, 0.22, -17.4)])
    sent, publish = _published()

    # Act
    triggered = scan(client, publish=publish)

    # Assert
    assert [t["trailer_id"] for t in triggered] == ["demo_001"]
    assert len(sent) == 1


def test_rescan_with_unchanged_cliffs_does_not_retrigger() -> None:
    # Arrange
    client = FakeClient(["demo_001"], [(47, 0.22, -17.4)])
    sent, publish = _published()
    scan(client, publish=publish)

    # Act: the scheduler fires again, nothing about the data has changed
    triggered = scan(client, publish=publish)

    # Assert
    assert triggered == []
    assert len(sent) == 1, "an unchanged trailer must not re-spend on Gemini"


def test_a_new_cliff_retriggers() -> None:
    # Arrange
    client = FakeClient(["demo_001"], [(47, 0.22, -17.4)])
    sent, publish = _published()
    scan(client, publish=publish)

    # Act: a new cliff appears at second 63
    client.cliff_rows = [(47, 0.22, -17.4), (63, 0.15, -9.1)]
    triggered = scan(client, publish=publish)

    # Assert
    assert [t["trailer_id"] for t in triggered] == ["demo_001"]
    assert len(sent) == 2


def test_fingerprint_ignores_sub_millipercent_jitter() -> None:
    assert fingerprint([{"second": 47, "drop_pct": 0.220001}]) == fingerprint(
        [{"second": 47, "drop_pct": 0.2200004}]
    )


def test_fingerprint_is_order_independent() -> None:
    a = [{"second": 47, "drop_pct": 0.22}, {"second": 63, "drop_pct": 0.15}]
    assert fingerprint(a) == fingerprint(list(reversed(a)))


def test_detect_cliffs_rejects_an_id_that_would_reach_query_text() -> None:
    # changepoints.sql interpolates trailer_id directly into SQL text.
    client = FakeClient(["x"], [])
    with pytest.raises(ValueError):
        detect_cliffs(client, INJECTION_PAYLOAD)


def test_scan_skips_malformed_trailer_ids_from_the_database() -> None:
    # Arrange: a poisoned row in the trailers table must never reach the query.
    client = FakeClient(["demo_001", INJECTION_PAYLOAD], [(47, 0.22, -17.4)])
    sent, publish = _published()

    # Act
    scan(client, publish=publish)

    # Assert
    assert all(INJECTION_PAYLOAD not in q for q in client.queries)
    assert [t for t, _ in sent] == ["demo_001"]


def test_a_trailer_with_no_cliffs_never_triggers_a_pipeline_run() -> None:
    # Arrange: demo_control is the false-positive control and has zero cliffs.
    client = FakeClient(["demo_control"], [])
    sent, publish = _published()

    # Act
    triggered = scan(client, publish=publish)

    # Assert: nothing to diagnose means nothing to spend on
    assert triggered == []
    assert sent == []
