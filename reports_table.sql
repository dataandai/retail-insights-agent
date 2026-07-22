-- Reports table for the Retail Insights Agent n8n workflow.
-- Run this once against the Postgres database configured in the
-- Save Report / Resolve Owner Reports / Owner-Scoped Delete nodes.

CREATE TABLE IF NOT EXISTS reports (
    id          SERIAL PRIMARY KEY,
    user_id     TEXT        NOT NULL,
    question    TEXT        NOT NULL,
    sql         TEXT        NOT NULL,
    report      TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Owner-scoped lookups drive both resolve and delete paths.
CREATE INDEX IF NOT EXISTS idx_reports_user_id ON reports (user_id);
CREATE INDEX IF NOT EXISTS idx_reports_user_created ON reports (user_id, created_at DESC);
