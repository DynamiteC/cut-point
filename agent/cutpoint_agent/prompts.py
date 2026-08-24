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
Return your findings strictly as the AnalysisResult schema:
- overall_retention_end: the retention_fraction at the last second from Query 1 (or average across cohorts at max second)
- milestone_funnel: a dict mapping milestone name ('reached_25pct', 'reached_50pct', 'reached_75pct', 'completed') to its fraction float from Query 4
- cliffs: the list of CliffPoint objects from Query 2 (second, drop_pct, z_score, affected_cohorts)
"""


def diagnostician_prompt(second: int, drop_pct: float, cohorts: list[str]) -> str:
    cohort_list = ", ".join(cohorts)
    drop_pct_display = round(drop_pct * 100, 1)
    return (
        f"At second {second}, {drop_pct_display}% of {cohort_list} viewers left. "
        "Describe exactly what happens on screen in this +/-5s window and give the most "
        "plausible causal hypothesis for why viewers in these cohorts specifically left at "
        "this moment. Rate severity 1-5 (5 = catastrophic churn driver) and your confidence "
        "0-1."
    )


REPORTER_ACTION_BY_SEVERITY = {
    1: "trim",
    2: "trim",
    3: "shorten",
    4: "replace_shot",
    5: "soften_reveal",
}
