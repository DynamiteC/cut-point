from pathlib import Path

SQL_DIR = Path(__file__).resolve().parent.parent.parent / "sql" / "analysis"


def _load_sql(name: str) -> str:
    path = SQL_DIR / name
    if path.exists():
        return path.read_text().strip()
    return ""


RETENTION_SQL = _load_sql("retention_curve.sql")
CHANGEPOINTS_SQL = _load_sql("changepoints.sql")
COHORT_DIVERGENCE_SQL = _load_sql("cohort_divergence.sql")
MILESTONE_FUNNEL_SQL = _load_sql("milestone_funnel.sql")

# ANALYST_INSTRUCTION lived here: the prompt that told an LlmAgent to run the four
# SQL files and transcribe the results. It was removed with the LLM analyst. The
# SQL constants below are still loaded because the narrator's tool boundary and
# the tests reference the same files.
def diagnostician_prompt(
    second: int, drop_pct: float, cohorts: list[str], start_s: int = 0, end_s: int | None = None
) -> str:
    """Prompt for one extracted clip.

    The clip is an excerpt whose own timeline starts at 00:00, not at `second`.
    Addressing the model with the absolute trailer timestamp made it refuse
    ("the provided images only cover 00:00:00 to 00:00:09"), and that refusal was
    written into the report. Anchor on the clip-relative offset instead.
    """
    cohort_list = ", ".join(cohorts)
    drop_pct_display = round(drop_pct * 100, 1)
    if end_s is None:
        end_s = second + 5
    offset = max(0, second - start_s)
    return (
        f"This video is a {end_s - start_s}s excerpt from a longer trailer, covering "
        f"trailer seconds {start_s} to {end_s}. Its own timeline starts at 00:00. "
        f"The moment of interest is {offset}s into THIS clip "
        f"(trailer second {second}), where {drop_pct_display}% of {cohort_list} viewers left. "
        "Describe exactly what happens on screen around that moment, using clip-relative "
        "times only, and give the most plausible causal hypothesis for why viewers in these "
        "cohorts specifically left there. Rate severity 1-5 (5 = catastrophic churn driver) "
        "and your confidence 0-1."
    )


REPORTER_ACTION_BY_SEVERITY = {
    1: "trim",
    2: "trim",
    3: "shorten",
    4: "replace_shot",
    5: "soften_reveal",
}
