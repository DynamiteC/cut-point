"""Step 4: reporter -- merges analysis, extraction and diagnoses into
DirectorsNotes, writes JSON, then renders MD + HTML.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.events.event_actions import EventActions

from agent.cutpoint_agent import store
from agent.cutpoint_agent.prompts import REPORTER_ACTION_BY_SEVERITY
from agent.cutpoint_agent.schemas import (
    AnalysisResult,
    CliffFinding,
    Diagnosis,
    DirectorsNotes,
    ExtractionResult,
    RecutRecommendation,
    ValidationReport,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def build_recommendation(cliff_second: int, hypothesis: str, severity: int) -> RecutRecommendation:
    action = REPORTER_ACTION_BY_SEVERITY.get(severity, "trim")
    target_range = (max(0, cliff_second - 5), cliff_second + 5)
    return RecutRecommendation(
        action=action,
        target_range_s=target_range,
        rationale=f"{hypothesis.rstrip('.')}; recommend {action} around second {cliff_second}.",
    )


def build_executive_summary(trailer_id: str, cliffs: list[CliffFinding]) -> str:
    if not cliffs:
        return f"{trailer_id}: no significant retention cliffs detected."
    worst = max(cliffs, key=lambda c: c.drop_pct)
    return (
        f"{trailer_id} loses the most viewers at second {worst.second} "
        f"({worst.drop_pct * 100:.1f}% drop among {', '.join(worst.affected_cohorts)}): "
        f"{worst.hypothesis} {len(cliffs)} cliff(s) total flagged for recut review."
    )


def build_directors_notes(
    trailer_id: str,
    title: str,
    duration_s: int,
    analysis: AnalysisResult,
    extraction: ExtractionResult,
    diagnoses: list[Diagnosis],
) -> DirectorsNotes:
    diagnosis_by_second = {d.second: d for d in diagnoses}
    clip_by_second = {c.second: c for c in extraction.clips}

    findings: list[CliffFinding] = []
    for cliff in analysis.cliffs:
        diagnosis = diagnosis_by_second[cliff.second]
        clip = clip_by_second[cliff.second]
        findings.append(
            CliffFinding(
                second=cliff.second,
                drop_pct=cliff.drop_pct,
                affected_cohorts=cliff.affected_cohorts,
                z_score=cliff.z_score,
                clip_path=clip.clip_path,
                on_screen=diagnosis.on_screen,
                hypothesis=diagnosis.hypothesis,
                severity=diagnosis.severity,
                recommendations=[
                    build_recommendation(cliff.second, diagnosis.hypothesis, diagnosis.severity)
                ],
            )
        )

    return DirectorsNotes(
        trailer_id=trailer_id,
        title=title,
        duration_s=duration_s,
        analyzed_at=datetime.now(UTC),
        overall_retention_end=analysis.overall_retention_end,
        milestone_funnel=analysis.milestone_funnel,
        cliffs=findings,
        executive_summary=build_executive_summary(trailer_id, findings),
    )


def write_report_json(notes: DirectorsNotes, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or (REPO_ROOT / "data" / "reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{notes.trailer_id}.json"
    out_path.write_text(notes.model_dump_json(indent=2))
    return out_path


class ReporterAgent(BaseAgent):
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        analysis = AnalysisResult.model_validate(ctx.session.state["analysis_result"])
        extraction = ExtractionResult.model_validate(ctx.session.state["extraction_result"])
        diagnoses = [Diagnosis.model_validate(d) for d in ctx.session.state["diagnoses"]]
        title = ctx.session.state.get("title", analysis.trailer_id)
        duration_s = ctx.session.state.get("duration_s", 0)

        notes = build_directors_notes(
            analysis.trailer_id, title, duration_s, analysis, extraction, diagnoses
        )
        validation = ctx.session.state.get("validation_report")
        if validation:
            notes = notes.model_copy(
                update={"validation": ValidationReport.model_validate(validation)}
            )
        report_path = write_report_json(notes)
        # On Cloud Run the line above lands on an ephemeral per-instance disk that
        # the instance serving GET /report will never see. In firestore mode the
        # durable copy is what the API actually reads back.
        if store.using_firestore():
            store.save_report(analysis.trailer_id, notes.model_dump(mode="json"))

        yield Event(
            author=self.name,
            actions=EventActions(
                state_delta={
                    "directors_notes": notes.model_dump(mode="json"),
                    "report_path": str(report_path),
                }
            ),
        )
