import shutil
from pathlib import Path

import pytest

from contractguard.application import pipeline
from contractguard.application.contract_loader import ContractError
from contractguard.application.pipeline import PipelineConfigError, run_pipeline
from contractguard.data_access.db import connect, init_schema
from contractguard.data_access.repository import ContractChangedError, get_run

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"
CUST_HEADER = ["customer_id", "full_name", "email", "country", "signup_date", "tier", "phone"]
ORD_HEADER = ["order_id", "customer_id", "order_ts", "status", "quantity",
              "unit_price", "currency", "coupon_code"]
T15 = "2026-01-15T10:00:00Z"
T16 = "2026-01-16T10:00:00Z"


@pytest.fixture
def conn():
    connection = connect(":memory:")
    init_schema(connection)
    yield connection
    connection.close()


def cust(n, **overrides):
    row = {"customer_id": f"CUST-{n:06d}", "full_name": f"Person {n}",
           "email": f"p{n}@example.com", "country": "IN", "signup_date": "2025-06-01",
           "tier": "gold", "phone": ""}
    row.update(overrides)
    return row


def order(n, customer=1, **overrides):
    row = {"order_id": f"ORD-{n:08d}", "customer_id": f"CUST-{customer:06d}",
           "order_ts": T15, "status": "placed", "quantity": "2",
           "unit_price": "499.50", "currency": "INR", "coupon_code": ""}
    row.update(overrides)
    return row


def write(tmp_path, name, header, rows, raw_lines=()):
    lines = [",".join(header)] + [",".join(r[h] for h in header) for r in rows] + list(raw_lines)
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def go(conn, dataset, path):
    return run_pipeline(conn, CONTRACTS, dataset, path)


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def load_customers(conn, tmp_path, n=3):
    path = write(tmp_path, "customers.csv", CUST_HEADER, [cust(i) for i in range(1, n + 1)])
    return go(conn, "customers", path)


def load_orders(conn, tmp_path, name, rows):
    return go(conn, "orders", write(tmp_path, name, ORD_HEADER, rows))


# ---- happy paths ------------------------------------------------------------

def test_customers_happy_path(conn, tmp_path):
    result = load_customers(conn, tmp_path)
    assert result.status == "success"
    assert result.input_rows == 3 and result.inserted_rows == 3
    assert result.reconciles
    assert count(conn, "customers_clean") == 3
    run = get_run(conn, result.run_id)
    assert run["status"] == "success" and run["ended_at"] is not None
    assert run["inserted_rows"] == 3


def test_orders_happy_path_writes_history(conn, tmp_path):
    load_customers(conn, tmp_path)
    result = load_orders(conn, tmp_path, "orders.csv", [order(1), order(2)])
    assert result.status == "success" and result.inserted_rows == 2 and result.reconciles
    assert count(conn, "orders_clean") == 2 and count(conn, "order_history") == 2
    row = conn.execute("SELECT * FROM orders_clean WHERE order_id = 'ORD-00000001'").fetchone()
    assert row["source_file"] == "orders.csv" and row["first_run_id"] == result.run_id


# ---- reruns and idempotency -------------------------------------------------

def test_rerun_of_same_file_is_skipped(conn, tmp_path):
    path = write(tmp_path, "customers.csv", CUST_HEADER, [cust(i) for i in (1, 2, 3)])
    first = go(conn, "customers", path)
    second = go(conn, "customers", path)
    assert first.status == "success"
    assert second.status == "skipped_already_loaded"
    assert second.input_rows == second.not_processed_rows == 3 and second.reconciles
    assert count(conn, "customers_clean") == 3
    assert count(conn, "pipeline_runs") == 2


def test_same_rows_in_a_new_file_are_unchanged_not_duplicated(conn, tmp_path):
    load_customers(conn, tmp_path)
    load_orders(conn, tmp_path, "orders_a.csv", [order(1), order(2)])
    second = load_orders(conn, tmp_path, "orders_b.csv", [order(2), order(1)])
    assert second.status == "success"
    assert second.unchanged_rows == 2 and second.inserted_rows == 0 and second.reconciles
    assert count(conn, "orders_clean") == 2 and count(conn, "order_history") == 2


# ---- empty and schema problems ---------------------------------------------

def test_empty_file(conn, tmp_path):
    path = tmp_path / "customers.csv"
    path.write_bytes(b"")
    result = go(conn, "customers", path)
    assert result.status == "empty" and result.input_rows == 0 and result.reconciles
    assert count(conn, "customers_clean") == 0
    assert get_run(conn, result.run_id)["status"] == "empty"


def test_header_only_file(conn, tmp_path):
    path = write(tmp_path, "customers.csv", CUST_HEADER, [])
    result = go(conn, "customers", path)
    assert result.status == "empty" and result.reconciles


