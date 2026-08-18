-- Changepoint (retention cliff) detector.
-- Aggregates all cohorts into an overall per-second retention curve, computes a
-- robust z-score (median absolute deviation) over the second-to-second delta,
-- and flags seconds where the drop is both statistically unusual and large enough
-- to matter (drop_pct >= 3%).
-- Params: {trailer_id}
WITH overall AS (
    SELECT
        second_offset,
        uniqMerge(viewers_state) AS viewers
    FROM cutpoint.mv_second_viewers
    WHERE trailer_id = '{trailer_id}'
    GROUP BY second_offset
    ORDER BY second_offset
),
baseline AS (
    SELECT avg(viewers) AS baseline_viewers
    FROM overall
    WHERE second_offset <= 2
),
deltas AS (
    SELECT
        second_offset,
        viewers,
        viewers - lagInFrame(viewers, 1, viewers) OVER (ORDER BY second_offset) AS delta,
        (lagInFrame(viewers, 1, viewers) OVER (ORDER BY second_offset) - viewers)
            / (lagInFrame(viewers, 1, viewers) OVER (ORDER BY second_offset) + 1) AS drop_pct
    FROM overall
),
stats AS (
    SELECT
        median(delta) AS med_delta,
        medianAbsDeviation(delta) AS mad_delta
    FROM deltas
)
SELECT
    d.second_offset AS second,
    d.drop_pct AS drop_pct,
    (d.delta - s.med_delta) / (s.mad_delta * 1.4826 + 1) AS z_score,
    groupArray(cohort_hits.cohort) AS affected_cohorts
FROM deltas AS d
CROSS JOIN stats AS s
LEFT JOIN (
    SELECT cohort, second_offset
    FROM cutpoint.mv_second_viewers
    WHERE trailer_id = '{trailer_id}'
) AS cohort_hits ON cohort_hits.second_offset = d.second_offset
WHERE abs((d.delta - s.med_delta) / (s.mad_delta * 1.4826 + 1)) > 3
  AND d.drop_pct >= 0.03
GROUP BY d.second_offset, d.drop_pct, z_score
ORDER BY drop_pct DESC
LIMIT 10
;
