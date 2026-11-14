"""Tier 2 (application): run one producer file through read -> validate -> load."""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

from contractguard.application.contract_loader import load_contract
from contractguard.application.validator import check_references, validate_batch
from contractguard.data_access import repository
from contractguard.data_access.db import utc_now_iso
from contractguard.data_access.reader import SourceFileError, read_csv_file

log = logging.getLogger("contractguard.pipeline")

# Table and column names come from this fixed map, never from user input.
TARGETS = {
    "customers": {
        "table": "customers_clean",
        "key": "customer_id",
        "columns": ("customer_id", "full_name", "email", "country",
                    "signup_date", "tier", "phone"),
    },
    "orders": {
        "table": "orders_clean",
        "key": "order_id",
        "columns": ("order_id", "customer_id", "order_ts", "status",
                    "quantity", "unit_price", "currency", "coupon_code"),
    },
}
REFERENCE_TABLES = {"customers.customer_id": ("customers_clean", "customer_id")}
CHUNK = 500  # keys per "WHERE key IN (...)" query, stays under SQLite's variable limit


class PipelineConfigError(Exception):
    """The requested dataset or contract cannot be run by this pipeline."""


@dataclass
class PipelineResult:
    run_id: str
    dataset: str
    source_file: str
    status: str
    contract_version: int
    input_rows: int = 0
    inserted_rows: int = 0
    updated_rows: int = 0
    late_rows: int = 0
    unchanged_rows: int = 0
    rejected_rows: int = 0
    not_processed_rows: int = 0
    failure_reason: str | None = None
    issues: list = field(default_factory=list)
    reject_reasons: dict = field(default_factory=dict)

    @property
    def counts(self) -> dict:
        return {name: getattr(self, name) for name in repository.COUNT_FIELDS}

    @property
    def reconciles(self) -> bool:
        """Every input row must end in exactly one outcome."""
        accounted = (self.inserted_rows + self.updated_rows + self.late_rows
                     + self.unchanged_rows + self.rejected_rows + self.not_processed_rows)
        return self.input_rows == accounted


# ---- small SQL helpers ------------------------------------------------------

def _insert_sql(table, columns) -> str:
    names = ", ".join(columns) + ", source_file, first_run_id, last_run_id, loaded_at"
    marks = ", ".join("?" * (len(columns) + 4))
    return f"INSERT INTO {table} ({names}) VALUES ({marks})"


def _update_sql(table, key, columns) -> str:
    non_key = [c for c in columns if c != key]
    assignments = ", ".join(f"{c} = ?" for c in non_key)
    return (f"UPDATE {table} SET {assignments}, source_file = ?, last_run_id = ?, "
            f"loaded_at = ? WHERE {key} = ?")


def _update_params(values, key, columns, source_file, run_id, now) -> list:
    non_key = [c for c in columns if c != key]
    return [values[c] for c in non_key] + [source_file, run_id, now, values[key]]


def _fetch_existing(conn, target, keys) -> dict:
    """Return {key: {column: value}} for the keys already in the target table."""
    columns, key, table = target["columns"], target["key"], target["table"]
    found = {}
    for start in range(0, len(keys), CHUNK):
        chunk = keys[start:start + CHUNK]
        marks = ",".join("?" * len(chunk))
        sql = f"SELECT {', '.join(columns)} FROM {table} WHERE {key} IN ({marks})"
        for row in conn.execute(sql, chunk):
            found[row[key]] = {c: row[c] for c in columns}
    return found


def _same(old, new, columns) -> bool:
    return all(old[c] == new[c] for c in columns)


def _zero() -> dict:
    return {"inserted_rows": 0, "updated_rows": 0, "late_rows": 0, "unchanged_rows": 0}


def _history_id(values) -> str:
    """Deterministic ID for one version of an order, so reruns cannot duplicate it."""
    parts = ["" if values[c] is None else str(values[c]) for c in TARGETS["orders"]["columns"]]
    return "h_" + hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


# ---- load step --------------------------------------------------------------

