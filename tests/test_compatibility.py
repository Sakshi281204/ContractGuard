from pathlib import Path

import pytest

from contractguard.application.compatibility import (
    BREAKING,
    NON_BREAKING,
    compare_contracts,
    compare_files,
)
from contractguard.application.contract_loader import Column, Contract

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"


def col(name, kind="string", nullable=False, **extra):
    return Column(name=name, type=kind, nullable=nullable, **extra)


def make(version, *columns, pk=("id",), name="demo"):
    return Contract(name=name, version=version, description="",
                    primary_key=tuple(pk), columns=tuple(columns))


ID = col("id")


def diff(old_cols, new_cols):
    return compare_contracts(make(1, ID, *old_cols), make(2, ID, *new_cols))


def pairs(report):
    return {(c.severity, c.code, c.column) for c in report.changes}


def test_identical_contracts_have_no_changes():
    report = diff([col("a")], [col("a")])
    assert report.changes == [] and report.verdict == "NO_CHANGE" and not report.is_breaking


def test_removed_column_is_breaking():
    assert pairs(diff([col("a")], [])) == {(BREAKING, "COLUMN_REMOVED", "a")}


def test_added_required_column_is_breaking():
    assert pairs(diff([], [col("a")])) == {(BREAKING, "COLUMN_ADDED_REQUIRED", "a")}


def test_added_optional_column_is_non_breaking():
    report = diff([], [col("a", nullable=True)])
    assert pairs(report) == {(NON_BREAKING, "COLUMN_ADDED_OPTIONAL", "a")}
    assert report.verdict == "NON_BREAKING"


def test_integer_to_float_is_non_breaking():
    report = diff([col("n", "integer")], [col("n", "float")])
    assert pairs(report) == {(NON_BREAKING, "TYPE_WIDENED", "n")}


def test_float_to_integer_is_breaking():
    report = diff([col("n", "float")], [col("n", "integer")])
    assert pairs(report) == {(BREAKING, "TYPE_CHANGED", "n")}


def test_string_to_integer_is_breaking():
    report = diff([col("n", "string")], [col("n", "integer")])
    assert pairs(report) == {(BREAKING, "TYPE_CHANGED", "n")}


def test_nullable_to_required_is_breaking():
    report = diff([col("a", nullable=True)], [col("a", nullable=False)])
    assert pairs(report) == {(BREAKING, "NULLABLE_TO_REQUIRED", "a")}


def test_required_to_nullable_is_breaking():
    report = diff([col("a", nullable=False)], [col("a", nullable=True)])
    assert pairs(report) == {(BREAKING, "REQUIRED_TO_NULLABLE", "a")}


def test_allowed_value_removed_is_breaking():
    report = diff([col("c", allowed_values=("INR", "USD"))], [col("c", allowed_values=("INR",))])
    assert pairs(report) == {(BREAKING, "ALLOWED_VALUE_REMOVED", "c")}


def test_allowed_value_added_is_non_breaking():
    report = diff([col("c", allowed_values=("INR",))], [col("c", allowed_values=("INR", "USD"))])
    assert pairs(report) == {(NON_BREAKING, "ALLOWED_VALUE_ADDED", "c")}


def test_new_allowed_values_restriction_is_breaking():
    report = diff([col("c")], [col("c", allowed_values=("a", "b"))])
    assert pairs(report) == {(BREAKING, "CONSTRAINT_ADDED", "c")}


def test_dropping_allowed_values_restriction_is_non_breaking():
    report = diff([col("c", allowed_values=("a", "b"))], [col("c")])
    assert pairs(report) == {(NON_BREAKING, "CONSTRAINT_RELAXED", "c")}


def test_max_narrowed_is_breaking():
    report = diff([col("q", "integer", max=1000)], [col("q", "integer", max=500)])
    assert pairs(report) == {(BREAKING, "RANGE_NARROWED", "q")}


def test_max_widened_is_non_breaking():
    report = diff([col("q", "integer", max=1000)], [col("q", "integer", max=5000)])
    assert pairs(report) == {(NON_BREAKING, "RANGE_WIDENED", "q")}


def test_min_raised_is_breaking():
    report = diff([col("q", "integer", min=1)], [col("q", "integer", min=10)])
    assert pairs(report) == {(BREAKING, "RANGE_NARROWED", "q")}


