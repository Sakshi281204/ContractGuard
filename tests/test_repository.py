from pathlib import Path

import pytest

from contractguard.application.contract_loader import load_contract
from contractguard.data_access.db import connect, init_schema
from contractguard.data_access.repository import (
    ContractChangedError,
    add_quarantine,
    find_loaded_run,
    finish_run,
    get_run,
    new_run_id,
    quarantine_id,
    register_contract,
    start_run,
)

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"
SHA = "a" * 64


@pytest.fixture
def conn():
    connection = connect(":memory:")
    init_schema(connection)
    yield connection
    connection.close()


def item(row_number, codes=("BAD_TYPE",)):
    return {"row_number": row_number, "reason_codes": list(codes),
            "reason_detail": "bad value", "raw": {"order_id": f"ORD-{row_number}"}}


def test_start_run_records_a_running_row(conn):
    run_id = start_run(conn, "orders", 1, "f.csv", SHA)
    row = get_run(conn, run_id)
    assert row["status"] == "running" and row["ended_at"] is None
    assert row["dataset"] == "orders" and row["source_file"] == "f.csv"


def test_finish_run_success_stores_counts(conn):
    run_id = start_run(conn, "orders", 1, "f.csv", SHA)
    finish_run(conn, run_id, "success", {"input_rows": 10, "inserted_rows": 7, "rejected_rows": 3})
    row = get_run(conn, run_id)
    assert row["status"] == "success" and row["ended_at"] is not None
    assert (row["input_rows"], row["inserted_rows"], row["rejected_rows"]) == (10, 7, 3)
    assert row["late_rows"] == 0


def test_finish_run_failed_stores_reason(conn):
    run_id = start_run(conn, "orders", 1, "f.csv", SHA)
    finish_run(conn, run_id, "failed", failure_reason="disk full")
    row = get_run(conn, run_id)
    assert row["status"] == "failed" and row["failure_reason"] == "disk full"


def test_finish_unknown_run_raises(conn):
    with pytest.raises(LookupError):
        finish_run(conn, "run_missing", "success")


def test_finish_run_rejects_unknown_count_field(conn):
    run_id = start_run(conn, "orders", 1, "f.csv", SHA)
    with pytest.raises(ValueError, match="unknown count"):
        finish_run(conn, run_id, "success", {"bogus_rows": 1})


def test_find_loaded_run_only_returns_successful_runs(conn):
    run_id = start_run(conn, "orders", 1, "f.csv", SHA)
    assert find_loaded_run(conn, "orders", SHA, 1) is None  # still running
    finish_run(conn, run_id, "success", {"input_rows": 1})
    assert find_loaded_run(conn, "orders", SHA, 1)["run_id"] == run_id
    assert find_loaded_run(conn, "orders", "b" * 64, 1) is None  # other file content
    assert find_loaded_run(conn, "orders", SHA, 2) is None       # other contract version
    assert find_loaded_run(conn, "customers", SHA, 1) is None    # other dataset


def test_failed_run_is_not_treated_as_loaded(conn):
    run_id = start_run(conn, "orders", 1, "f.csv", SHA)
    finish_run(conn, run_id, "failed", failure_reason="crash")
    assert find_loaded_run(conn, "orders", SHA, 1) is None


def test_quarantine_rows_are_stored(conn):
    run_id = start_run(conn, "orders", 1, "f.csv", SHA)
    added = add_quarantine(conn, run_id, "orders", "f.csv", SHA, [item(2), item(5, ("A", "B"))])
    conn.commit()
    assert added == 2
    rows = conn.execute("SELECT * FROM quarantine ORDER BY row_number").fetchall()
    assert [r["row_number"] for r in rows] == [2, 5]
    assert rows[1]["reason_codes"] == "A,B"
    assert "ORD-5" in rows[1]["raw_record"]


def test_quarantine_rerun_does_not_duplicate(conn):
    run_id = start_run(conn, "orders", 1, "f.csv", SHA)
    assert add_quarantine(conn, run_id, "orders", "f.csv", SHA, [item(2)]) == 1
    assert add_quarantine(conn, run_id, "orders", "f.csv", SHA, [item(2)]) == 0
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0] == 1


def test_quarantine_id_is_deterministic_and_row_specific():
    assert quarantine_id("orders", SHA, 2) == quarantine_id("orders", SHA, 2)
    assert quarantine_id("orders", SHA, 2) != quarantine_id("orders", SHA, 3)
    assert quarantine_id("orders", SHA, 2) != quarantine_id("customers", SHA, 2)


def test_register_contract_first_time_then_unchanged(conn):
    path = CONTRACTS / "orders_v1.yaml"
    contract = load_contract(path)
    assert register_contract(conn, contract, path) == "registered"
    assert register_contract(conn, contract, path) == "unchanged"


def test_register_contract_rejects_edited_version(conn, tmp_path):
    original = CONTRACTS / "orders_v1.yaml"
    contract = load_contract(original)
    register_contract(conn, contract, original)
    edited = tmp_path / "orders_v1.yaml"
    edited.write_text(original.read_text(encoding="utf-8").replace("max: 1000", "max: 999"),
                      encoding="utf-8")
    with pytest.raises(ContractChangedError, match="publish a new version"):
        register_contract(conn, contract, edited)


def test_run_ids_are_unique():
    assert new_run_id() != new_run_id()
