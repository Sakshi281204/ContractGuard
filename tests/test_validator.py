from pathlib import Path

import pytest

from contractguard.application.contract_loader import load_contract
from contractguard.application.validator import check_references, validate_batch

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"
ORDERS = load_contract(CONTRACTS / "orders_v1.yaml")
CUSTOMERS = load_contract(CONTRACTS / "customers_v1.yaml")


def order(**overrides):
    row = {
        "order_id": "ORD-00000001",
        "customer_id": "CUST-000001",
        "order_ts": "2026-01-15T10:30:00Z",
        "status": "placed",
        "quantity": "2",
        "unit_price": "499.50",
        "currency": "INR",
        "coupon_code": "",
    }
    row.update(overrides)
    return row


HEADER = list(order().keys())


def run(rows, header=HEADER):
    return validate_batch(ORDERS, header, rows)


def only_code(result):
    assert result.rejected_count == 1
    return result.rejected[0].codes


# ---- happy path and empty input ------------------------------------------

def test_happy_path_returns_typed_values():
    result = run([order()])
    assert result.status == "ok" and result.accepted_count == 1
    values = result.accepted[0].values
    assert values["quantity"] == 2 and isinstance(values["quantity"], int)
    assert values["unit_price"] == 499.5
    assert values["order_ts"] == "2026-01-15T10:30:00Z"
    assert values["coupon_code"] is None


def test_empty_batch_with_header_is_not_a_failure():
    result = run([])
    assert result.status == "empty"
    assert result.input_rows == 0 and result.accepted_count == 0


def test_empty_header_is_a_schema_mismatch():
    result = validate_batch(ORDERS, [], [])
    assert result.status == "schema_mismatch"


# ---- schema changes -------------------------------------------------------

def test_missing_required_column_rejects_whole_batch():
    header = [h for h in HEADER if h != "status"]
    rows = [{k: v for k, v in order().items() if k != "status"}] * 3
    result = validate_batch(ORDERS, header, rows)
    assert result.status == "schema_mismatch"
    assert result.accepted == [] and result.rejected == []
    assert result.rows_not_processed == 3
    assert any(i.code == "MISSING_COLUMN" and i.severity == "error" for i in result.issues)


def test_missing_optional_column_is_a_warning():
    header = [h for h in HEADER if h != "coupon_code"]
    row = {k: v for k, v in order().items() if k != "coupon_code"}
    result = validate_batch(ORDERS, header, [row])
    assert result.status == "ok" and result.accepted_count == 1
    assert result.accepted[0].values["coupon_code"] is None
    assert any(i.code == "MISSING_OPTIONAL_COLUMN" for i in result.issues)


def test_unexpected_column_is_warned_and_ignored():
    result = validate_batch(ORDERS, HEADER + ["channel"], [order(channel="web")])
    assert result.status == "ok" and result.accepted_count == 1
    assert "channel" not in result.accepted[0].values
    assert any(i.code == "UNEXPECTED_COLUMN" for i in result.issues)


# ---- nulls ----------------------------------------------------------------

def test_null_required_value_is_rejected():
    assert only_code(run([order(status="")])) == ["NULL_REQUIRED"]


def test_whitespace_only_counts_as_null():
    assert only_code(run([order(customer_id="   ")])) == ["NULL_REQUIRED"]


def test_optional_null_is_accepted():
    assert run([order(coupon_code="")]).accepted_count == 1


# ---- types ----------------------------------------------------------------

@pytest.mark.parametrize("bad", ["abc", "1.5", "1e3", "\u0661\u0662"])
def test_bad_integer_is_rejected(bad):
    assert only_code(run([order(quantity=bad)])) == ["BAD_TYPE"]


@pytest.mark.parametrize("bad", ["nan", "inf", "1_0", "abc"])
def test_bad_float_is_rejected(bad):
    assert only_code(run([order(unit_price=bad)])) == ["BAD_TYPE"]


@pytest.mark.parametrize("bad", ["yesterday", "2026-13-01T10:00:00", "2026-01-01"])
def test_bad_timestamp_is_rejected(bad):
    assert only_code(run([order(order_ts=bad)])) == ["BAD_TYPE"]


def test_timestamp_with_offset_is_normalized_to_utc():
    result = run([order(order_ts="2026-01-01T05:30:00+05:30")])
    assert result.accepted[0].values["order_ts"] == "2026-01-01T00:00:00Z"


