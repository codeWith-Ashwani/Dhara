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
