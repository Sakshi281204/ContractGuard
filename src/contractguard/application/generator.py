"""Tier 2 (application): reproducible synthetic data generator with a separate answer key."""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import random
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

CUSTOMER_HEADER = ["customer_id", "full_name", "email", "country", "signup_date", "tier", "phone"]
ORDER_HEADER = ["order_id", "customer_id", "order_ts", "status", "quantity",
                "unit_price", "currency", "coupon_code"]
COUNTRIES = ["IN", "US", "GB", "DE", "AE"]
TIERS = ["standard", "silver", "gold"]
STATUSES = ["placed", "shipped", "delivered", "cancelled"]
CURRENCIES = ["INR", "USD"]
BASE_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)
IST = timezone(timedelta(hours=5, minutes=30))

# (reason code the validator should report, share of n_orders)
BATCH1_BAD = (("BAD_TYPE", 0.004), ("OUT_OF_RANGE", 0.004), ("NOT_ALLOWED", 0.004),
              ("NULL_REQUIRED", 0.003), ("BAD_PATTERN", 0.003), ("INVALID_REFERENCE", 0.004))
BATCH2_BAD = ("BAD_TYPE", "INVALID_REFERENCE")
CUSTOMER_BAD = ("BAD_PATTERN", "NOT_ALLOWED", "NULL_REQUIRED", "BAD_TYPE")


def _k(n: int, rate: float) -> int:
    """How many rows of a kind to create: at least one, so every case always appears."""
    return max(1, round(n * rate))


