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

ANALYST_INSTRUCTION = f"""You are the analyst step of a fixed diagnostic pipeline. You do not
decide what to do -- you execute exactly four SQL queries against ClickHouse via the
run_query tool, in this order, for trailer_id={{trailer_id}}:

--- Query 1: Retention Curve ---
{RETENTION_SQL}

--- Query 2: Changepoints (Cliffs) ---
{CHANGEPOINTS_SQL}

--- Query 3: Cohort Divergence ---
{COHORT_DIVERGENCE_SQL}

--- Query 4: Milestone Funnel ---
{MILESTONE_FUNNEL_SQL}

Replace '{{trailer_id}}' with the actual trailer_id parameter value before running each query with run_query. Do not invent new queries.

OUTPUT RULES -- these matter, Query 1 returns thousands of per-second rows:
- Emit ONLY the AnalysisResult JSON. No prose, no markdown, no tables, no LaTeX.
- Never echo raw query rows back. Query 1 and Query 3 are context for your
  reading only; nothing from them belongs in the output except the single
  overall_retention_end float.
- cliffs comes from Query 2 only, and Query 2 already returns at most 10 rows.
  Never emit more than 10 cliffs.
- Keep the whole response under 4000 tokens. If you are producing a long
  response you have misunderstood the task.

Return your findings strictly as the AnalysisResult schema:
- overall_retention_end: the retention_fraction at the last second from Query 1 (or average across cohorts at max second)
- milestone_funnel: a dict mapping milestone name ('reached_25pct', 'reached_50pct', 'reached_75pct', 'completed') to its fraction float from Query 4
- cliffs: the list of CliffPoint objects from Query 2 (second, drop_pct, z_score, affected_cohorts)
"""


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
