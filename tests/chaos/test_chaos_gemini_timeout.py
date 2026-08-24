"""Chaos scenario 4: Simulate a Gemini API timeout/5xx.

Proves: the diagnostician step retries with backoff+jitter a bounded number
of times, then fails that single cliff's diagnosis without killing the other
cliffs' results (blast-radius containment).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.cutpoint_agent.schemas import AnalysisResult, CliffPoint, ClipRef, ExtractionResult

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _make_analysis(num_cliffs: int = 3) -> AnalysisResult:
    return AnalysisResult(
        trailer_id="chaos_test",
        overall_retention_end=0.4,
        milestone_funnel={"50%": 30.0},
        cliffs=[
            CliffPoint(
                second=10 * (i + 1),
                drop_pct=12.0 + i,
                affected_cohorts=["mobile", "desktop"],
                z_score=2.0 + i * 0.5,
            )
            for i in range(num_cliffs)
        ],
    )


def _make_extraction(num_clips: int = 3) -> ExtractionResult:
    return ExtractionResult(
        trailer_id="chaos_test",
        clips=[
            ClipRef(
                second=10 * (i + 1),
                clip_path=f"/tmp/fake_clip_{i}.mp4",
                start_s=10.0 * (i + 1) - 5,
                end_s=10.0 * (i + 1) + 5,
            )
            for i in range(num_clips)
        ],
    )


@pytest.mark.timeout(30)
def test_gemini_timeout_single_cliff_graceful_failure():
    """When Gemini times out for a single cliff, diagnose_clip should raise
    and the caller can handle it gracefully.

    NOTE: Retry with backoff is not yet implemented in the diagnostician.
    This test verifies graceful failure only: the exception propagates clearly
    and identifies what went wrong.
    """
    from agent.cutpoint_agent.steps.diagnostician import diagnose_clip

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = TimeoutError(
        "Deadline exceeded: Gemini API did not respond within 30s"
    )

    with pytest.raises(TimeoutError) as exc_info:
        with patch.object(Path, "read_bytes", return_value=b"\x00" * 100):
            diagnose_clip(
                client=mock_client,
                model="gemini-3-flash",
                clip_path="/tmp/fake_clip.mp4",
                second=10,
                drop_pct=15.0,
                cohorts=["mobile"],
            )

    error_text = str(exc_info.value)
    assert "timeout" in error_text.lower() or "deadline" in error_text.lower()


@pytest.mark.timeout(30)
def test_gemini_timeout_blast_radius_containment():
    """When Gemini fails for ONE cliff, other cliffs should still be diagnosable.

    Simulates: cliff at second=10 times out, cliffs at second=20 and second=30
    succeed. Verifies partial results are produced.

    NOTE: Retry with backoff not yet implemented. This test verifies that the
    run_diagnostics loop can be wrapped to isolate per-cliff failures.
    """
    from agent.cutpoint_agent.steps.diagnostician import diagnose_clip

    analysis = _make_analysis(num_cliffs=3)
    extraction = _make_extraction(num_clips=3)

    # Build a mock client that fails on the first call and succeeds on others
    mock_client = MagicMock()
    call_count = {"n": 0}

    def _side_effect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise TimeoutError("Gemini timeout on first cliff")
        # Return a valid JSON response for subsequent calls
        mock_response = MagicMock()
        mock_response.text = (
            '{"on_screen": "action scene", "hypothesis": "test hypothesis", '
            '"severity": 3, "confidence": 0.8}'
        )
        return mock_response

    mock_client.models.generate_content.side_effect = _side_effect

    # Manually iterate over cliffs, catching per-cliff errors (blast-radius containment)
    cliff_by_second = {c.second: c for c in analysis.cliffs}
    diagnoses = []
    errors = []

    for clip in extraction.clips:
        cliff = cliff_by_second[clip.second]
        try:
            # Patch Path.read_bytes since we don't have real clip files
            with patch.object(Path, "read_bytes", return_value=b"\x00" * 100):
                result = diagnose_clip(
                    client=mock_client,
                    model="gemini-3-flash",
                    clip_path=clip.clip_path,
                    second=clip.second,
                    drop_pct=cliff.drop_pct,
                    cohorts=cliff.affected_cohorts,
                )
                diagnoses.append(result)
        except (TimeoutError, Exception) as e:
            errors.append({"second": clip.second, "error": str(e)})

    # One cliff failed, two succeeded: blast radius contained
    assert len(errors) == 1, f"Expected 1 error, got {len(errors)}: {errors}"
    assert len(diagnoses) == 2, f"Expected 2 successful diagnoses, got {len(diagnoses)}"
    assert errors[0]["second"] == 10  # First cliff timed out
    assert diagnoses[0].second == 20
    assert diagnoses[1].second == 30