def test_missing_required_column_rejects_whole_batch(conn, tmp_path):
    header = [h for h in ORD_HEADER if h != "status"]
    path = write(tmp_path, "orders.csv", header, [order(1), order(2)])
    result = go(conn, "orders", path)
    assert result.status == "rejected_schema"
    assert result.input_rows == result.not_processed_rows == 2 and result.reconciles
    assert "status" in result.failure_reason
    assert count(conn, "orders_clean") == 0


def test_unexpected_column_loads_with_a_warning(conn, tmp_path):
    path = write(tmp_path, "customers.csv", CUST_HEADER + ["channel"],
                 [cust(1, channel="web")])
    result = go(conn, "customers", path)
    assert result.status == "success" and result.inserted_rows == 1
    assert "UNEXPECTED_COLUMN" in [i.code for i in result.issues]


# ---- bad rows and quarantine ------------------------------------------------

def test_bad_row_is_quarantined_with_its_source_line(conn, tmp_path):
    path = write(tmp_path, "customers.csv", CUST_HEADER,
                 [cust(1), cust(2, email="nope"), cust(3)])
    result = go(conn, "customers", path)
    assert result.inserted_rows == 2 and result.rejected_rows == 1 and result.reconciles
    rows = conn.execute("SELECT * FROM quarantine").fetchall()
    assert len(rows) == 1
    assert rows[0]["row_number"] == 3 and rows[0]["reason_codes"] == "BAD_PATTERN"
    assert rows[0]["run_id"] == result.run_id and rows[0]["source_file"] == "customers.csv"
    assert "CUST-000002" in rows[0]["raw_record"]
    assert rows[0]["file_sha256"] == get_run(conn, result.run_id)["file_sha256"]


def test_malformed_line_is_quarantined(conn, tmp_path):
    path = write(tmp_path, "customers.csv", CUST_HEADER, [cust(i) for i in (1, 2, 3)],
                 raw_lines=["CUST-000004,OnlyTwoFields"])
    result = go(conn, "customers", path)
    assert result.input_rows == 4 and result.inserted_rows == 3 and result.rejected_rows == 1
    assert result.reject_reasons == {"MALFORMED_ROW": 1} and result.reconciles
    assert conn.execute("SELECT row_number FROM quarantine").fetchone()[0] == 5


def test_duplicate_rows_in_one_file(conn, tmp_path):
    path = write(tmp_path, "customers.csv", CUST_HEADER, [cust(1), cust(1)])
    result = go(conn, "customers", path)
    assert result.inserted_rows == 1 and result.rejected_rows == 1
    assert result.reject_reasons == {"DUPLICATE_EXACT": 1}
    assert count(conn, "customers_clean") == 1


def test_unknown_customer_is_quarantined_as_invalid_reference(conn, tmp_path):
    load_customers(conn, tmp_path)
    result = load_orders(conn, tmp_path, "orders.csv", [order(1), order(2, customer=99)])
    assert result.inserted_rows == 1 and result.rejected_rows == 1 and result.reconciles
    assert conn.execute("SELECT reason_codes FROM quarantine").fetchone()[0] == "INVALID_REFERENCE"
    assert count(conn, "orders_clean") == 1


def test_orders_before_customers_are_all_rejected(conn, tmp_path):
    result = load_orders(conn, tmp_path, "orders.csv", [order(1), order(2)])
    assert result.status == "success"
    assert result.inserted_rows == 0 and result.rejected_rows == 2 and result.reconciles
    assert result.reject_reasons == {"INVALID_REFERENCE": 2}


# ---- history, updates and late data ----------------------------------------

def test_newer_version_updates_current_row_and_keeps_history(conn, tmp_path):
    load_customers(conn, tmp_path)
    first = load_orders(conn, tmp_path, "orders_a.csv", [order(1, order_ts=T15)])
    second = load_orders(conn, tmp_path, "orders_b.csv",
                         [order(1, order_ts=T16, status="shipped")])
    assert second.updated_rows == 1 and second.reconciles
    row = conn.execute("SELECT * FROM orders_clean").fetchone()
    assert row["status"] == "shipped" and row["order_ts"] == T16
    assert row["first_run_id"] == first.run_id and row["last_run_id"] == second.run_id
    assert count(conn, "order_history") == 2


def test_late_arrival_goes_to_history_only(conn, tmp_path):
    load_customers(conn, tmp_path)
    load_orders(conn, tmp_path, "orders_a.csv", [order(1, order_ts=T16, status="shipped")])
    late = load_orders(conn, tmp_path, "orders_b.csv", [order(1, order_ts=T15, status="placed")])
    assert late.late_rows == 1 and late.updated_rows == 0 and late.reconciles
    row = conn.execute("SELECT * FROM orders_clean").fetchone()
    assert row["status"] == "shipped" and row["order_ts"] == T16  # not overwritten
    assert count(conn, "order_history") == 2
    flag = conn.execute(
        "SELECT is_late_arrival FROM order_history WHERE order_ts = ?", (T15,)
    ).fetchone()[0]
    assert flag == 1