def _apply_customers(conn, run_id, source_file, accepted) -> dict:
    target = TARGETS["customers"]
    columns, key, table = target["columns"], target["key"], target["table"]
    existing = _fetch_existing(conn, target, [r.values[key] for r in accepted])
    now = utc_now_iso()
    counts, inserts, updates = _zero(), [], []
    for row in accepted:
        v = row.values
        old = existing.get(v[key])
        if old is None:
            inserts.append([v[c] for c in columns] + [source_file, run_id, run_id, now])
            counts["inserted_rows"] += 1
        elif _same(old, v, columns):
            counts["unchanged_rows"] += 1
        else:
            updates.append(_update_params(v, key, columns, source_file, run_id, now))
            counts["updated_rows"] += 1
    conn.executemany(_insert_sql(table, columns), inserts)
    conn.executemany(_update_sql(table, key, columns), updates)
    return counts


def _apply_orders(conn, run_id, source_file, accepted) -> dict:
    target = TARGETS["orders"]
    columns, key, table = target["columns"], target["key"], target["table"]
    existing = _fetch_existing(conn, target, [r.values[key] for r in accepted])
    now = utc_now_iso()
    counts, inserts, updates, history = _zero(), [], [], []

    def history_row(v, is_late):
        return [_history_id(v), v["order_id"], v["customer_id"], v["order_ts"], v["status"],
                v["quantity"], v["unit_price"], v["currency"], v["coupon_code"],
                is_late, run_id, source_file, now]

    for row in accepted:
        v = row.values
        old = existing.get(v[key])
        if old is None:
            inserts.append([v[c] for c in columns] + [source_file, run_id, run_id, now])
            history.append(history_row(v, 0))
            counts["inserted_rows"] += 1
        elif _same(old, v, columns):
            counts["unchanged_rows"] += 1
        elif v["order_ts"] >= old["order_ts"]:
            updates.append(_update_params(v, key, columns, source_file, run_id, now))
            history.append(history_row(v, 0))
            counts["updated_rows"] += 1
        else:  # older than what we already hold: late arrival, history only
            seen = conn.execute(
                "SELECT 1 FROM order_history WHERE history_id = ?", (_history_id(v),)
            ).fetchone()
            if seen:
                counts["unchanged_rows"] += 1
            else:
                history.append(history_row(v, 1))
                counts["late_rows"] += 1

    conn.executemany(_insert_sql(table, columns), inserts)
    conn.executemany(_update_sql(table, key, columns), updates)
    conn.executemany(
        "INSERT OR IGNORE INTO order_history (history_id, order_id, customer_id, order_ts, "
        "status, quantity, unit_price, currency, coupon_code, is_late_arrival, run_id, "
        "source_file, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        history,
    )
    return counts


def _apply(conn, dataset, run_id, source_file, accepted) -> dict:
    if dataset == "customers":
        return _apply_customers(conn, run_id, source_file, accepted)
    return _apply_orders(conn, run_id, source_file, accepted)


def _reference_keys(conn, contract) -> dict:
    keys: dict = {}
    for col in contract.columns:
        if not col.references or col.references in keys:
            continue
        if col.references not in REFERENCE_TABLES:
            raise PipelineConfigError(f"no reference table configured for '{col.references}'")
        table, column = REFERENCE_TABLES[col.references]
        keys[col.references] = {r[0] for r in conn.execute(f"SELECT {column} FROM {table}")}
    return keys


def _process(conn, result, contract, read, batch) -> None:
    """Check references, quarantine rejects and load accepted rows (caller commits)."""
    batch = check_references(batch, contract, _reference_keys(conn, contract))

    items = []
    for bad in read.malformed:
        items.append({"row_number": bad.line_number, "reason_codes": ["MALFORMED_ROW"],
                      "reason_detail": bad.reason, "raw": {"_fields": bad.raw}})
    for rej in batch.rejected:
        items.append({
            "row_number": read.row_numbers[rej.row_number - 1],  # real file line number
            "reason_codes": rej.codes,
            "reason_detail": "; ".join(f"{r.column or 'row'}: {r.detail}" for r in rej.reasons),
            "raw": rej.raw,
        })
    reasons = batch.reason_counts()
    if read.malformed:
        reasons["MALFORMED_ROW"] = len(read.malformed)

    if items:
        repository.add_quarantine(
            conn, result.run_id, result.dataset, result.source_file, read.sha256, items
        )
    counts = _apply(conn, result.dataset, result.run_id, result.source_file, batch.accepted)

    result.inserted_rows = counts["inserted_rows"]
    result.updated_rows = counts["updated_rows"]
    result.late_rows = counts["late_rows"]
    result.unchanged_rows = counts["unchanged_rows"]
    result.rejected_rows = len(items)
    result.reject_reasons = dict(sorted(reasons.items()))
    result.status = "success"


