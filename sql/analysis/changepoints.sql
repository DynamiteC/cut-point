-- Changepoint (retention cliff) detector.
-- Aggregates all cohorts into an overall per-second retention curve, computes a
-- robust z-score (median absolute deviation) over the second-to-second delta,
-- and flags seconds where the drop is both statistically unusual and large enough
-- to matter (drop_pct >= 3%). affected_cohorts lists only cohorts whose own
-- per-cohort drop at that second is at least half the overall drop (i.e. cohorts
-- that actually contributed to the cliff, not every cohort with data at that second).
-- Params: trailer_id (String), bound server-side via clickhouse-connect.
WITH overall AS (
    SELECT
        second_offset,
        uniqMerge(viewers_state) AS viewers
    FROM cutpoint.mv_second_viewers
    WHERE trailer_id = {trailer_id:String}
    GROUP BY second_offset
    ORDER BY second_offset
),
deltas AS (
    SELECT
        second_offset,
        viewers,
        lagInFrame(viewers, 1, viewers) OVER (ORDER BY second_offset) AS prev_viewers,
        viewers - lagInFrame(viewers, 1, viewers) OVER (ORDER BY second_offset) AS delta,
        (lagInFrame(viewers, 1, viewers) OVER (ORDER BY second_offset) - viewers)
            / (lagInFrame(viewers, 1, viewers) OVER (ORDER BY second_offset) + 1) AS drop_pct
    FROM overall
),
med AS (
    SELECT median(delta) AS med_delta FROM deltas
),
mad AS (
    SELECT median(abs(d.delta - m.med_delta)) AS mad_delta
    FROM deltas AS d, med AS m
),
per_cohort AS (
    SELECT
        cohort,
        second_offset,
        uniqMerge(viewers_state) AS viewers
    FROM cutpoint.mv_second_viewers
    WHERE trailer_id = {trailer_id:String}
    GROUP BY cohort, second_offset
),
per_cohort_delta AS (
    SELECT
        cohort,
        second_offset,
        (lagInFrame(viewers, 1, viewers) OVER (PARTITION BY cohort ORDER BY second_offset) - viewers)
            / (lagInFrame(viewers, 1, viewers) OVER (PARTITION BY cohort ORDER BY second_offset) + 1)
            AS cohort_drop_pct
    FROM per_cohort
),
flagged AS (
    SELECT
        d.second_offset AS second,
        d.drop_pct AS drop_pct,
        (d.delta - m.med_delta) / (mad.mad_delta * 1.4826 + 1) AS z_score
    FROM deltas AS d
    CROSS JOIN med AS m
    CROSS JOIN mad AS mad
    WHERE abs((d.delta - m.med_delta) / (mad.mad_delta * 1.4826 + 1)) > 3
      AND d.drop_pct >= 0.03
)
SELECT
    f.second AS second,
    f.drop_pct AS drop_pct,
    f.z_score AS z_score,
    groupArray(pcd.cohort) AS affected_cohorts
FROM flagged AS f
INNER JOIN per_cohort_delta AS pcd
    ON pcd.second_offset = f.second AND pcd.cohort_drop_pct >= f.drop_pct * 0.5
GROUP BY f.second, f.drop_pct, f.z_score
ORDER BY drop_pct DESC
LIMIT 10
