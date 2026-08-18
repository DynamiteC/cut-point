CREATE DATABASE IF NOT EXISTS cutpoint;

CREATE TABLE IF NOT EXISTS cutpoint.raw_playback_events
(
    event_ts        DateTime64(3, 'UTC'),
    event_date      Date DEFAULT toDate(event_ts),
    trailer_id      LowCardinality(String),
    session_id      UUID,
    cohort          LowCardinality(String),
    region          LowCardinality(String),
    device          LowCardinality(String),
    second_offset   UInt16,
    event_type      Enum8('start'=1,'heartbeat'=2,'exit'=3,'complete'=4)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(event_date)
ORDER BY (trailer_id, cohort, second_offset, event_ts);

CREATE TABLE IF NOT EXISTS cutpoint.trailers
(
    trailer_id      LowCardinality(String),
    title           String,
    duration_s      UInt16,
    video_path      String,
    created_at      DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(created_at)
ORDER BY trailer_id;
