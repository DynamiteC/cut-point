-- Per-second spread across cohorts, to surface cliffs that hit only one demographic.
-- Params: {trailer_id}
WITH per_second_cohort AS (
    SELECT
        cohort,
        second_offset,
        uniqMerge(viewers_state) AS viewers
    FROM cutpoint.mv_second_viewers
    WHERE trailer_id = '{trailer_id}'
    GROUP BY cohort, second_offset
),
baseline AS (
    SELECT
        cohort,
        avg(viewers) AS baseline_viewers
    FROM per_second_cohort
    WHERE second_offset <= 2
    GROUP BY cohort
),
normalized AS (
    SELECT
        p.second_offset AS second_offset,
        p.cohort AS cohort,
        p.viewers / b.baseline_viewers AS retention_fraction
    FROM per_second_cohort AS p
    INNER JOIN baseline AS b ON p.cohort = b.cohort
)
SELECT
    second_offset,
    max(retention_fraction) - min(retention_fraction) AS cohort_spread,
    argMin(cohort, retention_fraction) AS worst_cohort,
    min(retention_fraction) AS worst_cohort_retention
FROM normalized
GROUP BY second_offset
ORDER BY cohort_spread DESC
LIMIT 20
