"""Phase 5 gate: pipeline order is fixed, analyst output schema validates,
diagnostician prompt contains cliff metadata, reporter writes valid DirectorsNotes.
Gemini client and the extractor HTTP call are mocked -- no cloud calls.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from agent.cutpoint_agent.agent import build_root_agent
from agent.cutpoint_agent.prompts import diagnostician_prompt
from agent.cutpoint_agent.schemas import AnalysisResult, CliffPoint, ClipRef, ExtractionResult
from agent.cutpoint_agent.steps.diagnostician import run_diagnostics
from agent.cutpoint_agent.steps.extractor import run_extraction
from agent.cutpoint_agent.steps.reporter import build_directors_notes, write_report_json


def test_pipeline_order_is_fixed():
    root = build_root_agent()
    names = [a.name for a in root.sub_agents]
    assert names == ["analyst", "extractor", "diagnostician", "narrator", "reporter"]
    # The narrator writes prose from findings, so it must run after the
    # diagnoses exist and before the report is assembled.
    assert names.index("narrator") > names.index("diagnostician")
    assert names.index("narrator") < names.index("reporter")


def test_no_model_can_put_a_number_in_the_report():
    root = build_root_agent()
    # The analyst is deterministic: it reads ClickHouse directly and holds no
    # model at all. That is the point -- no model can put a number in the report.
    analyst = root.sub_agents[0]
    assert not hasattr(analyst, "model"), "the analyst must not carry a model"
    assert getattr(analyst, "output_schema", None) is None

    # The only LlmAgent in the pipeline writes prose, never structured numbers.
    narrator = root.sub_agents[3].narrator
    assert narrator.output_key == "executive_summary"
    assert narrator.output_schema is None, (
        "forcing structured output through this model is what truncated mid-JSON before"
    )

    sample = {
        "trailer_id": "demo_001",
        "overall_retention_end": 0.35,
        "milestone_funnel": {"reached_25pct": 0.7, "completed": 0.35},
        "cliffs": [{"second": 47, "drop_pct": 0.22, "affected_cohorts": ["18-24"], "z_score": -10.0}],
    }
    validated = AnalysisResult.model_validate(sample)
    assert validated.cliffs[0].second == 47


def test_diagnostician_prompt_contains_cliff_metadata():
    prompt = diagnostician_prompt(47, 0.22, ["18-24", "25-34"])
    assert "47" in prompt
    assert "22.0%" in prompt
    assert "18-24" in prompt
    assert "25-34" in prompt


def test_extractor_calls_http_service_per_cliff_no_llm():
    analysis = AnalysisResult(
        trailer_id="demo_001",
        overall_retention_end=0.3,
        milestone_funnel={},
        cliffs=[
            CliffPoint(second=22, drop_pct=0.18, affected_cohorts=["18-24"], z_score=-10.0),
            CliffPoint(second=47, drop_pct=0.22, affected_cohorts=["18-24", "25-34"], z_score=-15.0),
        ],
    )

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.json.return_value = {"clip_path": "data/clips/fake.mp4", "duration_s": 10.0}
    mock_client.post.return_value = mock_response

    result = run_extraction(analysis, "data/videos/demo_001.mp4", "http://localhost:8081", mock_client)

    assert len(result.clips) == 2
    assert mock_client.post.call_count == 2
    first_call_args = mock_client.post.call_args_list[0].kwargs["json"]
    assert first_call_args["start_s"] == 17  # 22 - 5
    assert first_call_args["end_s"] == 27  # 22 + 5


def test_diagnostician_uses_mocked_gemini_client_never_real_vertex():
    analysis = AnalysisResult(
        trailer_id="demo_001",
        overall_retention_end=0.3,
        milestone_funnel={},
        cliffs=[CliffPoint(second=47, drop_pct=0.22, affected_cohorts=["18-24"], z_score=-15.0)],
    )
    extraction = ExtractionResult(
        trailer_id="demo_001",
        clips=[ClipRef(second=47, clip_path=__file__, start_s=42, end_s=52)],
    )

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = json.dumps(
        {
            "on_screen": "a jump-scare reveal",
            "hypothesis": "the reveal spoils the villain twist",
            "severity": 4,
            "confidence": 0.8,
        }
    )
    mock_client.models.generate_content.return_value = mock_response

    diagnoses, failures = run_diagnostics(analysis, extraction, mock_client, "gemini-3.5-flash")
    assert failures == []

    assert len(diagnoses) == 1
    assert diagnoses[0].severity == 4
    assert "spoils" in diagnoses[0].hypothesis
    mock_client.models.generate_content.assert_called_once()


def test_reporter_writes_valid_directors_notes(tmp_path):
    from agent.cutpoint_agent.schemas import Diagnosis

    analysis = AnalysisResult(
        trailer_id="demo_001",
        overall_retention_end=0.3,
        milestone_funnel={"completed": 0.3},
        cliffs=[CliffPoint(second=47, drop_pct=0.22, affected_cohorts=["18-24"], z_score=-15.0)],
    )
    extraction = ExtractionResult(
        trailer_id="demo_001", clips=[ClipRef(second=47, clip_path="clip.mp4", start_s=42, end_s=52)]
    )
    diagnoses = [
        Diagnosis(second=47, on_screen="reveal", hypothesis="spoiler", severity=4, confidence=0.8)
    ]

    notes = build_directors_notes("demo_001", "Demo One", 90, analysis, extraction, diagnoses)
    assert notes.trailer_id == "demo_001"
    assert len(notes.cliffs) == 1
    assert notes.cliffs[0].recommendations[0].action == "replace_shot"

    out_path = write_report_json(notes, out_dir=tmp_path)
    assert out_path.exists()
    reloaded = json.loads(out_path.read_text())
    assert reloaded["trailer_id"] == "demo_001"


def test_one_failing_clip_does_not_discard_the_others(tmp_path):
    """The chaos suite claims per-clip blast-radius containment. It has to be true:
    a raising diagnose_clip used to abort the run and throw away both the
    diagnoses already obtained and all the extraction spend behind them.
    """
    from agent.cutpoint_agent.schemas import AnalysisResult, ClipRef, ExtractionResult
    from agent.cutpoint_agent.steps.diagnostician import run_diagnostics

    analysis = AnalysisResult(
        trailer_id="demo_001",
        overall_retention_end=0.35,
        milestone_funnel={"completed": 0.35},
        cliffs=[
            {"second": 10, "drop_pct": 0.2, "affected_cohorts": ["18-24"], "z_score": -5.0},
            {"second": 20, "drop_pct": 0.3, "affected_cohorts": ["18-24"], "z_score": -6.0},
        ],
    )
    clip_a = tmp_path / "a.mp4"
    clip_b = tmp_path / "b.mp4"
    clip_a.write_bytes(b"\x00fake-mp4")
    clip_b.write_bytes(b"\x00fake-mp4")
    extraction = ExtractionResult(
        trailer_id="demo_001",
        clips=[
            ClipRef(second=10, clip_path=str(clip_a), start_s=5, end_s=15),
            ClipRef(second=20, clip_path=str(clip_b), start_s=15, end_s=25),
        ],
    )

    calls = []

    class Models:
        def generate_content(self, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise TimeoutError("Gemini timed out on this clip")

            class R:
                text = '{"on_screen":"x","hypothesis":"y","severity":3,"confidence":0.8}'

            return R()

    class FlakyClient:
        models = Models()

    diagnoses, failures = run_diagnostics(analysis, extraction, FlakyClient(), "gemini-3.5-flash")

    assert len(diagnoses) == 1, "the surviving clip's diagnosis must be kept"
    assert len(failures) == 1
    assert failures[0]["second"] == 10
    assert "TimeoutError" in failures[0]["error"]


async def test_a_failing_narrator_does_not_end_the_run(monkeypatch):
    """The report must exist even if the model will not cooperate. The reporter
    falls back to the deterministic summary, so a narrator failure costs prose,
    not the run.
    """
    from agent.cutpoint_agent.steps.narrator import NarratorAgent

    class ExplodingNarrator:
        name = "narrator_llm"

        async def run_async(self, ctx):
            raise ValueError("Invalid JSON: EOF while parsing a value")
            yield  # pragma: no cover -- makes this an async generator

    class FakeSession:
        def __init__(self):
            self.state = {"trailer_id": "demo_001"}

    class FakeCtx:
        def __init__(self):
            self.session = FakeSession()

    agent = NarratorAgent.model_construct(name="narrator", narrator=ExplodingNarrator())

    events = [e async for e in agent._run_async_impl(FakeCtx())]

    assert len(events) == 1
    delta = events[0].actions.state_delta
    assert "EOF while parsing" in delta["narrator_error"], "the failure must stay visible"
    assert "executive_summary" not in delta, "the reporter's deterministic summary stands"


def test_narrator_summary_that_cites_a_nonexistent_cliff_is_rejected():
    """The first version of this step named the state keys instead of
    interpolating them, so the model received no data and confidently invented a
    CGI explosion and a viewer count that were nowhere in the diagnoses.
    Grounding the prompt fixed the cause; this catches the next instance.
    """
    from agent.cutpoint_agent.steps.narrator import summary_is_grounded

    detected = {23, 48, 69}

    ok, _ = summary_is_grounded(
        "The worst moment is at second 48, a static establishing shot.", detected
    )
    assert ok

    ok, why = summary_is_grounded(
        "A poorly rendered CGI explosion at second 12 loses the 18-24 cohort.", detected
    )
    assert not ok
    assert "12" in why


def test_grounding_check_is_case_insensitive_and_ignores_other_numbers():
    from agent.cutpoint_agent.steps.narrator import summary_is_grounded

    ok, _ = summary_is_grounded(
        "At Second 48 retention falls 13.5% across 2 cohorts.", {48}
    )
    assert ok, "percentages and cohort counts are not cliff citations"


def test_importing_the_agent_package_needs_no_credentials(monkeypatch):
    """agent.py exports root_agent at module scope, because that is the symbol
    `adk web` and `adk run` discover. Building it must therefore not require
    configuration: raising on a missing CLICKHOUSE_HOST made merely IMPORTING the
    package fail, which broke test collection everywhere without a .env and was
    the first thing CI hit.
    """
    import importlib

    monkeypatch.delenv("CLICKHOUSE_HOST", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)

    import agent.cutpoint_agent.agent as agent_mod

    reloaded = importlib.reload(agent_mod)
    assert next(s.name for s in reloaded.root_agent.sub_agents) == "analyst"


def test_run_live_still_fails_loud_on_missing_credentials(monkeypatch):
    """Moving the check off the import path must not lose it. A real run has to
    fail immediately and name the variable, not die deep inside the pipeline.
    """
    import asyncio

    from agent.run_pipeline import run_live
    from ingest.errors import MissingCredentialError

    monkeypatch.delenv("CLICKHOUSE_HOST", raising=False)

    with pytest.raises(MissingCredentialError) as excinfo:
        asyncio.run(run_live("demo_001"))
    assert "CLICKHOUSE_HOST" in str(excinfo.value)
