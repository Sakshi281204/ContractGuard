import argparse
import json
import logging
import sqlite3
from dataclasses import asdict, is_dataclass
from pathlib import Path

from contractguard.application.generator import generate_dataset
from contractguard.application.pipeline import run_pipeline
from contractguard.application.contract_loader import load_contract
from contractguard.application.validator import validate_batch
from contractguard.application.compatibility import compare_files
from contractguard.data_access.db import connect, init_schema
from contractguard.data_access.reader import read_csv_file


def to_dict(result):
    if is_dataclass(result):
        return asdict(result)
    if hasattr(result, "__dict__"):
        return vars(result)
    return {"result": str(result)}


def save_json(payload, path):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8"
    )
    print(f"Report saved: {target}")


def command_generate(args):
    result = generate_dataset(
        args.out, args.customers, args.orders, args.seed
    )
    print(f"Generated {len(result['steps'])} files.")
    print(f"Customers: {args.customers}")
    print(f"Orders: {args.orders}")
    print(f"Seed: {args.seed}")
    print(f"Input files: {Path(args.out) / 'input'}")
    print(f"Answer key: {Path(args.out) / 'expected' / 'expected_results.json'}")
    return 0


def command_run(args):
    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = connect(str(db_path))
    try:
        init_schema(conn)

        result = run_pipeline(
            conn,
            args.contracts,
            args.dataset,
            args.file,
            args.version
        )

        payload = to_dict(result)
        print(json.dumps(payload, indent=2, default=str))
        save_json(payload, args.report)
        print(f"Database: {db_path}")

        return 0 if getattr(result, "reconciles", False) else 1
    finally:
        conn.close()


def command_validate(args):
    contract = load_contract(args.contract)
    source = read_csv_file(args.file)

    result = validate_batch(
        contract,
        source.header,
        source.rows
    )

    rejected = [
        {
            "row_number": row.row_number,
            "raw": row.raw,
            "reasons": [asdict(reason) for reason in row.reasons]
        }
        for row in result.rejected
    ]

    payload = {
        "command": "validate",
        "contract": contract.name,
        "contract_version": contract.version,
        "source_file": source.file_name,
        "sha256": source.sha256,
        "status": result.status,
        "input_rows": result.input_rows,
        "accepted_rows": result.accepted_count,
        "rejected_rows": result.rejected_count,
        "malformed_rows": [
            {
                "line_number": row.line_number,
                "raw": row.raw,
                "reason": row.reason
            }
            for row in source.malformed
        ],
        "schema_issues": [asdict(issue) for issue in result.issues],
        "reject_reasons": result.reason_counts(),
        "rejected_records": rejected
    }

    print(json.dumps(payload, indent=2, default=str))
    save_json(payload, args.report)

    if result.status == "schema_mismatch":
        return 1

    if result.rejected_count or source.malformed:
        return 1

    return 0


def command_compare(args):
    result = compare_files(args.old, args.new)
    payload = result.to_dict()

    print(result.to_text())
    save_json(payload, args.report)

    return 0


def command_report(args):
    db_path = Path(args.db)

    if not db_path.is_file():
        raise FileNotFoundError(f"Database not found: {db_path}")

    conn = connect(str(db_path))

    try:
        run_query = "SELECT * FROM pipeline_runs WHERE 1=1"
        quarantine_query = "SELECT * FROM quarantine WHERE 1=1"
        run_params = []
        quarantine_params = []

        if args.run_id:
            run_query += " AND run_id = ?"
            quarantine_query += " AND run_id = ?"
            run_params.append(args.run_id)
            quarantine_params.append(args.run_id)

        if args.dataset:
            run_query += " AND dataset = ?"
            quarantine_query += " AND dataset = ?"
            run_params.append(args.dataset)
            quarantine_params.append(args.dataset)

        run_query += " ORDER BY started_at DESC"
        quarantine_query += " ORDER BY quarantined_at DESC"

        runs = [
            dict(row)
            for row in conn.execute(run_query, run_params).fetchall()
        ]

        quarantined = [
            dict(row)
            for row in conn.execute(
                quarantine_query, quarantine_params
            ).fetchall()
        ]

        payload = {
            "database": str(db_path),
            "filters": {
                "run_id": args.run_id,
                "dataset": args.dataset
            },
            "total_runs": len(runs),
            "total_quarantined_records": len(quarantined),
            "runs": runs,
            "quarantine": quarantined
        }

        print(json.dumps(payload, indent=2, default=str))
        save_json(payload, args.report)

        return 0
    finally:
        conn.close()


def build_parser():
    parser = argparse.ArgumentParser(
        prog="contractguard",
        description="ContractGuard data quality pipeline"
    )

    commands = parser.add_subparsers(
        dest="command",
        required=True
    )

    generate = commands.add_parser(
        "generate",
        help="Generate reproducible sample data"
    )
    generate.add_argument("--out", default="data/sample")
    generate.add_argument("--customers", type=int, default=30)
    generate.add_argument("--orders", type=int, default=60)
    generate.add_argument("--seed", type=int, default=42)
    generate.set_defaults(func=command_generate)

    run = commands.add_parser(
        "run",
        help="Run a dataset file through the ingestion pipeline"
    )
    run.add_argument("--contracts", default="contracts")
    run.add_argument("--dataset", required=True)
    run.add_argument("--file", required=True)
    run.add_argument("--db", default="data/contractguard.db")
    run.add_argument("--version", type=int, default=1)
    run.add_argument("--report", default="reports/latest_run.json")
    run.set_defaults(func=command_run)

    validate = commands.add_parser(
        "validate",
        help="Validate a CSV file against a data contract"
    )
    validate.add_argument("--contract", required=True)
    validate.add_argument("--file", required=True)
    validate.add_argument("--report", default="reports/validation_report.json")
    validate.set_defaults(func=command_validate)

    compare = commands.add_parser(
        "compare",
        help="Compare two versions of a data contract"
    )
    compare.add_argument("--old", required=True)
    compare.add_argument("--new", required=True)
    compare.add_argument("--report", default="reports/compatibility_report.json")
    compare.set_defaults(func=command_compare)

    report = commands.add_parser(
        "report",
        help="Display pipeline run and quarantine reports"
    )
    report.add_argument("--db", default="data/contractguard.db")
    report.add_argument("--run-id")
    report.add_argument("--dataset")
    report.add_argument("--report", default="reports/pipeline_report.json")
    report.set_defaults(func=command_report)

    return parser


def main():
    logging.basicConfig(level=logging.INFO)
    parser = build_parser()
    args = parser.parse_args()

    try:
        return args.func(args)
    except Exception as exc:
        logging.exception("ContractGuard command failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
