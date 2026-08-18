-- Per-second retention curve for a trailer, normalized against the second 0-2 baseline.
-- Params: {trailer_id}
WITH per_second AS (
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
    FROM per_second
    WHERE second_offset <= 2
    GROUP BY cohort
)
SELECT
    p.cohort AS cohort,
    p.second_offset AS second_offset,
    p.viewers AS viewers,
    b.baseline_viewers AS baseline_viewers,
    p.viewers / b.baseline_viewers AS retention_fraction
FROM per_second AS p
INNER JOIN baseline AS b ON p.cohort = b.cohort
ORDER BY cohort, second_offset
;
