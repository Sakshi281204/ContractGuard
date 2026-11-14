"""Tier 2 (application): validate one producer batch against a data contract."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from contractguard.application.contract_loader import Column, Contract

_INT = re.compile(r"^[+-]?[0-9]+$")
_FLOAT = re.compile(r"^[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?$")
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]{1,6})?)?"
    r"(Z|[+-][0-9]{2}:[0-9]{2})?$"
)


@dataclass(frozen=True)
class Reason:
    code: str
    column: str | None
    detail: str


@dataclass(frozen=True)
class SchemaIssue:
    severity: str  # "error" or "warning"
    code: str
    detail: str


@dataclass
class AcceptedRow:
    row_number: int
    values: dict


@dataclass
class RejectedRow:
    row_number: int
    raw: dict
    reasons: list

    @property
    def codes(self) -> list:
        return [r.code for r in self.reasons]

    @property
    def summary(self) -> str:
        parts = []
        for r in self.reasons:
            parts.append(f"{r.code}[{r.column}]" if r.column else r.code)
        return "; ".join(parts)


@dataclass
class BatchResult:
    status: str  # "ok", "empty" or "schema_mismatch"
    issues: list
    accepted: list
    rejected: list
    input_rows: int
    rows_not_processed: int = 0

    @property
    def accepted_count(self) -> int:
        return len(self.accepted)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)

    def reason_counts(self) -> dict:
        counts: dict = {}
        for row in self.rejected:
            for code in row.codes:
                counts[code] = counts.get(code, 0) + 1
        return dict(sorted(counts.items()))


def _parse(column: Column, text: str):
    """Return (typed_value, error_detail). Exactly one of them is None."""
    kind = column.type
    if kind == "string":
        return text, None
    if kind == "integer":
        if not _INT.match(text):
            return None, f"'{text}' is not an integer"
        return int(text), None
    if kind == "float":
        if not _FLOAT.match(text):
            return None, f"'{text}' is not a number"
        value = float(text)
        if not math.isfinite(value):
            return None, f"'{text}' is not a finite number"
        return value, None
    if kind == "date":
        if not _DATE.match(text):
            return None, f"'{text}' is not an ISO date (YYYY-MM-DD)"
        try:
            date.fromisoformat(text)
        except ValueError:
            return None, f"'{text}' is not a real calendar date"
        return text, None
    # timestamp
    if not _TIMESTAMP.match(text):
        return None, f"'{text}' is not an ISO-8601 timestamp"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None, f"'{text}' is not a real timestamp"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), None


def _check_row(contract: Contract, row: dict, patterns: dict):
    values: dict = {}
    reasons: list = []
    for col in contract.columns:
        raw = row.get(col.name)
        text = "" if raw is None else str(raw).strip()
        if text == "":
            values[col.name] = None
            if not col.nullable:
                reasons.append(Reason("NULL_REQUIRED", col.name, "required value is empty"))
            continue
        value, error = _parse(col, text)
        if error:
            values[col.name] = None
            reasons.append(Reason("BAD_TYPE", col.name, error))
            continue
        if col.allowed_values is not None and text not in col.allowed_values:
            reasons.append(Reason(
                "NOT_ALLOWED", col.name,
                f"'{text}' is not one of {list(col.allowed_values)}",
            ))
        if isinstance(value, (int, float)):
            if col.min is not None and value < col.min:
                reasons.append(Reason("OUT_OF_RANGE", col.name, f"{value} is below minimum {col.min}"))
            if col.max is not None and value > col.max:
                reasons.append(Reason("OUT_OF_RANGE", col.name, f"{value} is above maximum {col.max}"))
        if col.pattern is not None and not patterns[col.name].search(text):
            reasons.append(Reason("BAD_PATTERN", col.name, f"'{text}' does not match {col.pattern}"))
        values[col.name] = value
    return values, reasons


def validate_batch(contract: Contract, header: list, rows: list) -> BatchResult:
    """Validate raw rows (dicts of text values) against a contract."""
    issues: list = []
    header_set = set(header)
    fatal = False

    for col in contract.columns:
        if col.name in header_set:
            continue
        if col.nullable:
            issues.append(SchemaIssue(
                "warning", "MISSING_OPTIONAL_COLUMN",
                f"optional column '{col.name}' is absent; values treated as null",
            ))
        else:
            fatal = True
            issues.append(SchemaIssue(
                "error", "MISSING_COLUMN", f"required column '{col.name}' is absent",
            ))
    for name in header:
        if name not in contract.column_names:
            issues.append(SchemaIssue(
                "warning", "UNEXPECTED_COLUMN",
                f"column '{name}' is not in the contract and was ignored",
            ))

    if fatal:
        return BatchResult("schema_mismatch", issues, [], [], len(rows), len(rows))
    if not rows:
        return BatchResult("empty", issues, [], [], 0)

    patterns = {c.name: re.compile(c.pattern) for c in contract.columns if c.pattern}
    accepted: list = []
    rejected: list = []
    seen: dict = {}  # primary key tuple -> values of the first accepted row

    for number, row in enumerate(rows, start=1):
        values, reasons = _check_row(contract, row, patterns)
        if not reasons:
            key = tuple(values[k] for k in contract.primary_key)
            if key in seen:
                code = "DUPLICATE_EXACT" if seen[key] == values else "DUPLICATE_KEY_CONFLICT"
                reasons.append(Reason(code, None, f"key {key} already accepted in this batch"))
            else:
                seen[key] = values
        if reasons:
            rejected.append(RejectedRow(number, dict(row), reasons))
        else:
            accepted.append(AcceptedRow(number, values))

    return BatchResult("ok", issues, accepted, rejected, len(rows))


def check_references(result: BatchResult, contract: Contract, reference_keys: dict) -> BatchResult:
    """Move accepted rows with an unknown foreign key into the rejected list."""
    ref_columns = [c for c in contract.columns if c.references]
    for col in ref_columns:
        if col.references not in reference_keys:
            raise ValueError(f"no reference keys supplied for '{col.references}'")

    kept: list = []
    for row in result.accepted:
        reasons = []
        for col in ref_columns:
            value = row.values.get(col.name)
            if value is not None and value not in reference_keys[col.references]:
                reasons.append(Reason(
                    "INVALID_REFERENCE", col.name,
                    f"'{value}' not found in {col.references}",
                ))
        if reasons:
            raw = {k: ("" if v is None else str(v)) for k, v in row.values.items()}
            result.rejected.append(RejectedRow(row.row_number, raw, reasons))
        else:
            kept.append(row)
    result.accepted = kept
    result.rejected.sort(key=lambda r: r.row_number)
    return result