# ---- public entry point -----------------------------------------------------

def run_pipeline(conn, contracts_dir, dataset, file_path, contract_version=1) -> PipelineResult:
    """Process one producer CSV file. Safe to rerun; every run is logged."""
    target = TARGETS.get(dataset)
    if target is None:
        raise PipelineConfigError(f"unknown dataset '{dataset}'; expected one of {sorted(TARGETS)}")

    contract_path = Path(contracts_dir) / f"{dataset}_v{contract_version}.yaml"
    contract = load_contract(contract_path)
    extra = sorted(set(contract.column_names) - set(target["columns"]))
    missing = sorted(set(target["columns"]) - set(contract.column_names))
    if extra or missing:
        raise PipelineConfigError(
            f"{dataset} contract v{contract_version} does not match the target table "
            f"(extra columns: {extra}, missing columns: {missing}); a contract that changes "
            "the schema needs a new target table before it can be loaded"
        )
    repository.register_contract(conn, contract, contract_path)

    file_name = Path(file_path).name
    try:
        read = read_csv_file(file_path)
    except SourceFileError as exc:
        run_id = repository.start_run(conn, dataset, contract.version, file_name, "unreadable")
        repository.finish_run(conn, run_id, "failed", failure_reason=str(exc))
        log.error("run %s failed: %s", run_id, exc)
        return PipelineResult(run_id, dataset, file_name, "failed", contract.version,
                              failure_reason=str(exc))

    total = read.total_records
    run_id = repository.start_run(conn, dataset, contract.version, read.file_name, read.sha256)
    result = PipelineResult(run_id, dataset, read.file_name, "running", contract.version,
                            input_rows=total)
    log.info("run %s started: dataset=%s file=%s rows=%d contract=v%d",
             run_id, dataset, read.file_name, total, contract.version)

    try:
        earlier = repository.find_loaded_run(conn, dataset, read.sha256, contract.version)
        if earlier is not None:
            result.status = "skipped_already_loaded"
            result.not_processed_rows = total
            result.failure_reason = f"same file content was already loaded by {earlier['run_id']}"
        elif not read.header:
            result.status = "empty"
        else:
            batch = validate_batch(contract, read.header, read.rows)
            result.issues = list(batch.issues)
            if batch.status == "schema_mismatch":
                result.status = "rejected_schema"
                result.not_processed_rows = total
                result.failure_reason = "; ".join(
                    i.detail for i in batch.issues if i.severity == "error"
                )
            elif total == 0:
                result.status = "empty"
            else:
                _process(conn, result, contract, read, batch)
        # finish_run commits the data, quarantine and run log together
        repository.finish_run(conn, run_id, result.status, result.counts, result.failure_reason)
    except Exception as exc:  # roll back everything, then record the failure
        conn.rollback()
        reason = f"{type(exc).__name__}: {exc}"
        log.exception("run %s failed", run_id)
        result.status = "failed"
        result.failure_reason = reason
        result.inserted_rows = result.updated_rows = result.late_rows = 0
        result.unchanged_rows = result.rejected_rows = 0
        result.not_processed_rows = total
        result.reject_reasons = {}
        repository.finish_run(conn, run_id, "failed", result.counts, reason)
        return result

    log.info("run %s finished: status=%s inserted=%d updated=%d late=%d unchanged=%d "
             "rejected=%d not_processed=%d", run_id, result.status, result.inserted_rows,
             result.updated_rows, result.late_rows, result.unchanged_rows,
             result.rejected_rows, result.not_processed_rows)
    return result
