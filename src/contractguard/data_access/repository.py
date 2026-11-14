"""Tier 3 (data access): run log, quarantine and contract registry."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from contractguard.data_access.db import utc_now_iso

COUNT_FIELDS = (
    "input_rows", "inserted_rows", "updated_rows", "late_rows",
    "unchanged_rows", "rejected_rows", "not_processed_rows",
)


class ContractChangedError(Exception):
    """A registered contract version was edited instead of publishing a new one."""


def new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run_{stamp}_{uuid.uuid4().hex[:6]}"


def start_run(conn, dataset, contract_version, source_file, file_sha256) -> str:
    """Record a new run as 'running' and commit, so even a crash leaves a trace."""
    run_id = new_run_id()
    conn.execute(
        "INSERT INTO pipeline_runs (run_id, dataset, contract_version, source_file, "
        "file_sha256, started_at, status) VALUES (?, ?, ?, ?, ?, ?, 'running')",
        (run_id, dataset, contract_version, source_file, file_sha256, utc_now_iso()),
    )
    conn.commit()
    return run_id


def finish_run(conn, run_id, status, counts=None, failure_reason=None) -> None:
    """Close a run with its final status, row counts and optional failure reason."""
    counts = counts or {}
    unknown = set(counts) - set(COUNT_FIELDS)
    if unknown:
        raise ValueError(f"unknown count field(s): {sorted(unknown)}")
    assignments = ["status = ?", "ended_at = ?", "failure_reason = ?"]
    params = [status, utc_now_iso(), failure_reason]
    for name in COUNT_FIELDS:  # column names come from this fixed list, never from input
        if name in counts:
            assignments.append(f"{name} = ?")
            params.append(int(counts[name]))
    params.append(run_id)
    cursor = conn.execute(
        f"UPDATE pipeline_runs SET {', '.join(assignments)} WHERE run_id = ?", params
    )
    if cursor.rowcount == 0:
        conn.rollback()
        raise LookupError(f"run not found: {run_id}")
    conn.commit()


def get_run(conn, run_id):
    return conn.execute("SELECT * FROM pipeline_runs WHERE run_id = ?", (run_id,)).fetchone()


def find_loaded_run(conn, dataset, file_sha256, contract_version):
    """Return an earlier successful run of the same file content, or None."""
    return conn.execute(
        "SELECT * FROM pipeline_runs WHERE dataset = ? AND file_sha256 = ? "
        "AND contract_version = ? AND status = 'success' "
        "ORDER BY started_at LIMIT 1",
        (dataset, file_sha256, contract_version),
    ).fetchone()


def quarantine_id(dataset, file_sha256, row_number) -> str:
    """Deterministic ID: the same bad row in the same file always gets the same ID."""
    digest = hashlib.sha256(f"{dataset}|{file_sha256}|{row_number}".encode()).hexdigest()
    return f"q_{digest[:16]}"


def add_quarantine(conn, run_id, dataset, source_file, file_sha256, items) -> int:
    """Store rejected rows; returns how many were new. The caller commits.

    Each item is a dict with: row_number, reason_codes (list), reason_detail, raw.
    """
    now = utc_now_iso()
    records = [
        (
            quarantine_id(dataset, file_sha256, item["row_number"]),
            run_id, dataset, source_file, file_sha256, item["row_number"],
            ",".join(item["reason_codes"]), item["reason_detail"],
            json.dumps(item["raw"], ensure_ascii=False, sort_keys=True), now,
        )
        for item in items
    ]
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO quarantine (quarantine_id, run_id, dataset, source_file, "
        "file_sha256, row_number, reason_codes, reason_detail, raw_record, quarantined_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        records,
    )
    return conn.total_changes - before


def register_contract(conn, contract, path) -> str:
    """Remember a contract version's content hash. Returns 'registered' or 'unchanged'."""
    sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    row = conn.execute(
        "SELECT contract_sha256 FROM contract_versions WHERE contract_name = ? AND version = ?",
        (contract.name, contract.version),
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO contract_versions (contract_name, version, contract_sha256, "
            "registered_at) VALUES (?, ?, ?, ?)",
            (contract.name, contract.version, sha, utc_now_iso()),
        )
        conn.commit()
        return "registered"
    if row["contract_sha256"] == sha:
        return "unchanged"
    raise ContractChangedError(
        f"{contract.name} v{contract.version} is already registered with different "
        "content; publish a new version instead of editing it"
    )
