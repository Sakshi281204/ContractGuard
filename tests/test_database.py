import re

import pytest
import sqlite3

from contractguard.data_access.db import connect, init_schema, utc_now_iso
from contractguard.data_access.repository import start_run


@pytest.fixture
def conn():
    connection = connect(":memory:")
    init_schema(connection)
    yield connection
    connection.close()


def make_run(conn):
    return start_run(conn, "orders", 1, "f.csv", "a" * 64)


def insert_customer(conn, run_id, customer_id="CUST-000001"):
    conn.execute(
        "INSERT INTO customers_clean (customer_id, full_name, email, country, signup_date, "
        "tier, phone, source_file, first_run_id, last_run_id, loaded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (customer_id, "Asha Rao", "a@x.com", "IN", "2025-01-01", "gold", None,
         "f.csv", run_id, run_id, "2026-01-01T00:00:00Z"),
    )


def insert_order(conn, run_id, customer_id, order_id="ORD-00000001"):
    conn.execute(
        "INSERT INTO orders_clean (order_id, customer_id, order_ts, status, quantity, "
        "unit_price, currency, coupon_code, source_file, first_run_id, last_run_id, loaded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (order_id, customer_id, "2026-01-01T00:00:00Z", "placed", 1, 10.0, "INR", None,
         "f.csv", run_id, run_id, "2026-01-01T00:00:00Z"),
    )


def test_schema_creates_expected_tables(conn):
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    expected = {"contract_versions", "pipeline_runs", "customers_clean",
                "orders_clean", "order_history", "quarantine"}
    assert expected <= names


def test_init_schema_twice_is_safe(conn):
    init_schema(conn)
    init_schema(conn)


def test_foreign_key_blocks_order_for_unknown_customer(conn):
    run_id = make_run(conn)
    with pytest.raises(sqlite3.IntegrityError):
        insert_order(conn, run_id, "CUST-999999")
    insert_customer(conn, run_id, "CUST-000001")
    insert_order(conn, run_id, "CUST-000001")  # works once the customer exists
    assert conn.execute("SELECT COUNT(*) FROM orders_clean").fetchone()[0] == 1


def test_primary_key_blocks_duplicate_customer(conn):
    run_id = make_run(conn)
    insert_customer(conn, run_id)
    with pytest.raises(sqlite3.IntegrityError):
        insert_customer(conn, run_id)


def test_run_status_check_constraint(conn):
    run_id = make_run(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE pipeline_runs SET status = 'weird' WHERE run_id = ?", (run_id,))


def test_connect_creates_parent_folder(tmp_path):
    target = tmp_path / "sub" / "x.db"
    connection = connect(target)
    connection.close()
    assert target.exists()


def test_utc_now_format():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", utc_now_iso())