def test_late_row_seen_again_is_not_duplicated(conn, tmp_path):
    load_customers(conn, tmp_path)
    load_orders(conn, tmp_path, "orders_a.csv", [order(1, order_ts=T16, status="shipped")])
    load_orders(conn, tmp_path, "orders_b.csv", [order(1, order_ts=T15, status="placed")])
    third = load_orders(conn, tmp_path, "orders_c.csv",
                        [order(1, order_ts=T15, status="placed"), order(2)])
    assert third.unchanged_rows == 1 and third.inserted_rows == 1 and third.late_rows == 0
    assert count(conn, "order_history") == 3


# ---- failure and recovery ---------------------------------------------------

def test_failed_load_rolls_back_and_a_retry_succeeds(conn, tmp_path, monkeypatch):
    path = write(tmp_path, "customers.csv", CUST_HEADER, [cust(i) for i in (1, 2, 3)])

    def boom(connection, run_id, source_file, accepted):
        connection.execute(
            "INSERT INTO customers_clean (customer_id, full_name, email, country, signup_date, "
            "tier, phone, source_file, first_run_id, last_run_id, loaded_at) VALUES "
            "('CUST-000001', 'x', 'x@x.com', 'IN', '2025-01-01', 'gold', NULL, 'f', ?, ?, "
            "'2026-01-01T00:00:00Z')", (run_id, run_id))
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline, "_apply_customers", boom)
    failed = go(conn, "customers", path)
    assert failed.status == "failed" and "boom" in failed.failure_reason
    assert failed.not_processed_rows == failed.input_rows == 3 and failed.reconciles
    assert count(conn, "customers_clean") == 0 and count(conn, "quarantine") == 0
    run = get_run(conn, failed.run_id)
    assert run["status"] == "failed" and "boom" in run["failure_reason"]

    monkeypatch.undo()
    retry = go(conn, "customers", path)
    assert retry.status == "success" and retry.inserted_rows == 3
    assert count(conn, "customers_clean") == 3


def test_unreadable_file_records_a_failed_run(conn, tmp_path):
    path = tmp_path / "bad.csv"
    path.write_bytes(b"\xff\xfe\x00\x01")
    result = go(conn, "customers", path)
    assert result.status == "failed" and "UTF-8" in result.failure_reason
    assert get_run(conn, result.run_id)["status"] == "failed"


def test_missing_file_records_a_failed_run(conn, tmp_path):
    result = go(conn, "customers", tmp_path / "nope.csv")
    assert result.status == "failed" and "not found" in result.failure_reason
    assert count(conn, "pipeline_runs") == 1


# ---- configuration problems -------------------------------------------------

def test_proposed_v2_contract_cannot_be_loaded(conn, tmp_path):
    with pytest.raises(PipelineConfigError, match="does not match"):
        run_pipeline(conn, CONTRACTS, "orders", tmp_path / "x.csv", contract_version=2)


def test_unknown_dataset_is_rejected(conn, tmp_path):
    with pytest.raises(PipelineConfigError, match="unknown dataset"):
        go(conn, "payments", tmp_path / "x.csv")


def test_missing_contract_version_is_rejected(conn, tmp_path):
    with pytest.raises(ContractError, match="not found"):
        run_pipeline(conn, CONTRACTS, "orders", tmp_path / "x.csv", contract_version=9)


def test_edited_contract_version_is_refused(conn, tmp_path):
    folder = tmp_path / "contracts_copy"
    shutil.copytree(CONTRACTS, folder)
    path = write(tmp_path, "customers.csv", CUST_HEADER, [cust(1)])
    assert run_pipeline(conn, folder, "customers", path).status == "success"
    target = folder / "customers_v1.yaml"
    original = target.read_text(encoding="utf-8")
    target.write_text(original.replace("gold]", "gold, platinum]"), encoding="utf-8")
    assert target.read_text(encoding="utf-8") != original
    with pytest.raises(ContractChangedError):
        run_pipeline(conn, folder, "customers", path)


# ---- reconciliation on a mixed file ----------------------------------------

def test_mixed_batch_reconciles_with_traceable_rejects(conn, tmp_path):
    load_customers(conn, tmp_path)
    rows = [order(1), order(2), order(1), order(4, quantity="x"), order(5, customer=99)]
    path = write(tmp_path, "orders.csv", ORD_HEADER, rows, raw_lines=["ORD-00000006,CUST-000001"])
    result = go(conn, "orders", path)
    assert result.input_rows == 6 and result.inserted_rows == 2 and result.rejected_rows == 4
    assert result.reconciles
    assert result.reject_reasons == {"BAD_TYPE": 1, "DUPLICATE_EXACT": 1,
                                     "INVALID_REFERENCE": 1, "MALFORMED_ROW": 1}
    lines = {r[0] for r in conn.execute("SELECT row_number FROM quarantine")}
    assert lines == {4, 5, 6, 7}
