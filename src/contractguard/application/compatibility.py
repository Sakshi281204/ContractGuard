"""Tier 2 (application): compare two versions of a contract (breaking vs non-breaking)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from contractguard.application.contract_loader import Contract, load_contract

BREAKING = "BREAKING"
NON_BREAKING = "NON_BREAKING"


@dataclass(frozen=True)
class Change:
    severity: str
    code: str
    column: str | None
    detail: str


@dataclass
class CompatibilityReport:
    contract_name: str
    old_version: int
    new_version: int
    changes: list = field(default_factory=list)

    @property
    def breaking(self) -> list:
        return [c for c in self.changes if c.severity == BREAKING]

    @property
    def non_breaking(self) -> list:
        return [c for c in self.changes if c.severity == NON_BREAKING]

    @property
    def is_breaking(self) -> bool:
        return bool(self.breaking)

    @property
    def verdict(self) -> str:
        if self.breaking:
            return BREAKING
        if self.non_breaking:
            return NON_BREAKING
        return "NO_CHANGE"

    def to_dict(self) -> dict:
        return {
            "contract": self.contract_name,
            "old_version": self.old_version,
            "new_version": self.new_version,
            "verdict": self.verdict,
            "breaking_count": len(self.breaking),
            "non_breaking_count": len(self.non_breaking),
            "changes": [
                {"severity": c.severity, "code": c.code, "column": c.column, "detail": c.detail}
                for c in self.changes
            ],
        }

    def to_text(self) -> str:
        lines = [
            f"Contract compatibility report: {self.contract_name} "
            f"v{self.old_version} -> v{self.new_version}",
            f"Verdict: {self.verdict} ({len(self.breaking)} breaking, "
            f"{len(self.non_breaking)} non-breaking)",
        ]
        for title, items in (("BREAKING", self.breaking), ("NON-BREAKING", self.non_breaking)):
            if items:
                lines.append("")
                lines.append(f"{title} ({len(items)})")
                for c in items:
                    where = c.column if c.column else "(contract)"
                    lines.append(f"  [{c.code}] {where} - {c.detail}")
        if not self.changes:
            lines.append("")
            lines.append("No differences found.")
        return "\n".join(lines)


def _fmt(value) -> str:
    return "none" if value is None else str(value)


def _compare_column(old, new) -> list:
    out: list = []
    name = new.name

    if old.type != new.type:
        if (old.type, new.type) == ("integer", "float"):
            out.append(Change(NON_BREAKING, "TYPE_WIDENED", name,
                              "type widened from integer to float"))
        else:
            out.append(Change(BREAKING, "TYPE_CHANGED", name,
                              f"type changed from {old.type} to {new.type}"))

    if old.nullable != new.nullable:
        if old.nullable:
            out.append(Change(BREAKING, "NULLABLE_TO_REQUIRED", name,
                              "column was optional and is now required; "
                              "producers that send nulls will fail"))
        else:
            out.append(Change(BREAKING, "REQUIRED_TO_NULLABLE", name,
                              "column was required and may now be null; "
                              "consumers that assume a value is always present may break"))

    old_set = None if old.allowed_values is None else set(old.allowed_values)
    new_set = None if new.allowed_values is None else set(new.allowed_values)
    if old_set is None and new_set is not None:
        out.append(Change(BREAKING, "CONSTRAINT_ADDED", name,
                          f"allowed values now restricted to {sorted(new_set)}"))
    elif old_set is not None and new_set is None:
        out.append(Change(NON_BREAKING, "CONSTRAINT_RELAXED", name,
                          "allowed-values restriction was removed"))
    elif old_set is not None and new_set is not None:
        for value in sorted(old_set - new_set):
            out.append(Change(BREAKING, "ALLOWED_VALUE_REMOVED", name,
                              f"value '{value}' is no longer allowed"))
        for value in sorted(new_set - old_set):
            out.append(Change(NON_BREAKING, "ALLOWED_VALUE_ADDED", name,
                              f"value '{value}' is now allowed"))

    if old.min != new.min:
        narrowed = new.min is not None and (old.min is None or new.min > old.min)
        out.append(_range_change(name, "minimum", old.min, new.min, narrowed))
    if old.max != new.max:
        narrowed = new.max is not None and (old.max is None or new.max < old.max)
        out.append(_range_change(name, "maximum", old.max, new.max, narrowed))

    if old.pattern != new.pattern:
        if old.pattern is None:
            out.append(Change(BREAKING, "PATTERN_ADDED", name,
                              f"format pattern added: {new.pattern}"))
        elif new.pattern is None:
            out.append(Change(NON_BREAKING, "PATTERN_REMOVED", name,
                              "format pattern was removed"))
        else:
            out.append(Change(BREAKING, "PATTERN_CHANGED", name,
                              f"format pattern changed from {old.pattern} to {new.pattern}"))

    if old.references != new.references:
        if old.references is None:
            out.append(Change(BREAKING, "REFERENCE_ADDED", name,
                              f"now must exist in {new.references}"))
        elif new.references is None:
            out.append(Change(NON_BREAKING, "REFERENCE_REMOVED", name,
                              "reference requirement was removed"))
        else:
            out.append(Change(BREAKING, "REFERENCE_CHANGED", name,
                              f"reference changed from {old.references} to {new.references}"))
    return out


def _range_change(name, bound, old, new, narrowed) -> Change:
    text = f"{bound} changed from {_fmt(old)} to {_fmt(new)}"
    if narrowed:
        return Change(BREAKING, "RANGE_NARROWED", name, text + " (range is stricter)")
    return Change(NON_BREAKING, "RANGE_WIDENED", name, text + " (range is looser)")


def compare_contracts(old: Contract, new: Contract) -> CompatibilityReport:
    """Compare two versions of the same contract."""
    if old.name != new.name:
        raise ValueError(
            f"cannot compare different contracts: '{old.name}' and '{new.name}'"
        )
    if new.version <= old.version:
        raise ValueError(
            f"new version (v{new.version}) must be higher than old version (v{old.version})"
        )

    changes: list = []
    old_cols = {c.name: c for c in old.columns}
    new_cols = {c.name: c for c in new.columns}

    for col in old.columns:
        if col.name not in new_cols:
            changes.append(Change(BREAKING, "COLUMN_REMOVED", col.name,
                                  "column was removed; consumers reading it will fail"))
    for col in new.columns:
        if col.name not in old_cols:
            if col.nullable:
                changes.append(Change(NON_BREAKING, "COLUMN_ADDED_OPTIONAL", col.name,
                                      "new optional column; existing producers are unaffected"))
            else:
                changes.append(Change(BREAKING, "COLUMN_ADDED_REQUIRED", col.name,
                                      "new required column; existing producers will fail"))
    for col in new.columns:
        if col.name in old_cols:
            changes.extend(_compare_column(old_cols[col.name], col))

    if old.primary_key != new.primary_key:
        changes.append(Change(BREAKING, "PRIMARY_KEY_CHANGED", None,
                              f"primary key changed from {list(old.primary_key)} "
                              f"to {list(new.primary_key)}"))

    changes.sort(key=lambda c: 0 if c.severity == BREAKING else 1)  # stable sort
    return CompatibilityReport(old.name, old.version, new.version, changes)


def compare_files(old_path, new_path) -> CompatibilityReport:
    return compare_contracts(load_contract(Path(old_path)), load_contract(Path(new_path)))