def test_min_lowered_is_non_breaking():
    report = diff([col("q", "integer", min=10)], [col("q", "integer", min=1)])
    assert pairs(report) == {(NON_BREAKING, "RANGE_WIDENED", "q")}


def test_new_bound_is_breaking_and_removed_bound_is_non_breaking():
    added = diff([col("q", "integer")], [col("q", "integer", min=1)])
    removed = diff([col("q", "integer", min=1)], [col("q", "integer")])
    assert pairs(added) == {(BREAKING, "RANGE_NARROWED", "q")}
    assert pairs(removed) == {(NON_BREAKING, "RANGE_WIDENED", "q")}


def test_pattern_changed_is_breaking():
    report = diff([col("p", pattern="^A$")], [col("p", pattern="^B$")])
    assert pairs(report) == {(BREAKING, "PATTERN_CHANGED", "p")}


def test_pattern_removed_is_non_breaking():
    report = diff([col("p", pattern="^A$")], [col("p")])
    assert pairs(report) == {(NON_BREAKING, "PATTERN_REMOVED", "p")}


def test_reference_added_is_breaking():
    report = diff([col("r")], [col("r", references="customers.customer_id")])
    assert pairs(report) == {(BREAKING, "REFERENCE_ADDED", "r")}


def test_primary_key_change_is_breaking():
    other = col("other")
    report = compare_contracts(make(1, ID, other, pk=("id",)),
                               make(2, ID, other, pk=("id", "other")))
    assert pairs(report) == {(BREAKING, "PRIMARY_KEY_CHANGED", None)}


def test_column_reorder_is_not_a_change():
    a, b = col("a"), col("b")
    report = compare_contracts(make(1, ID, a, b), make(2, ID, b, a))
    assert report.changes == []


def test_rename_shows_as_removed_plus_added():
    report = diff([col("phone", nullable=True)], [col("mobile", nullable=True)])
    assert pairs(report) == {(BREAKING, "COLUMN_REMOVED", "phone"),
                             (NON_BREAKING, "COLUMN_ADDED_OPTIONAL", "mobile")}


def test_different_contract_names_are_rejected():
    with pytest.raises(ValueError, match="different contracts"):
        compare_contracts(make(1, ID, name="orders"), make(2, ID, name="customers"))


def test_new_version_must_be_higher():
    with pytest.raises(ValueError, match="higher"):
        compare_contracts(make(2, ID), make(2, ID))


def test_real_orders_v1_to_v2():
    report = compare_files(CONTRACTS / "orders_v1.yaml", CONTRACTS / "orders_v2.yaml")
    assert pairs(report) == {
        (BREAKING, "COLUMN_REMOVED", "unit_price"),
        (BREAKING, "COLUMN_ADDED_REQUIRED", "price_inr"),
        (BREAKING, "ALLOWED_VALUE_REMOVED", "currency"),
        (NON_BREAKING, "ALLOWED_VALUE_ADDED", "status"),
        (NON_BREAKING, "RANGE_WIDENED", "quantity"),
        (NON_BREAKING, "COLUMN_ADDED_OPTIONAL", "channel"),
    }
    assert len(report.changes) == 6
    assert report.verdict == BREAKING and report.is_breaking
    assert (report.old_version, report.new_version) == (1, 2)


def test_compare_files_reads_yaml_from_disk():
    report = compare_files(CONTRACTS / "orders_v1.yaml", CONTRACTS / "orders_v2.yaml")
    assert report.contract_name == "orders" and report.is_breaking


def test_report_text_and_dict():
    report = compare_files(CONTRACTS / "orders_v1.yaml", CONTRACTS / "orders_v2.yaml")
    text = report.to_text()
    assert "orders v1 -> v2" in text and "Verdict: BREAKING" in text
    assert "[COLUMN_REMOVED] unit_price" in text and "NON-BREAKING (3)" in text
    data = report.to_dict()
    assert data["breaking_count"] == 3 and data["non_breaking_count"] == 3
    assert data["verdict"] == BREAKING and len(data["changes"]) == 6
    assert set(data["changes"][0]) == {"severity", "code", "column", "detail"}


def test_breaking_changes_are_listed_first():
    report = compare_files(CONTRACTS / "orders_v1.yaml", CONTRACTS / "orders_v2.yaml")
    severities = [c.severity for c in report.changes]
    assert severities == sorted(severities, key=lambda s: 0 if s == BREAKING else 1)
