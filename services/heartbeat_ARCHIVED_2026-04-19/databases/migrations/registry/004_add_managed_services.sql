-- Migration 004: Add managed_services table
-- HeartBeat's service manifest — defines which services to start, their order,
-- executable paths, restart policies, and current runtime state.
--
-- Schema matches HEARTBEAT_LIFECYCLE_SPEC.md Section 2.
-- Populated by the Installer at install time. HeartBeat reads on startup.

CREATE TABLE IF NOT EXISTS managed_services (
    service_name        TEXT PRIMARY KEY,
    instance_id         TEXT NOT NULL,
    executable_path     TEXT NOT NULL,
    working_directory   TEXT NOT NULL,
    arguments           TEXT,                           -- JSON array of CLI args
    environment         TEXT,                           -- JSON object of env vars
    startup_priority    INTEGER NOT NULL,               -- 0=HeartBeat, 1=Core, 2=Relay/HIS, 3=Edge, 4=Float
    auto_start          BOOLEAN NOT NULL DEFAULT 1,     -- Start on HeartBeat boot
    auto_restart        BOOLEAN NOT NULL DEFAULT 1,     -- Restart on crash
    restart_policy      TEXT NOT NULL DEFAULT 'immediate_3',  -- See LIFECYCLE_SPEC Section 4
    health_endpoint     TEXT,                           -- e.g. "http://localhost:8000/health"
    current_pid         INTEGER,                        -- Runtime PID (NULL when stopped)
    current_status      TEXT NOT NULL DEFAULT 'stopped', -- stopped/starting/healthy/degraded/unhealthy/crash_loop
    last_started_at     TEXT,
    last_stopped_at     TEXT,
    restart_count       INTEGER NOT NULL DEFAULT 0,
    last_restart_at     TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

-- Index for startup ordering
CREATE INDEX IF NOT EXISTS idx_managed_services_priority
    ON managed_services(startup_priority, auto_start);
