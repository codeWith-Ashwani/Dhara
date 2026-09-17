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