def _canonical(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _varied(dt: datetime, rng: random.Random) -> str:
    """Same instant written in one of three accepted timestamp styles."""
    style = rng.randrange(10)
    if style == 0:
        return dt.astimezone(IST).strftime("%Y-%m-%dT%H:%M:%S+05:30")
    if style == 1:
        return dt.strftime("%Y-%m-%d %H:%M:%S")  # no timezone: treated as UTC
    return _canonical(dt)


# ---- customers --------------------------------------------------------------

def _customer(number: int, rng: random.Random) -> dict:
    signup = date(2024, 1, 1) + timedelta(days=rng.randrange(0, 700))
    return {
        "customer_id": f"CUST-{number:06d}",
        "full_name": f"Customer {number}",
        "email": f"customer{number}@example.com",
        "country": rng.choice(COUNTRIES),
        "signup_date": signup.isoformat(),
        "tier": rng.choice(TIERS),
        "phone": rng.choice(["", f"+91{rng.randrange(7000000000, 9999999999)}"]),
    }


def _bad_customer(kind: str, number: int, rng: random.Random) -> dict:
    row = _customer(number, rng)
    if kind == "BAD_PATTERN":
        row["email"] = "not-an-email"
    elif kind == "NOT_ALLOWED":
        row["country"] = "FR"
    elif kind == "NULL_REQUIRED":
        row["full_name"] = ""
    elif kind == "BAD_TYPE":
        row["signup_date"] = "2025-13-45"
    return row


# ---- orders -----------------------------------------------------------------

def _new_order(number: int, n_customers: int, rng: random.Random) -> dict:
    roll = rng.randrange(50)
    quantity = 1 if roll == 0 else 1000 if roll == 1 else rng.randrange(1, 1001)
    price = 0.01 if rng.randrange(50) == 0 else round(rng.uniform(1, 5000), 2)
    return {
        "number": number,
        "customer": rng.randrange(1, n_customers + 1),
        "dt": BASE_TS + timedelta(seconds=rng.randrange(0, 150 * 86400)),
        "status": rng.choice(STATUSES),
        "quantity": quantity,
        "price": price,
        "currency": rng.choice(CURRENCIES),
        "coupon": rng.choice(["", "", f"SAVE{rng.randrange(5, 50)}"]),
    }


def _order_row(rec: dict, ts_text: str) -> dict:
    return {
        "order_id": f"ORD-{rec['number']:08d}",
        "customer_id": f"CUST-{rec['customer']:06d}",
        "order_ts": ts_text,
        "status": rec["status"],
        "quantity": str(rec["quantity"]),
        "unit_price": f"{rec['price']:.2f}",
        "currency": rec["currency"],
        "coupon_code": rec["coupon"],
    }


def _bad_order(kind: str, number: int, n_customers: int, rng: random.Random) -> dict:
    rec = _new_order(number, n_customers, rng)
    row = _order_row(rec, _canonical(rec["dt"]))
    if kind == "BAD_TYPE":
        row["quantity"] = "abc"
    elif kind == "OUT_OF_RANGE":
        row["quantity"] = rng.choice(["0", "1001"])
    elif kind == "NOT_ALLOWED":
        row["status"] = "unknown"
    elif kind == "NULL_REQUIRED":
        row["customer_id"] = ""
    elif kind == "BAD_PATTERN":
        row["order_id"] = f"BAD-{number}"
    elif kind == "INVALID_REFERENCE":
        row["customer_id"] = f"CUST-{990000 + rng.randrange(1, 9999):06d}"
    return row


# ---- files and answer key ---------------------------------------------------

def _write_rows(path: Path, header: list, entries: list) -> None:
    """Write dict rows through csv, and plain strings as raw (malformed) lines."""
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        for entry in entries:
            if isinstance(entry, str):
                handle.write(entry + "\n")
            else:
                writer.writerow([entry[h] for h in header])


def _step(file, dataset, status, input_rows, scenario, inserted=0, updated=0, late=0,
          unchanged=0, rejected=0, not_processed=0, reasons=None) -> dict:
    return {
        "file": file, "dataset": dataset, "scenario": scenario, "status": status,
        "input_rows": input_rows, "inserted_rows": inserted, "updated_rows": updated,
        "late_rows": late, "unchanged_rows": unchanged, "rejected_rows": rejected,
        "not_processed_rows": not_processed,
        "reject_reasons": dict(sorted((reasons or {}).items())),
    }


def _simple_order(number: int) -> dict:
    rec = {"number": number, "customer": 1, "dt": BASE_TS + timedelta(days=number % 50),
           "status": "placed", "quantity": 2, "price": 499.5, "currency": "INR", "coupon": ""}
    return _order_row(rec, _canonical(rec["dt"]))


def generate_dataset(out_dir, n_customers: int = 10_000, n_orders: int = 100_000,
                     seed: int = 42) -> dict:
    """Write input CSV files and a separate answer key. Returns the answer key."""
    if n_customers < 10 or n_customers > 500_000:
        raise ValueError("n_customers must be between 10 and 500000")
    if n_orders < 20 or n_orders > 1_000_000:
        raise ValueError("n_orders must be between 20 and 1000000")

    rng = random.Random(seed)
    out = Path(out_dir)
    inp, exp = out / "input", out / "expected"
    inp.mkdir(parents=True, exist_ok=True)
    exp.mkdir(parents=True, exist_ok=True)
    steps: list = []

    # 1) customers.csv
    customers = [_customer(i, rng) for i in range(1, n_customers + 1)]
    kc = _k(n_customers, 0.01)
    bad_ids = itertools.count(900_001)
    reasons: Counter = Counter()
    entries: list = list(customers)
    for kind in CUSTOMER_BAD:
        for _ in range(kc):
            entries.append(_bad_customer(kind, next(bad_ids), rng))
            reasons[kind] += 1
    for _ in range(kc):
        entries.append(f"CUST-{next(bad_ids):06d},OnlyTwoFields")
        reasons["MALFORMED_ROW"] += 1
    rng.shuffle(entries)
    entries += [dict(c) for c in rng.sample(customers, kc)]  # duplicates always come later
    reasons["DUPLICATE_EXACT"] += kc
    _write_rows(inp / "customers.csv", CUSTOMER_HEADER, entries)
    steps.append(_step("customers.csv", "customers", "success", len(entries),
                       "valid customers plus bad email/country/name/date, a duplicate and a "
                       "malformed line", inserted=n_customers, rejected=sum(reasons.values()),
                       reasons=reasons))

    # 2) orders_batch_1.csv
    extra_ids = itertools.count(50_000_001)
    recs = [_new_order(i, n_customers, rng) for i in range(1, n_orders + 1)]
    valid_rows = [_order_row(r, _varied(r["dt"], rng)) for r in recs]
    entries = list(valid_rows)
    reasons = Counter()
    for kind, rate in BATCH1_BAD:
        for _ in range(_k(n_orders, rate)):
            entries.append(_bad_order(kind, next(extra_ids), n_customers, rng))
            reasons[kind] += 1
    for _ in range(_k(n_orders, 0.002)):
        entries.append(f"ORD-{next(extra_ids):08d},CUST-000001")
        reasons["MALFORMED_ROW"] += 1
    rng.shuffle(entries)
    for original in rng.sample(valid_rows, _k(n_orders, 0.004)):
        entries.append(dict(original))
        reasons["DUPLICATE_EXACT"] += 1
    for original in rng.sample(valid_rows, _k(n_orders, 0.002)):
        clash = dict(original)
        clash["quantity"] = "2" if original["quantity"] == "1" else "1"
        entries.append(clash)
        reasons["DUPLICATE_KEY_CONFLICT"] += 1
    _write_rows(inp / "orders_batch_1.csv", ORDER_HEADER, entries)
    steps.append(_step("orders_batch_1.csv", "orders", "success", len(entries),
                       "first load: valid, boundary, bad, duplicate, malformed and "
                       "invalid-reference rows", inserted=n_orders,
                       rejected=sum(reasons.values()), reasons=reasons))

    # 3) orders_batch_2.csv: updates, late arrivals, unchanged re-sends, new orders
    n_upd, n_late, n_same, n_new = (_k(n_orders, 0.05), _k(n_orders, 0.03),
                                    _k(n_orders, 0.05), _k(n_orders, 0.10))
    chosen = rng.sample(range(n_orders), n_upd + n_late + n_same)
    upd_idx, late_idx = chosen[:n_upd], chosen[n_upd:n_upd + n_late]
    same_idx = chosen[n_upd + n_late:]
    entries = []
    for i in upd_idx:
        rec = dict(recs[i])
        rec["dt"] = rec["dt"] + timedelta(days=rng.randrange(1, 30), seconds=rng.randrange(3600))
        rec["status"] = rng.choice([s for s in STATUSES if s != recs[i]["status"]])
        entries.append(_order_row(rec, _canonical(rec["dt"])))
    for i in late_idx:
        rec = dict(recs[i])
        rec["dt"] = rec["dt"] - timedelta(days=rng.randrange(1, 30), seconds=rng.randrange(3600))
        rec["status"] = rng.choice([s for s in STATUSES if s != recs[i]["status"]])
        entries.append(_order_row(rec, _canonical(rec["dt"])))
    for i in same_idx:
        entries.append(_order_row(recs[i], _canonical(recs[i]["dt"])))
    for j in range(1, n_new + 1):
        rec = _new_order(n_orders + j, n_customers, rng)
        entries.append(_order_row(rec, _canonical(rec["dt"])))
    reasons = Counter()
    for kind in BATCH2_BAD:
        for _ in range(_k(n_orders, 0.002)):
            entries.append(_bad_order(kind, next(extra_ids), n_customers, rng))
            reasons[kind] += 1
    rng.shuffle(entries)
    _write_rows(inp / "orders_batch_2.csv", ORDER_HEADER, entries)
    steps.append(_step("orders_batch_2.csv", "orders", "success", len(entries),
                       "second load: updates, late arrivals, unchanged re-sends, new orders "
                       "and a few bad rows", inserted=n_new, updated=n_upd, late=n_late,
                       unchanged=n_same, rejected=sum(reasons.values()), reasons=reasons))

    # 4) schema-change and empty-batch files (small, fixed content)
    extra_rows = [dict(_simple_order(70_000_000 + i), channel="web") for i in (1, 2)]
    _write_rows(inp / "orders_extra_column.csv", ORDER_HEADER + ["channel"], extra_rows)
    steps.append(_step("orders_extra_column.csv", "orders", "success", 2,
                       "unexpected extra column: loads with a warning", inserted=2))

    short_header = [h for h in ORDER_HEADER if h != "status"]
    _write_rows(inp / "orders_missing_column.csv", short_header,
                [_simple_order(60_000_000 + i) for i in (1, 2, 3)])
    steps.append(_step("orders_missing_column.csv", "orders", "rejected_schema", 3,
                       "required column 'status' missing: whole batch rejected",
                       not_processed=3))

    v2_header = ["order_id", "customer_id", "order_ts", "status", "quantity",
                 "price_inr", "currency", "coupon_code", "channel"]
    v2_rows = []
    for i in (1, 2):
        row = dict(_simple_order(80_000_000 + i), channel="web")
        row["price_inr"] = row["unit_price"]
        v2_rows.append(row)
    _write_rows(inp / "orders_v2_style.csv", v2_header, v2_rows)
    steps.append(_step("orders_v2_style.csv", "orders", "rejected_schema", 2,
                       "producer switched to the v2 layout (unit_price replaced by price_inr)",
                       not_processed=2))

    (inp / "orders_empty.csv").write_bytes(b"")
    steps.append(_step("orders_empty.csv", "orders", "empty", 0, "zero-byte file"))
    (inp / "orders_header_only.csv").write_text(",".join(ORDER_HEADER) + "\n", encoding="utf-8")
    steps.append(_step("orders_header_only.csv", "orders", "empty", 0, "header but no rows"))

    for number, step in enumerate(steps, start=1):
        step["step"] = number
    expected = {
        "seed": seed, "n_customers": n_customers, "n_orders": n_orders,
        "note": "Answer key. Run steps in order. Re-running orders_batch_1.csv afterwards "
                "must give status skipped_already_loaded and change no table.",
        "steps": steps,
        "final_table_counts": {
            "customers_clean": n_customers,
            "orders_clean": n_orders + n_new + 2,
            "order_history": n_orders + n_new + n_upd + n_late + 2,
            "quarantine": sum(s["rejected_rows"] for s in steps),
        },
    }
    (exp / "expected_results.json").write_text(
        json.dumps(expected, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return expected


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Generate ContractGuard synthetic data.")
    parser.add_argument("--out", default="data/sample")
    parser.add_argument("--customers", type=int, default=10_000)
    parser.add_argument("--orders", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    expected = generate_dataset(args.out, args.customers, args.orders, args.seed)
    total = sum(s["input_rows"] for s in expected["steps"])
    print(f"Generated {len(expected['steps'])} files with {total} records "
          f"(seed {args.seed}) in {args.out}")
    print("Answer key: " + str(Path(args.out) / "expected" / "expected_results.json"))


if __name__ == "__main__":
    main()
