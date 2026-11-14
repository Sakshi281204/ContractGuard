"""Tier 2 (application): load and validate data contract YAML files."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

VALID_TYPES = ("string", "integer", "float", "date", "timestamp")

_REQUIRED_TOP_KEYS = {"contract_name", "version", "primary_key", "columns"}
_TOP_KEYS = _REQUIRED_TOP_KEYS | {"description"}
_COLUMN_KEYS = {
    "name", "type", "nullable", "allowed_values", "min", "max", "pattern", "references",
}
_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]*$")
_REFERENCE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
_FILE_NAME = re.compile(r"^(?P<name>.+)_v(?P<version>\d+)\.ya?ml$")


class ContractError(Exception):
    """Raised when a contract file is missing, unreadable or invalid."""


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    nullable: bool
    allowed_values: tuple[str, ...] | None = None
    min: float | None = None
    max: float | None = None
    pattern: str | None = None
    references: str | None = None


@dataclass(frozen=True)
class Contract:
    name: str
    version: int
    description: str
    primary_key: tuple[str, ...]
    columns: tuple[Column, ...]

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)

    def get_column(self, name: str) -> Column:
        for column in self.columns:
            if column.name == name:
                return column
        raise KeyError(name)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _parse_column(raw, index: int, src: str) -> Column:
    where = f"{src}: column #{index + 1}"
    if not isinstance(raw, dict):
        raise ContractError(f"{where} must be a mapping")
    unknown = set(raw) - _COLUMN_KEYS
    if unknown:
        raise ContractError(f"{where} has unknown key(s): {sorted(unknown)}")
    name = raw.get("name")
    if not isinstance(name, str) or not _SNAKE_CASE.match(name):
        raise ContractError(f"{where} needs a snake_case 'name' (got {name!r})")
    where = f"{src}: column '{name}'"
    col_type = raw.get("type")
    if col_type not in VALID_TYPES:
        raise ContractError(
            f"{where} has unsupported type {col_type!r}; allowed: {list(VALID_TYPES)}"
        )
    nullable = raw.get("nullable")
    if not isinstance(nullable, bool):
        raise ContractError(f"{where} needs 'nullable' set to true or false")

    allowed = raw.get("allowed_values")
    if allowed is not None:
        if not isinstance(allowed, list) or not allowed:
            raise ContractError(f"{where}: allowed_values must be a non-empty list")
        allowed = tuple(str(v) for v in allowed)

    for bound in ("min", "max"):
        if raw.get(bound) is not None and not _is_number(raw[bound]):
            raise ContractError(f"{where}: {bound} must be a number")
    low, high = raw.get("min"), raw.get("max")
    if low is not None and high is not None and low > high:
        raise ContractError(f"{where}: min is greater than max")

    pattern = raw.get("pattern")
    if pattern is not None:
        try:
            re.compile(pattern)
        except (re.error, TypeError) as exc:
            raise ContractError(f"{where}: invalid pattern ({exc})") from exc

    references = raw.get("references")
    if references is not None and (
        not isinstance(references, str) or not _REFERENCE.match(references)
    ):
        raise ContractError(f"{where}: references must look like 'table.column'")

    return Column(
        name=name, type=col_type, nullable=nullable, allowed_values=allowed,
        min=low, max=high, pattern=pattern, references=references,
    )


def load_contract(path) -> Contract:
    """Read one contract YAML file and return a validated Contract."""
    path = Path(path)
    src = path.name
    if not path.is_file():
        raise ContractError(f"contract file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ContractError(f"{src}: invalid YAML ({exc})") from exc
    if not isinstance(raw, dict):
        raise ContractError(f"{src}: top level must be a mapping")

    missing = _REQUIRED_TOP_KEYS - raw.keys()
    if missing:
        raise ContractError(f"{src}: missing required key(s): {sorted(missing)}")
    unknown = set(raw) - _TOP_KEYS
    if unknown:
        raise ContractError(f"{src}: unknown key(s): {sorted(unknown)}")

    version = raw["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ContractError(f"{src}: version must be a whole number >= 1")
    if not isinstance(raw["contract_name"], str) or not raw["contract_name"]:
        raise ContractError(f"{src}: contract_name must be a non-empty string")

    columns_raw = raw["columns"]
    if not isinstance(columns_raw, list) or not columns_raw:
        raise ContractError(f"{src}: needs at least one column")
    columns = tuple(_parse_column(c, i, src) for i, c in enumerate(columns_raw))

    names = [c.name for c in columns]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ContractError(f"{src}: duplicate column name(s): {duplicates}")

    key = raw["primary_key"]
    if not isinstance(key, list) or not key or not all(isinstance(k, str) for k in key):
        raise ContractError(f"{src}: primary_key must be a non-empty list of column names")
    by_name = {c.name: c for c in columns}
    for key_column in key:
        if key_column not in by_name:
            raise ContractError(
                f"{src}: primary key column '{key_column}' is not defined in columns"
            )
        if by_name[key_column].nullable:
            raise ContractError(
                f"{src}: primary key column '{key_column}' must not be nullable"
            )

    return Contract(
        name=raw["contract_name"],
        version=version,
        description=str(raw.get("description", "")),
        primary_key=tuple(key),
        columns=columns,
    )


def load_latest_contract(contracts_dir, name: str) -> Contract:
    """Load the highest-numbered version of <name>_v<N>.yaml in a folder."""
    candidates = []
    for file in Path(contracts_dir).glob(f"{name}_v*.y*ml"):
        match = _FILE_NAME.match(file.name)
        if match and match.group("name") == name:
            candidates.append((int(match.group("version")), file))
    if not candidates:
        raise ContractError(f"no contract versions found for '{name}' in {contracts_dir}")
    return load_contract(max(candidates)[1])
