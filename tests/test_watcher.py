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

    def query(self, sql, parameters=None):
        # Record the bound value too, so a test can assert a malformed id never
        # reaches the database by any route (query text or bound parameter).
        self.queries.append(sql)
        self.queries.append((parameters or {}).get("trailer_id", ""))
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
    # trailer_id is bound server-side, but the charset guard still rejects a
    # malformed id early rather than sending it to the database at all.
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


def test_an_unreachable_database_does_not_ask_pubsub_to_retry_forever(monkeypatch) -> None:
    """Pub/Sub redelivers on any 5xx. An unreachable database is not something a
    redelivery can fix, so an escaping exception turned one scheduled tick into
    an unbounded retry loop that wakes the service and bills for it.
    """
    from fastapi.testclient import TestClient

    import services.watcher.main as watcher

    monkeypatch.setenv("CUTPOINT_REQUIRE_AUTH", "false")

    def refuse(*args, **kwargs):
        raise ConnectionRefusedError("[Errno 111] Connection refused")

    monkeypatch.setattr("ingest.clickhouse_client.get_ingest_client", refuse)

    response = TestClient(watcher.app).post("/pubsub/scan", json={"message": {"data": "e30="}})

    assert response.status_code == 200, "a 5xx here would be retried forever"
    body = response.json()
    assert body["status"] == "degraded"
    assert "ConnectionRefusedError" in body["error"]


def test_the_watcher_uses_a_readonly_connection(monkeypatch) -> None:
    """Four documents claimed the watcher pins readonly=1 at the session level.
    It used the read-write client. A public document asserting a security
    property the code does not have is worse than not claiming it, so this pins
    the behaviour rather than the prose.
    """
    import services.watcher.main as watcher

    monkeypatch.setenv("CUTPOINT_REQUIRE_AUTH", "false")
    used = {}

    class FakeClient:
        def query(self, sql, parameters=None):
            return FakeResult([])

        def close(self):
            pass

    def fake_readonly(*args, **kwargs):
        used["readonly"] = True
        return FakeClient()

    def fake_readwrite(*args, **kwargs):
        used["readwrite"] = True
        return FakeClient()

    monkeypatch.setattr("ingest.clickhouse_client.get_readonly_client", fake_readonly)
    monkeypatch.setattr("ingest.clickhouse_client.get_ingest_client", fake_readwrite)

    from fastapi.testclient import TestClient

    TestClient(watcher.app).post("/pubsub/scan", json={"message": {"data": "e30="}})

    assert used.get("readonly"), "the watcher must use the read-only client"
    assert not used.get("readwrite"), "the watcher must never take a write connection"
