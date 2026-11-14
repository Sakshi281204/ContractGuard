import csv
import json
from pathlib import Path

import pytest

from contractguard.application.generator import generate_dataset
from contractguard.application.pipeline import run_pipeline
from contractguard.data_access.db import connect, init_schema

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"
COUNT_KEYS = ("input_rows", "inserted_rows", "updated_rows", "late_rows",
              "unchanged_rows", "rejected_rows", "not_processed_rows")


def read_rows(path):
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_same_seed_gives_identical_files(tmp_path):
    first = generate_dataset(tmp_path / "a", 40, 100, seed=7)
    second = generate_dataset(tmp_path / "b", 40, 100, seed=7)
    assert first == second
    for name in ("customers.csv", "orders_batch_1.csv", "orders_batch_2.csv"):
        assert (tmp_path / "a" / "input" / name).read_bytes() == \
               (tmp_path / "b" / "input" / name).read_bytes()


def test_different_seed_gives_different_files(tmp_path):
    generate_dataset(tmp_path / "a", 40, 100, seed=7)
    generate_dataset(tmp_path / "b", 40, 100, seed=8)
    assert (tmp_path / "a" / "input" / "orders_batch_1.csv").read_bytes() != \
           (tmp_path / "b" / "input" / "orders_batch_1.csv").read_bytes()


def test_answer_key_is_kept_separate_from_input(tmp_path):
    generate_dataset(tmp_path, 40, 100, seed=7)
    inputs = {p.name for p in (tmp_path / "input").iterdir()}
    assert all(name.endswith(".csv") for name in inputs)
    assert {p.name for p in (tmp_path / "expected").iterdir()} == {"expected_results.json"}
    for name in inputs:
        text = (tmp_path / "input" / name).read_text(encoding="utf-8")
        assert "expected" not in text.lower()


def test_returned_key_matches_the_json_file(tmp_path):
    expected = generate_dataset(tmp_path, 40, 100, seed=7)
    on_disk = json.loads((tmp_path / "expected" / "expected_results.json").read_text("utf-8"))
    assert on_disk == expected


@pytest.mark.parametrize("n_customers,n_orders", [(40, 100), (300, 2000)])
def test_pipeline_output_matches_the_answer_key(tmp_path, n_customers, n_orders):
    expected = generate_dataset(tmp_path, n_customers, n_orders, seed=7)
    conn = connect(":memory:")
    init_schema(conn)
    for step in expected["steps"]:
        path = tmp_path / "input" / step["file"]
        result = run_pipeline(conn, CONTRACTS, step["dataset"], path)
        assert result.status == step["status"], step["file"]
        for key in COUNT_KEYS:
            assert getattr(result, key) == step[key], f"{step['file']}: {key}"
        assert result.reject_reasons == step["reject_reasons"], step["file"]
        assert result.reconciles, step["file"]
    for table, wanted in expected["final_table_counts"].items():
        actual = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert actual == wanted, table
    conn.close()


def test_rerunning_a_loaded_file_is_skipped(tmp_path):
    expected = generate_dataset(tmp_path, 40, 100, seed=7)
    conn = connect(":memory:")
    init_schema(conn)
    for step in expected["steps"]:
        run_pipeline(conn, CONTRACTS, step["dataset"], tmp_path / "input" / step["file"])
    before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in expected["final_table_counts"]}
    again = run_pipeline(conn, CONTRACTS, "orders", tmp_path / "input" / "orders_batch_1.csv")
    assert again.status == "skipped_already_loaded" and again.reconciles
    after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in before}
    assert after == before
    conn.close()


@pytest.mark.parametrize("customers,orders", [(5, 100), (40, 10), (600_000, 100)])
def test_invalid_sizes_are_rejected(tmp_path, customers, orders):
    with pytest.raises(ValueError):
        generate_dataset(tmp_path, customers, orders)


def test_sample_covers_every_required_edge_case(tmp_path):
    expected = generate_dataset(tmp_path, 40, 100, seed=7)
    codes = set()
    for step in expected["steps"]:
        codes |= set(step["reject_reasons"])
    assert codes == {"BAD_TYPE", "OUT_OF_RANGE", "NOT_ALLOWED", "NULL_REQUIRED",
                     "BAD_PATTERN", "INVALID_REFERENCE", "DUPLICATE_EXACT",
                     "DUPLICATE_KEY_CONFLICT", "MALFORMED_ROW"}
    statuses = {s["status"] for s in expected["steps"]}
    assert {"success", "rejected_schema", "empty"} <= statuses
    second = next(s for s in expected["steps"] if s["file"] == "orders_batch_2.csv")
    assert second["updated_rows"] > 0 and second["late_rows"] > 0
    assert second["unchanged_rows"] > 0 and second["inserted_rows"] > 0


def test_generated_data_has_boundary_values_and_timezone_variants(tmp_path):
    generate_dataset(tmp_path, 100, 2000, seed=7)
    rows = read_rows(tmp_path / "input" / "orders_batch_1.csv")
    quantities = {r["quantity"] for r in rows}
    assert {"1", "1000"} <= quantities
    assert "0.01" in {r["unit_price"] for r in rows}
    stamps = [r["order_ts"] for r in rows]
    assert any(s.endswith("+05:30") for s in stamps)
    assert any(" " in s for s in stamps)
    assert any(s.endswith("Z") for s in stamps)
