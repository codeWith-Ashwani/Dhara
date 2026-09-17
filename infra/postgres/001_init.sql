CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS observations (
    id UUID NOT NULL,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    metric TEXT NOT NULL,
    value DOUBLE PRECISION NOT NULL,
    unit TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    geom GEOGRAPHY(POINT, 4326) NOT NULL,
    h3_r9 TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('accepted', 'flagged')),
    quality_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (id, observed_at),
    UNIQUE (source, external_id, observed_at)
);

SELECT create_hypertable('observations', by_range('observed_at'), if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS observations_h3_time_idx
    ON observations (h3_r9, observed_at DESC);
CREATE INDEX IF NOT EXISTS observations_geom_idx
    ON observations USING GIST (geom);

CREATE TABLE IF NOT EXISTS decision_audit_events (
    sequence BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id UUID NOT NULL UNIQUE,
    alert_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (
        action IN (
            'confirm',
            'request_ground_verification',
            'reject_as_false',
            'escalate_publish_cap'
        )
    ),
    actor_id TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    reason TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    previous_hash CHAR(64) NOT NULL,
    event_hash CHAR(64) NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS decision_audit_alert_sequence_idx
    ON decision_audit_events (alert_id, sequence);

CREATE OR REPLACE FUNCTION reject_decision_audit_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'decision audit events are append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS decision_audit_no_update ON decision_audit_events;
CREATE TRIGGER decision_audit_no_update
BEFORE UPDATE OR DELETE ON decision_audit_events
FOR EACH ROW EXECUTE FUNCTION reject_decision_audit_mutation();

CREATE TABLE IF NOT EXISTS alert_outcomes (
    outcome_id UUID PRIMARY KEY,
    alert_id TEXT NOT NULL UNIQUE,
    h3_r9 TEXT NOT NULL,
    label TEXT NOT NULL CHECK (label IN ('confirmed', 'refuted')),
    observed_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    actor_id TEXT NOT NULL,
    source TEXT NOT NULL,
    notes TEXT NOT NULL,
    report_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    reporter_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    sensor_confidence DOUBLE PRECISION NOT NULL CHECK (
        sensor_confidence BETWEEN 0 AND 1
    ),
    crowd_confidence DOUBLE PRECISION NOT NULL CHECK (
        crowd_confidence BETWEEN 0 AND 1
    ),
    fused_confidence DOUBLE PRECISION NOT NULL CHECK (
        fused_confidence BETWEEN 0 AND 1
    ),
    predicted_tier TEXT NOT NULL,
    data_classification TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS alert_outcomes_cell_recorded_idx
    ON alert_outcomes (h3_r9, recorded_at DESC);

CREATE TABLE IF NOT EXISTS calibration_artifacts (
    version TEXT PRIMARY KEY,
    stream TEXT NOT NULL CHECK (stream IN ('sensor', 'crowd', 'fused')),
    created_at TIMESTAMPTZ NOT NULL,
    input_digest CHAR(64) NOT NULL,
    sample_count INTEGER NOT NULL CHECK (sample_count > 0),
    brier_before DOUBLE PRECISION NOT NULL,
    brier_after DOUBLE PRECISION NOT NULL,
    promoted BOOLEAN NOT NULL DEFAULT FALSE,
    promotion_reason TEXT NOT NULL,
    bins JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS calibration_stream_created_idx
    ON calibration_artifacts (stream, created_at DESC);

CREATE TABLE IF NOT EXISTS zone_policy_artifacts (
    version TEXT PRIMARY KEY,
    h3_r9 TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    input_digest CHAR(64) NOT NULL,
    sample_count INTEGER NOT NULL CHECK (sample_count > 0),
    false_alarms INTEGER NOT NULL CHECK (false_alarms >= 0),
    misses INTEGER NOT NULL CHECK (misses >= 0),
    miss_cost DOUBLE PRECISION NOT NULL CHECK (miss_cost >= 0),
    false_alarm_cost DOUBLE PRECISION NOT NULL CHECK (false_alarm_cost >= 0),
    sensor_weight DOUBLE PRECISION NOT NULL CHECK (sensor_weight BETWEEN 0 AND 1),
    corroboration_bonus DOUBLE PRECISION NOT NULL CHECK (corroboration_bonus >= 0),
    advisory_threshold DOUBLE PRECISION NOT NULL CHECK (
        advisory_threshold BETWEEN 0 AND 1
    ),
    watch_threshold DOUBLE PRECISION NOT NULL CHECK (watch_threshold BETWEEN 0 AND 1),
    warning_threshold DOUBLE PRECISION NOT NULL CHECK (
        warning_threshold BETWEEN 0 AND 1
    )
);

CREATE INDEX IF NOT EXISTS zone_policy_cell_created_idx
    ON zone_policy_artifacts (h3_r9, created_at DESC);

CREATE TABLE IF NOT EXISTS learning_job_runs (
    sequence BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id UUID NOT NULL UNIQUE,
    input_digest CHAR(64) NOT NULL UNIQUE,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('completed', 'failed')),
    result JSONB NOT NULL,
    previous_hash CHAR(64) NOT NULL,
    event_hash CHAR(64) NOT NULL UNIQUE
);

CREATE OR REPLACE FUNCTION reject_learning_record_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'outcome and learning records are append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS alert_outcomes_no_update ON alert_outcomes;
CREATE TRIGGER alert_outcomes_no_update
BEFORE UPDATE OR DELETE ON alert_outcomes
FOR EACH ROW EXECUTE FUNCTION reject_learning_record_mutation();

DROP TRIGGER IF EXISTS calibration_artifacts_no_update ON calibration_artifacts;
CREATE TRIGGER calibration_artifacts_no_update
BEFORE UPDATE OR DELETE ON calibration_artifacts
FOR EACH ROW EXECUTE FUNCTION reject_learning_record_mutation();

DROP TRIGGER IF EXISTS zone_policy_artifacts_no_update ON zone_policy_artifacts;
CREATE TRIGGER zone_policy_artifacts_no_update
BEFORE UPDATE OR DELETE ON zone_policy_artifacts
FOR EACH ROW EXECUTE FUNCTION reject_learning_record_mutation();

DROP TRIGGER IF EXISTS learning_job_runs_no_update ON learning_job_runs;
CREATE TRIGGER learning_job_runs_no_update
BEFORE UPDATE OR DELETE ON learning_job_runs
FOR EACH ROW EXECUTE FUNCTION reject_learning_record_mutation();
