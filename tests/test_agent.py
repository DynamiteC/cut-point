"""Phase 5 gate: pipeline order is fixed, analyst output schema validates,
diagnostician prompt contains cliff metadata, reporter writes valid DirectorsNotes.
Gemini client and the extractor HTTP call are mocked -- no cloud calls.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from agent.cutpoint_agent.agent import build_root_agent
from agent.cutpoint_agent.prompts import diagnostician_prompt
from agent.cutpoint_agent.schemas import AnalysisResult, CliffPoint, ClipRef, ExtractionResult
from agent.cutpoint_agent.steps.diagnostician import run_diagnostics
from agent.cutpoint_agent.steps.extractor import run_extraction
from agent.cutpoint_agent.steps.reporter import build_directors_notes, write_report_json


def test_pipeline_order_is_fixed():
    root = build_root_agent()
    names = [a.name for a in root.sub_agents]
    assert names == ["analyst", "validator", "extractor", "diagnostician", "reporter"]
    # validator must sit immediately after analyst: every later step consumes
    # analysis_result, and it must be the ClickHouse-verified copy, not the
    # analyst's transcription.
    assert names.index("validator") == names.index("analyst") + 1


def test_analyst_output_schema_validates():
    root = build_root_agent()
    analyst = root.sub_agents[0]
    assert analyst.output_schema is AnalysisResult
    assert analyst.output_key == "analysis_result"

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

    diagnoses = run_diagnostics(analysis, extraction, mock_client, "gemini-3.5-flash")

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
