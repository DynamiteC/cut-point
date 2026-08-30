"""Pydantic models shared across the pipeline steps and the frontend API contract."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CliffPoint(BaseModel):
    second: int
    drop_pct: float
    affected_cohorts: list[str]
    z_score: float


class AnalysisResult(BaseModel):
    trailer_id: str
    overall_retention_end: float
    milestone_funnel: dict[str, float]
    cliffs: list[CliffPoint]


class ValidationReport(BaseModel):
    """Evidence that the numbers came from ClickHouse rather than from a model.

    This establishes provenance and reproducibility, NOT correctness. If the SQL
    is wrong the numbers are wrong, reproducibly. Detector accuracy is a separate
    claim, evidenced by tests/test_detector.py against injected ground truth with
    demo_control as a non-circular false-positive control.
    """

    # True when the model's transcription happened to agree with the database.
    # It does not mean the database is right.
    verified: bool
    source_rows: int
    llm_cliff_count: int
    verified_cliff_count: int
    corrected: list[str] = Field(default_factory=list)


class ClipRef(BaseModel):
    second: int
    clip_path: str
    start_s: float
    end_s: float


class ExtractionResult(BaseModel):
    trailer_id: str
    clips: list[ClipRef]


class Diagnosis(BaseModel):
    second: int
    on_screen: str
    hypothesis: str
    severity: int = Field(ge=1, le=5)
    confidence: float = Field(ge=0.0, le=1.0)


class RecutRecommendation(BaseModel):
    action: str  # 'trim' | 'reorder' | 'replace_shot' | 'shorten' | 'soften_reveal'
    target_range_s: tuple[int, int]
    rationale: str


class CliffFinding(BaseModel):
    second: int
    drop_pct: float
    affected_cohorts: list[str]
    z_score: float
    clip_path: str
    on_screen: str
    hypothesis: str
    severity: int
    recommendations: list[RecutRecommendation]


class DirectorsNotes(BaseModel):
    trailer_id: str
    title: str
    duration_s: int
    analyzed_at: datetime
    overall_retention_end: float
    milestone_funnel: dict[str, float]
    cliffs: list[CliffFinding]
    executive_summary: str
    # Evidence that every number above was re-derived from ClickHouse rather
    # than trusted from the analyst's transcription. Optional so existing
    # fixtures and reports stay loadable.
    validation: ValidationReport | None = None
    # Cliffs the database found that no diagnosis covers, usually because the
    # model call for that clip failed. Skipping them silently let the report
    # name the wrong second as worst and understate the total. Optional so
    # existing fixtures and published reports stay loadable.
    diagnosis_failures: list[dict] = Field(default_factory=list)
