"""Instruction / prompt templates for the LLM-backed pipeline steps."""

ANALYST_INSTRUCTION = """You are the analyst step of a fixed diagnostic pipeline. You do not
decide what to do -- you execute exactly four SQL templates against ClickHouse via the
run_query tool, in this order, for trailer_id={trailer_id}:

1. sql/analysis/retention_curve.sql
2. sql/analysis/changepoints.sql
3. sql/analysis/cohort_divergence.sql
4. sql/analysis/milestone_funnel.sql

Fill {{trailer_id}} with the given trailer_id and nothing else. Do not invent new queries.
Return your findings strictly as the AnalysisResult schema: overall_retention_end (the
retention_fraction at the last second of retention_curve.sql), milestone_funnel (a dict from
milestone_funnel.sql), and cliffs (the rows of changepoints.sql, one CliffPoint per row)."""


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
