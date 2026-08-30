"""Step 5: reporter -- merges analysis, extraction and diagnoses into
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


def public_clip_ref(clip_path: str) -> str:
    """What is safe to publish as a clip reference.

    GET /report/{id} is public. A local run writes an absolute path here, so the
    deployed API was serving the developer's home directory to anyone who asked,
    and advertising that the report came from a laptop rather than the pipeline.
    A gs:// URI is our own bucket and stays; anything else is reduced to its
    filename, which is all a reader can use anyway.
    """
    if clip_path.startswith("gs://"):
        return clip_path
    return Path(clip_path).name


def build_recommendation(cliff_second: int, hypothesis: str, severity: int) -> RecutRecommendation:
    action = REPORTER_ACTION_BY_SEVERITY.get(severity, "trim")
    target_range = (max(0, cliff_second - 5), cliff_second + 5)
    return RecutRecommendation(
        action=action,
        target_range_s=target_range,
        rationale=f"{hypothesis.rstrip('.')}; recommend {action} around second {cliff_second}.",
    )


def build_executive_summary(
    trailer_id: str, cliffs: list[CliffFinding], detected_total: int | None = None
) -> str:
    """The count and the superlative must reflect what the DATABASE found.

    Computing them over the diagnosed subset meant a single failed clip produced
    a confident, wrong headline: with cliffs at seconds 10 (20%) and 20 (30%),
    a timeout on second 20 yielded "loses the most viewers at second 10 ...
    1 cliff(s) total", when the worst is second 20 at 30%.
    """
    total = len(cliffs) if detected_total is None else detected_total
    if not cliffs:
        if total:
            return (
                f"{trailer_id}: {total} retention cliff(s) detected, but none could be "
                "diagnosed. See the undiagnosed list below."
            )
        return f"{trailer_id}: no significant retention cliffs detected."
    worst = max(cliffs, key=lambda c: c.drop_pct)
    complete = len(cliffs) == total
    # The superlative is only true across the whole detected set. With a cliff
    # undiagnosed, a bigger drop may sit in the part we could not explain, so the
    # claim is narrowed rather than left quietly false.
    lead = (
        f"{trailer_id} loses the most viewers at second {worst.second}"
        if complete
        else f"{trailer_id}: of the cliffs that could be explained, the worst is second {worst.second}"
    )
    coverage = (
        f"{len(cliffs)} cliff(s) total flagged for recut review."
        if complete
        else (
            f"{len(cliffs)} of {total} detected cliff(s) diagnosed; a larger drop may sit "
            "among the undiagnosed ones listed below."
        )
    )
    return (
        f"{lead} "
        f"({worst.drop_pct * 100:.1f}% drop among {', '.join(worst.affected_cohorts)}): "
        f"{worst.hypothesis} {coverage}"
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
    undiagnosed: list[dict] = []
    for cliff in analysis.cliffs:
        # Step 3 deliberately contains a per-clip failure and returns the
        # diagnoses it did get. Subscripting here threw KeyError on the first
        # missing one, so a single timed-out clip destroyed the whole report and
        # every paid inference in it -- undoing the containment step 3 exists to
        # provide. tests/test_agent.py covered run_diagnostics, not this.
        diagnosis = diagnosis_by_second.get(cliff.second)
        clip = clip_by_second.get(cliff.second)
        if diagnosis is None or clip is None:
            # Recorded, not swallowed. Skipping silently presented partial
            # coverage as complete.
            undiagnosed.append({
                "second": cliff.second,
                "drop_pct": cliff.drop_pct,
                "reason": "no diagnosis" if diagnosis is None else "no clip",
            })
            continue
        findings.append(
            CliffFinding(
                second=cliff.second,
                drop_pct=cliff.drop_pct,
                affected_cohorts=cliff.affected_cohorts,
                z_score=cliff.z_score,
                clip_path=public_clip_ref(clip.clip_path),
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
        executive_summary=build_executive_summary(trailer_id, findings, len(analysis.cliffs)),
        diagnosis_failures=undiagnosed,
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
        # The narrator writes the editor-facing paragraph from the verified
        # numbers. If it failed or produced nothing, keep the deterministic
        # template build_directors_notes already produced.
        narrated = (ctx.session.state.get("executive_summary") or "").strip()
        if narrated:
            notes = notes.model_copy(update={"executive_summary": narrated})
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