def test_timestamp_without_timezone_is_treated_as_utc():
    result = run([order(order_ts="2026-01-01 08:00:00")])
    assert result.accepted[0].values["order_ts"] == "2026-01-01T08:00:00Z"


# ---- allowed values, ranges, patterns ------------------------------------

@pytest.mark.parametrize("bad", ["unknown", "PLACED"])
def test_value_outside_allowed_list_is_rejected(bad):
    assert only_code(run([order(status=bad)])) == ["NOT_ALLOWED"]


@pytest.mark.parametrize("qty,ok", [("1", True), ("1000", True), ("0", False), ("1001", False)])
def test_quantity_boundaries(qty, ok):
    result = run([order(quantity=qty)])
    assert (result.accepted_count == 1) is ok
    if not ok:
        assert result.rejected[0].codes == ["OUT_OF_RANGE"]


def test_unit_price_below_minimum_is_rejected():
    assert only_code(run([order(unit_price="0")])) == ["OUT_OF_RANGE"]


def test_bad_order_id_pattern_is_rejected():
    assert only_code(run([order(order_id="ORDER-1")])) == ["BAD_PATTERN"]


def test_one_row_can_have_several_reasons():
    result = run([order(status="bogus", quantity="0", order_id="x")])
    assert result.rejected[0].codes == ["BAD_PATTERN", "NOT_ALLOWED", "OUT_OF_RANGE"] or \
        sorted(result.rejected[0].codes) == ["BAD_PATTERN", "NOT_ALLOWED", "OUT_OF_RANGE"]


def test_customer_email_and_country_rules():
    header = ["customer_id", "full_name", "email", "country", "signup_date", "tier", "phone"]
    good = {"customer_id": "CUST-000001", "full_name": "Asha Rao", "email": "asha@example.com",
            "country": "IN", "signup_date": "2025-06-01", "tier": "gold", "phone": ""}
    bad_email = dict(good, customer_id="CUST-000002", email="not-an-email")
    bad_country = dict(good, customer_id="CUST-000003", country="FR")
    result = validate_batch(CUSTOMERS, header, [good, bad_email, bad_country])
    assert result.accepted_count == 1
    assert result.rejected[0].codes == ["BAD_PATTERN"]
    assert result.rejected[1].codes == ["NOT_ALLOWED"]


# ---- duplicates -----------------------------------------------------------

def test_exact_duplicate_is_rejected_and_first_is_kept():
    result = run([order(), order()])
    assert result.accepted_count == 1
    assert result.rejected[0].codes == ["DUPLICATE_EXACT"]
    assert result.rejected[0].row_number == 2


def test_conflicting_duplicate_is_rejected():
    result = run([order(), order(quantity="3")])
    assert result.accepted_count == 1
    assert result.rejected[0].codes == ["DUPLICATE_KEY_CONFLICT"]


def test_invalid_row_does_not_block_later_valid_row_with_same_key():
    result = run([order(status="bogus"), order()])
    assert result.accepted_count == 1 and result.accepted[0].row_number == 2
    assert result.rejected[0].codes == ["NOT_ALLOWED"]


# ---- referential integrity -----------------------------------------------

def test_unknown_customer_is_rejected_as_invalid_reference():
    result = run([order(), order(order_id="ORD-00000002", customer_id="CUST-999999")])
    keys = {"customers.customer_id": {"CUST-000001"}}
    result = check_references(result, ORDERS, keys)
    assert result.accepted_count == 1
    assert result.rejected[0].codes == ["INVALID_REFERENCE"]
    assert result.rejected[0].row_number == 2


def test_missing_reference_key_set_raises():
    with pytest.raises(ValueError, match="no reference keys"):
        check_references(run([order()]), ORDERS, {})


# ---- reconciliation -------------------------------------------------------

def test_counts_reconcile():
    rows = [order(), order(), order(order_id="ORD-00000002", quantity="x"),
            order(order_id="ORD-00000003"), order(order_id="bad")]
    result = run(rows)
    assert result.accepted_count + result.rejected_count + result.rows_not_processed == len(rows)
    assert result.reason_counts() == {"BAD_PATTERN": 1, "BAD_TYPE": 1, "DUPLICATE_EXACT": 1}
