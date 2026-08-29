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
    """Evidence that the numbers in the report came from ClickHouse, not the LLM."""

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
