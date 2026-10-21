-- AgentForge application tables.
--
-- Postgres is the source of truth for every run. The queue (Pub/Sub, Day 4) will only
-- carry run IDs; everything else lives here.
--
-- LangGraph's own checkpoint tables are NOT here: PostgresSaver.setup() creates them.
-- Safe to run many times: every statement uses IF NOT EXISTS.

-- People who use the app. id will be the Google account ID once sign-in exists (Day 3).
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    email       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per run of an agent. This is what the api writes and the worker claims.
CREATE TABLE IF NOT EXISTS runs (
    id           UUID PRIMARY KEY,               -- also the LangGraph thread_id
    user_id      TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    template     TEXT NOT NULL DEFAULT 'job_hunt',
    status       TEXT NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    attempt      INT  NOT NULL DEFAULT 0,        -- how many times a worker has claimed it
    lease_until  TIMESTAMPTZ,                    -- a worker's claim expires at this time (Day 4)
    error        TEXT,                           -- why it failed, shown to the user
    input        JSONB NOT NULL,                 -- job ad and CV (deleted after 30 days, Day 5)
    result       JSONB,                          -- the agent's output once it succeeds
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- "Show me my runs, newest first."
CREATE INDEX IF NOT EXISTS runs_user_created_idx ON runs (user_id, created_at DESC);
-- The sweeper (Day 4) looks for runs stuck in one status for too long.
CREATE INDEX IF NOT EXISTS runs_status_updated_idx ON runs (status, updated_at);

-- Progress events for one run, streamed to the browser (Day 3).
-- id only ever goes up, so the browser can say "send me everything after event 41".
CREATE TABLE IF NOT EXISTS run_events (
    id          BIGSERIAL PRIMARY KEY,
    run_id      UUID NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,                   -- e.g. queued, step_done, succeeded, failed
    data        JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS run_events_run_idx ON run_events (run_id, id);

-- The user's application tracker: the agent's ONE side effect.
-- UNIQUE (run_id) is the idempotency guarantee: however many times the save step runs
-- for a run (retries, crashes, duplicate messages), there can only ever be one row.
-- No foreign key to runs yet, because terminal runs (agent.run) don't create a runs row.
-- Add it on Day 4, when every run comes through the api.
CREATE TABLE IF NOT EXISTS tracker (
    id           BIGSERIAL PRIMARY KEY,
    run_id       UUID NOT NULL UNIQUE,
    user_id      TEXT NOT NULL,
    company      TEXT NOT NULL,
    match_score  INT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
