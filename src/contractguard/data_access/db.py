"""Tier 3 (data access): SQLite connection and schema."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS contract_versions (
    contract_name    TEXT    NOT NULL,
    version          INTEGER NOT NULL,
    contract_sha256  TEXT    NOT NULL,
    registered_at    TEXT    NOT NULL,
    PRIMARY KEY (contract_name, version)
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id             TEXT PRIMARY KEY,
    dataset            TEXT    NOT NULL,
    contract_version   INTEGER NOT NULL,
    source_file        TEXT    NOT NULL,
    file_sha256        TEXT    NOT NULL,
    started_at         TEXT    NOT NULL,
    ended_at           TEXT,
    status             TEXT    NOT NULL CHECK (status IN
        ('running','success','failed','rejected_schema','empty','skipped_already_loaded')),
    input_rows         INTEGER NOT NULL DEFAULT 0,
    inserted_rows      INTEGER NOT NULL DEFAULT 0,
    updated_rows       INTEGER NOT NULL DEFAULT 0,
    late_rows          INTEGER NOT NULL DEFAULT 0,
    unchanged_rows     INTEGER NOT NULL DEFAULT 0,
    rejected_rows      INTEGER NOT NULL DEFAULT 0,
    not_processed_rows INTEGER NOT NULL DEFAULT 0,
    failure_reason     TEXT
);
CREATE INDEX IF NOT EXISTS ix_runs_file
    ON pipeline_runs (dataset, file_sha256, status);

CREATE TABLE IF NOT EXISTS customers_clean (
    customer_id   TEXT PRIMARY KEY,
    full_name     TEXT NOT NULL,
    email         TEXT NOT NULL,
    country       TEXT NOT NULL,
    signup_date   TEXT NOT NULL,
    tier          TEXT NOT NULL,
    phone         TEXT,
    source_file   TEXT NOT NULL,
    first_run_id  TEXT NOT NULL REFERENCES pipeline_runs (run_id),
    last_run_id   TEXT NOT NULL REFERENCES pipeline_runs (run_id),
    loaded_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders_clean (
    order_id      TEXT PRIMARY KEY,
    customer_id   TEXT NOT NULL REFERENCES customers_clean (customer_id),
    order_ts      TEXT NOT NULL,
    status        TEXT NOT NULL,
    quantity      INTEGER NOT NULL CHECK (quantity >= 1),
    unit_price    REAL NOT NULL CHECK (unit_price > 0),
    currency      TEXT NOT NULL,
    coupon_code   TEXT,
    source_file   TEXT NOT NULL,
    first_run_id  TEXT NOT NULL REFERENCES pipeline_runs (run_id),
    last_run_id   TEXT NOT NULL REFERENCES pipeline_runs (run_id),
    loaded_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_orders_customer ON orders_clean (customer_id);

CREATE TABLE IF NOT EXISTS order_history (
    history_id      TEXT PRIMARY KEY,
    order_id        TEXT NOT NULL REFERENCES orders_clean (order_id),
    customer_id     TEXT NOT NULL,
    order_ts        TEXT NOT NULL,
    status          TEXT NOT NULL,
    quantity        INTEGER NOT NULL,
    unit_price      REAL NOT NULL,
    currency        TEXT NOT NULL,
    coupon_code     TEXT,
    is_late_arrival INTEGER NOT NULL DEFAULT 0 CHECK (is_late_arrival IN (0, 1)),
    run_id          TEXT NOT NULL REFERENCES pipeline_runs (run_id),
    source_file     TEXT NOT NULL,
    recorded_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_history_order ON order_history (order_id, order_ts);

CREATE TABLE IF NOT EXISTS quarantine (
    quarantine_id  TEXT PRIMARY KEY,
    run_id         TEXT NOT NULL REFERENCES pipeline_runs (run_id),
    dataset        TEXT NOT NULL,
    source_file    TEXT NOT NULL,
    file_sha256    TEXT NOT NULL,
    row_number     INTEGER NOT NULL,
    reason_codes   TEXT NOT NULL,
    reason_detail  TEXT NOT NULL,
    raw_record     TEXT NOT NULL,
    quarantined_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_quarantine_run ON quarantine (dataset, run_id);
"""


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string, e.g. 2026-11-14T09:30:00Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(db_path) -> sqlite3.Connection:
    """Open a SQLite connection with foreign keys enforced."""
    target = str(db_path)
    if target != ":memory:":
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables if they do not exist. Safe to call repeatedly."""
    conn.executescript(SCHEMA)
    conn.commit()
