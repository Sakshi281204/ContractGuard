"""ContractGuard - Data Quality and Pipeline Monitoring Dashboard."""
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import asdict, is_dataclass
from pathlib import Path

import pandas as pd
import altair as alt
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from contractguard.application.compatibility import compare_files
from contractguard.application.contract_loader import load_contract
from contractguard.application.pipeline import run_pipeline
from contractguard.application.validator import validate_batch
from contractguard.data_access.db import connect, init_schema
from contractguard.data_access.reader import read_csv_file

DB_PATH = PROJECT_ROOT / "data" / "contractguard.db"
CONTRACTS_DIR = PROJECT_ROOT / "contracts"

st.set_page_config(
    page_title="ContractGuard | Data Quality",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .stApp {
        background: #F5F8F6;
        color: #172B24;
    }
    [data-testid="stSidebar"] {
        background: #102F25;
    }
    [data-testid="stSidebar"] * {
        color: #F1F8F4 !important;
    }
    [data-testid="stSidebar"] [data-testid="stRadio"] label {
        color: #F1F8F4 !important;
    }
    .block-container {
        padding-top: 1.8rem;
        padding-bottom: 3rem;
        max-width: 1500px;
    }
    h1, h2, h3, h4 {
        color: #173D2E !important;
        font-weight: 700 !important;
    }
    p, label, .stMarkdown, [data-testid="stCaptionContainer"] {
        color: #40564B;
    }
    [data-testid="stMetric"] {
        background: #FFFFFF;
        border: 1px solid #DCE9E0;
        border-radius: 14px;
        padding: 18px 20px;
        box-shadow: 0 2px 8px rgba(20, 60, 40, 0.05);
    }
    [data-testid="stMetricLabel"] {
        color: #53685D !important;
        font-weight: 600;
    }
    [data-testid="stMetricValue"] {
        color: #145B3D !important;
        font-weight: 750;
    }
    [data-testid="stMetricDelta"] {
        color: #16845B !important;
    }
    div[data-testid="stVerticalBlockBorderWrapper"] {
        background: #FFFFFF;
        border: 1px solid #DCE9E0;
        border-radius: 14px;
    }
    div.stButton > button[kind="primary"] {
        background: #16845B;
        border: 1px solid #16845B;
        color: white;
        border-radius: 9px;
        font-weight: 650;
    }
    div.stButton > button[kind="primary"]:hover {
        background: #106A48;
        border-color: #106A48;
        color: white;
    }
    div[data-testid="stDataFrame"] {
        border: 1px solid #DCE9E0;
        border-radius: 10px;
        overflow: hidden;
    }
    .hero {
        background: linear-gradient(110deg, #123E2D, #16845B);
        padding: 27px 30px;
        border-radius: 17px;
        margin-bottom: 22px;
        color: white;
    }
    .hero h1 {
        color: white !important;
        margin: 0;
        font-size: 30px;
    }
    .hero p {
        color: #E0F2E8 !important;
        margin: 7px 0 0 0;
        font-size: 15px;
    }
    .section-note {
        color: #63766B;
        font-size: 13px;
        margin-top: -8px;
    }
    .status-pill {
        display: inline-block;
        background: #E5F5EC;
        color: #176B45;
        padding: 5px 11px;
        border-radius: 20px;
        font-size: 12px;
        font-weight: 700;
    }
</style>
""", unsafe_allow_html=True)


# -------------------- DATABASE HELPERS --------------------

def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(DB_PATH)
    init_schema(conn)
    return conn


def fetch_all(query, params=()):
    conn = get_connection()
    try:
        return [dict(row) for row in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def fetch_one(query, params=()):
    conn = get_connection()
    try:
        row = conn.execute(query, params).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def as_dict(value):
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return vars(value)
    return {"result": str(value)}


def hero(title, subtitle):
    st.markdown(
        f'<div class="hero"><h1>{title}</h1><p>{subtitle}</p></div>',
        unsafe_allow_html=True,
    )


def section(title, note=None):
    st.subheader(title)
    if note:
        st.markdown(f'<p class="section-note">{note}</p>', unsafe_allow_html=True)


def get_runs(dataset=None, status=None):
    query = "SELECT * FROM pipeline_runs WHERE 1=1"
    params = []

    if dataset and dataset != "All":
        query += " AND dataset = ?"
        params.append(dataset)

    if status and status != "All":
        query += " AND status = ?"
        params.append(status)

    query += " ORDER BY started_at DESC"
    return fetch_all(query, params)


# -------------------- OVERVIEW --------------------

def overview_page():
    hero(
        "ContractGuard Command Center",
        "Monitor data ingestion, quality checks, contract changes, and pipeline health.",
    )

    all_runs = get_runs()

    datasets = ["All", "customers", "orders"]
    statuses = ["All"] + sorted({r["status"] for r in all_runs if r.get("status")})

    f1, f2 = st.columns(2)
    with f1:
        selected_dataset = st.selectbox("Dataset filter", datasets)
    with f2:
        selected_status = st.selectbox("Run status filter", statuses)

    runs = get_runs(selected_dataset, selected_status)

    # Count only actual completed ingestion outcomes in these KPI totals.
    completed = [r for r in all_runs if r.get("status") == "success"]

    total_runs = len(all_runs)
    successful = sum(r.get("status") == "success" for r in all_runs)
    failed = sum(r.get("status") == "failed" for r in all_runs)
    rejected_schema = sum(
        r.get("status") == "rejected_schema" for r in all_runs
    )
    inserted = sum(r.get("inserted_rows", 0) for r in completed)
    updated = sum(r.get("updated_rows", 0) for r in completed)
    rejected = sum(r.get("rejected_rows", 0) for r in completed)
    late = sum(r.get("late_rows", 0) for r in completed)

    quarantine_total = fetch_one(
        "SELECT COUNT(*) AS n FROM quarantine"
    ).get("n", 0)

    st.markdown("### Key Performance Indicators")
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total Pipeline Runs", f"{total_runs:,}")
    k2.metric("Successful Runs", f"{successful:,}")
    k3.metric("Rows Inserted", f"{inserted:,}")
    k4.metric("Quarantined Records", f"{quarantine_total:,}")

    k5, k6, k7, k8 = st.columns(4)
    k5.metric("Rows Updated", f"{updated:,}")
    k6.metric("Rejected Rows", f"{rejected:,}")
    k7.metric("Late Arrivals", f"{late:,}")
    k8.metric("Failed / Schema Runs", f"{failed + rejected_schema:,}")

    st.divider()
    st.markdown("### Pipeline Analytics")
    st.caption(
        "Interactive charts based on recorded pipeline runs. "
        "Click chart elements or hover to inspect values."
    )

    # Explicit chart styling prevents dark plot backgrounds.
    chart_config = {
        "background": "#FFFFFF",
        "view": {"stroke": "#E4ECE7"},
        "axis": {
            "labelColor": "#52665B",
            "titleColor": "#344A3D",
            "gridColor": "#E8EFEA",
            "domainColor": "#C8D7CE",
            "tickColor": "#C8D7CE",
        },
        "legend": {
            "labelColor": "#40564B",
            "titleColor": "#344A3D",
        },
        "title": {"color": "#173D2E"},
    }

    left, right = st.columns(2)

    with left:
        section(
            "Pipeline Run Status",
            "Select a status segment to highlight it. Hover for exact counts.",
        )

        if all_runs:
            status_df = (
                pd.DataFrame(all_runs)
                .groupby("status")
                .size()
                .reset_index(name="Runs")
            )

            status_selection = alt.selection_point(
                fields=["status"],
                on="click",
                clear="dblclick",
                empty=True,
            )

            status_chart = (
                alt.Chart(status_df)
                .mark_arc(innerRadius=65, outerRadius=115, stroke="#FFFFFF")
                .encode(
                    theta=alt.Theta("Runs:Q", stack=True),
                    color=alt.condition(
                        status_selection,
                        alt.Color(
                            "status:N",
                            title="Run status",
                            scale=alt.Scale(
                                domain=[
                                    "success",
                                    "failed",
                                    "rejected_schema",
                                    "empty",
                                    "skipped_already_loaded",
                                    "running",
                                ],
                                range=[
                                    "#16845B",
                                    "#D9534F",
                                    "#E6A23C",
                                    "#6B9AC4",
                                    "#82958A",
                                    "#8064A2",
                                ],
                            ),
                        ),
                        alt.value("#DCE7E0"),
                    ),
                    tooltip=[
                        alt.Tooltip("status:N", title="Status"),
                        alt.Tooltip("Runs:Q", title="Number of runs"),
                    ],
                )
                .add_params(status_selection)
                .properties(height=300, background="#FFFFFF")
                .configure(**chart_config)
            )

            st.altair_chart(
                status_chart,
                use_container_width=True,
                theme=None,
            )
        else:
            st.info("No pipeline runs are available for this chart.")

    with right:
        section(
            "Data Processing Outcomes",
            "Cumulative row outcomes from successful runs only.",
        )

        outcome_df = pd.DataFrame({
            "Outcome": [
                "Inserted",
                "Updated",
                "Rejected",
                "Late arrivals",
                "Unchanged",
            ],
            "Rows": [
                inserted,
                updated,
                rejected,
                late,
                sum(r.get("unchanged_rows", 0) for r in completed),
            ],
        })

        outcome_selection = alt.selection_point(
            fields=["Outcome"],
            on="click",
            clear="dblclick",
            empty=True,
        )

        outcome_bars = (
            alt.Chart(outcome_df)
            .mark_bar(cornerRadiusEnd=6, height=24)
            .encode(
                x=alt.X(
                    "Rows:Q",
                    title="Number of rows",
                    scale=alt.Scale(domainMin=0),
                ),
                y=alt.Y(
                    "Outcome:N",
                    sort="-x",
                    title=None,
                ),
                color=alt.condition(
                    outcome_selection,
                    alt.Color(
                        "Outcome:N",
                        legend=None,
                        scale=alt.Scale(
                            domain=[
                                "Inserted",
                                "Updated",
                                "Rejected",
                                "Late arrivals",
                                "Unchanged",
                            ],
                            range=[
                                "#16845B",
                                "#3987C9",
                                "#D9534F",
                                "#E6A23C",
                                "#82958A",
                            ],
                        ),
                    ),
                    alt.value("#DCE7E0"),
                ),
                tooltip=[
                    alt.Tooltip("Outcome:N", title="Outcome"),
                    alt.Tooltip("Rows:Q", title="Rows", format=","),
                ],
            )
            .add_params(outcome_selection)
        )

        outcome_labels = (
            alt.Chart(outcome_df)
            .mark_text(
                align="left",
                baseline="middle",
                dx=6,
                color="#344A3D",
                fontWeight="bold",
            )
            .encode(
                x="Rows:Q",
                y=alt.Y("Outcome:N", sort="-x"),
                text=alt.Text("Rows:Q", format=","),
            )
        )

        outcome_chart = (
            (outcome_bars + outcome_labels)
            .properties(height=300, background="#FFFFFF")
            .configure(**chart_config)
        )

        st.altair_chart(
            outcome_chart,
            use_container_width=True,
            theme=None,
        )

    st.divider()
    section(
        "Pipeline Activity Trend",
        "Daily input volume. Drag across the chart to select a time range; hover for details.",
    )

    if all_runs:
        trend = pd.DataFrame(all_runs)
        trend["started_at"] = pd.to_datetime(
            trend["started_at"],
            errors="coerce",
            utc=True,
        )
        trend = trend.dropna(subset=["started_at"])

        if not trend.empty:
            trend["Date"] = trend["started_at"].dt.date
            daily = (
                trend.groupby("Date", as_index=False)["input_rows"]
                .sum()
                .rename(columns={"input_rows": "Input rows"})
            )

            date_brush = alt.selection_interval(encodings=["x"])

            activity_line = (
                alt.Chart(daily)
                .mark_area(
                    line={"color": "#16845B", "strokeWidth": 3},
                    color="#A8DCC2",
                    opacity=0.45,
                    point={"filled": True, "fill": "#16845B", "size": 65},
                )
                .encode(
                    x=alt.X(
                        "Date:T",
                        title="Run date",
                        axis=alt.Axis(format="%d %b", labelAngle=0),
                    ),
                    y=alt.Y(
                        "Input rows:Q",
                        title="Input rows",
                        scale=alt.Scale(zero=True),
                    ),
                    tooltip=[
                        alt.Tooltip("Date:T", title="Date"),
                        alt.Tooltip(
                            "Input rows:Q",
                            title="Input rows",
                            format=",",
                        ),
                    ],
                )
                .add_params(date_brush)
                .properties(height=280, background="#FFFFFF")
                .configure(**chart_config)
            )

            st.altair_chart(
                activity_line,
                use_container_width=True,
                theme=None,
            )
        else:
            st.info("No valid timestamps are available for the activity trend.")
    else:
        st.info("Run the pipeline to populate the activity trend.")

    st.divider()
    section("Recent Pipeline Runs", "Use the filters above to narrow this table.")
    if runs:
        frame = pd.DataFrame(runs)
        columns = [
            "run_id", "dataset", "contract_version", "status",
            "input_rows", "inserted_rows", "updated_rows",
            "rejected_rows", "started_at",
        ]
        st.dataframe(
            frame[[c for c in columns if c in frame.columns]],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No runs match the selected filters.")

    st.caption(f"Connected database: {DB_PATH}")


# -------------------- VALIDATION AND INGESTION --------------------


def validation_page():
    hero(
        "Data Validation & Ingestion",
        "Validate producer files, understand data quality, and safely run ingestion.",
    )

    dataset = st.selectbox("Choose dataset", ["customers", "orders"])
    versions = [1, 2] if dataset == "orders" else [1]
    version = st.selectbox("Contract version", versions)

    contract_path = CONTRACTS_DIR / f"{dataset}_v{version}.yaml"
    if not contract_path.is_file():
        st.error(f"Contract not found: {contract_path}")
        return

    uploaded = st.file_uploader("Upload producer CSV", type=["csv"])
    if uploaded is None:
        st.info("Upload a CSV file to begin validation.")
        return

    file_bytes = uploaded.getvalue()
    safe_name = Path(uploaded.name).name

    st.success(f"File selected: {safe_name}")

    if not file_bytes:
        st.warning("The uploaded file is empty.")

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            preview_path = Path(temp_dir) / safe_name
            preview_path.write_bytes(file_bytes)
            preview_source = read_csv_file(preview_path)

            section("File Information")
            a, b, c = st.columns(3)
            a.metric("File Size", f"{len(file_bytes):,} bytes")
            b.metric("CSV Columns", len(preview_source.header))
            c.metric("Data Rows", preview_source.total_records)

            st.caption("Detected columns")
            st.code("  |  ".join(preview_source.header))

            if preview_source.rows:
                preview_df = pd.DataFrame(
                    preview_source.rows[:8],
                    columns=preview_source.header,
                )
                section("Data Preview", "First eight valid CSV records.")
                st.dataframe(
                    preview_df,
                    use_container_width=True,
                    hide_index=True,
                )

    except Exception as exc:
        st.error(f"Could not read uploaded CSV: {exc}")
        return

    st.divider()
    validate_clicked = st.button(
        "Validate File",
        type="primary",
        use_container_width=True,
    )

    if validate_clicked:
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir) / safe_name
                temp_path.write_bytes(file_bytes)

                source_file = read_csv_file(temp_path)
                contract = load_contract(contract_path)
                result = validate_batch(
                    contract,
                    source_file.header,
                    source_file.rows,
                )

                report = {
                    "dataset": dataset,
                    "contract_version": version,
                    "file_name": safe_name,
                    "status": result.status,
                    "input_rows": result.input_rows,
                    "accepted_rows": result.accepted_count,
                    "rejected_rows": result.rejected_count,
                    "malformed_rows": [
                        {
                            "line_number": item.line_number,
                            "raw": item.raw,
                            "reason": item.reason,
                        }
                        for item in source_file.malformed
                    ],
                    "schema_issues": [
                        asdict(item) for item in result.issues
                    ],
                    "reject_reasons": result.reason_counts(),
                    "rejected_records": [
                        {
                            "row_number": item.row_number,
                            "raw": item.raw,
                            "reasons": [
                                asdict(reason) for reason in item.reasons
                            ],
                        }
                        for item in result.rejected
                    ],
                }

                st.session_state["validation_report"] = report

        except Exception as exc:
            st.error(f"Validation failed: {exc}")
            st.exception(exc)

    report = st.session_state.get("validation_report")

    if report and report.get("file_name") == safe_name:
        st.divider()
        section("Validation Summary")

        accepted = report["accepted_rows"]
        rejected = report["rejected_rows"]
        malformed = len(report["malformed_rows"])
        input_rows = report["input_rows"]

        # This score describes accepted valid records, not overall business quality.
        validation_rate = (
            accepted / input_rows * 100 if input_rows else 0
        )

        x1, x2, x3, x4 = st.columns(4)
        x1.metric("Input Records", f"{input_rows:,}")
        x2.metric("Accepted", f"{accepted:,}")
        x3.metric("Rejected", f"{rejected:,}")
        x4.metric("Malformed CSV Rows", f"{malformed:,}")

        st.metric(
            "Validation Acceptance Rate",
            f"{validation_rate:.1f}%",
            help="Accepted records divided by input records returned by the validator.",
        )
        st.progress(validation_rate / 100 if input_rows else 0)

        if report["status"] == "valid" and not malformed:
            st.success("The file passed contract validation.")
        else:
            st.warning(
                f"Validation status: {report['status']}. "
                "Review the issues below."
            )

        quality_df = pd.DataFrame({
            "Result": ["Accepted", "Rejected"],
            "Records": [accepted, rejected],
        })

        section("Accepted vs Rejected Records")
        quality_chart = (
            alt.Chart(quality_df)
            .mark_bar(cornerRadiusEnd=7)
            .encode(
                x=alt.X("Records:Q", title="Number of records"),
                y=alt.Y("Result:N", sort="-x", title=None),
                color=alt.Color(
                    "Result:N",
                    scale=alt.Scale(
                        domain=["Accepted", "Rejected"],
                        range=["#16845B", "#D9534F"],
                    ),
                    legend=None,
                ),
                tooltip=["Result:N", alt.Tooltip("Records:Q", format=",")],
            )
            .properties(height=160, background="#FFFFFF")
            .configure(
                background="#FFFFFF",
                view={"stroke": "#E4ECE7"},
                axis={
                    "labelColor": "#52665B",
                    "titleColor": "#344A3D",
                    "gridColor": "#E8EFEA",
                },
            )
        )
        st.altair_chart(
            quality_chart,
            use_container_width=True,
            theme=None,
        )

        reasons = report["reject_reasons"]
        if reasons:
            section("Rejection Reasons")
            reason_df = pd.DataFrame(
                [
                    {"Reason": str(key), "Records": int(value)}
                    for key, value in reasons.items()
                ]
            ).sort_values("Records", ascending=False)

            reason_chart = (
                alt.Chart(reason_df)
                .mark_bar(cornerRadiusEnd=6, color="#D9534F")
                .encode(
                    x=alt.X("Records:Q", title="Number of occurrences"),
                    y=alt.Y("Reason:N", sort="-x", title=None),
                    tooltip=["Reason:N", "Records:Q"],
                )
                .properties(height=max(180, len(reason_df) * 38))
                .configure(
                    background="#FFFFFF",
                    view={"stroke": "#E4ECE7"},
                    axis={
                        "labelColor": "#52665B",
                        "titleColor": "#344A3D",
                        "gridColor": "#E8EFEA",
                    },
                )
            )
            st.altair_chart(
                reason_chart,
                use_container_width=True,
                theme=None,
            )

        if report["schema_issues"]:
            with st.expander("Inspect schema issues"):
                st.json(report["schema_issues"])

        if report["rejected_records"]:
            with st.expander("Inspect rejected records"):
                st.json(report["rejected_records"])

        if report["malformed_rows"]:
            with st.expander("Inspect malformed CSV rows"):
                st.dataframe(
                    pd.DataFrame(report["malformed_rows"]),
                    use_container_width=True,
                )

        st.download_button(
            "Download Validation Report",
            json.dumps(report, indent=2, default=str),
            file_name="contractguard_validation_report.json",
            mime="application/json",
        )

    st.divider()
    section("Execute Ingestion")
    st.caption(
        "This invokes the existing ingestion pipeline and writes its results "
        "to the configured SQLite database."
    )

    if st.button("Run Ingestion Pipeline", type="primary"):
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir) / safe_name
                temp_path.write_bytes(file_bytes)

                conn = get_connection()
                try:
                    result = run_pipeline(
                        conn,
                        str(CONTRACTS_DIR),
                        dataset,
                        str(temp_path),
                        version,
                    )
                finally:
                    conn.close()

            result_data = as_dict(result)
            st.session_state["last_run_result"] = result_data
            st.success("Pipeline execution finished.")
            st.json(result_data)

            if getattr(result, "reconciles", False):
                st.success("Input row reconciliation passed.")
            else:
                st.warning("Review the pipeline reconciliation result.")

        except Exception as exc:
            st.error(f"Pipeline execution failed: {exc}")
            st.exception(exc)



def comparison_page():
    hero(
        "Contract Compatibility Center",
        "Understand schema evolution and identify changes that may affect producers and consumers.",
    )

    old_path = CONTRACTS_DIR / "orders_v1.yaml"
    new_path = CONTRACTS_DIR / "orders_v2.yaml"

    if not old_path.is_file() or not new_path.is_file():
        st.error("Orders v1 or v2 contract file is missing.")
        return

    st.markdown("**Contract comparison:** Orders v1 ? Orders v2")
    st.caption(
        "The existing compatibility engine determines the severity "
        "and meaning of each detected change."
    )

    if st.button("Analyze Contract Changes", type="primary"):
        try:
            result = compare_files(str(old_path), str(new_path))
            st.session_state["compatibility_report"] = result.to_dict()
        except Exception as exc:
            st.error(f"Contract comparison failed: {exc}")
            st.exception(exc)

    report = st.session_state.get("compatibility_report")
    if not report:
        st.info("Select Analyze Contract Changes to generate the report.")
        return

    st.divider()
    section("Compatibility Verdict")

    breaking = report["breaking_count"]
    non_breaking = report["non_breaking_count"]
    verdict = report["verdict"]

    if verdict == "BREAKING":
        st.error("BREAKING CHANGE ? Existing producers or consumers may need updates.")
    elif verdict == "NON_BREAKING":
        st.success("NON-BREAKING ? Only non-breaking changes were detected.")
    else:
        st.success("NO CHANGE ? No contract differences were detected.")

    a, b, c = st.columns(3)
    a.metric("Breaking Changes", breaking)
    b.metric("Non-Breaking Changes", non_breaking)
    c.metric("Total Changes", len(report["changes"]))

    if report["changes"]:
        section("Change Severity Distribution")

        severity_df = pd.DataFrame([
            {"Severity": "Breaking", "Changes": breaking},
            {"Severity": "Non-breaking", "Changes": non_breaking},
        ])

        severity_chart = (
            alt.Chart(severity_df)
            .mark_bar(cornerRadiusEnd=7)
            .encode(
                x=alt.X("Changes:Q", title="Number of changes"),
                y=alt.Y("Severity:N", sort="-x", title=None),
                color=alt.Color(
                    "Severity:N",
                    scale=alt.Scale(
                        domain=["Breaking", "Non-breaking"],
                        range=["#D9534F", "#16845B"],
                    ),
                    legend=None,
                ),
                tooltip=["Severity:N", "Changes:Q"],
            )
            .properties(height=140)
            .configure(
                background="#FFFFFF",
                view={"stroke": "#E4ECE7"},
                axis={
                    "labelColor": "#52665B",
                    "titleColor": "#344A3D",
                    "gridColor": "#E8EFEA",
                },
            )
        )
        st.altair_chart(
            severity_chart,
            use_container_width=True,
            theme=None,
        )

        changes_df = pd.DataFrame(report["changes"])
        changes_df["column"] = changes_df["column"].fillna("(contract level)")

        section("Detailed Change Register")

        severity_filter = st.multiselect(
            "Filter by severity",
            ["BREAKING", "NON_BREAKING"],
            default=["BREAKING", "NON_BREAKING"],
        )

        filtered = changes_df[
            changes_df["severity"].isin(severity_filter)
        ]

        st.dataframe(
            filtered.rename(columns={
                "severity": "Severity",
                "code": "Change Code",
                "column": "Affected Column",
                "detail": "Explanation",
            }),
            use_container_width=True,
            hide_index=True,
        )

        st.download_button(
            "Download Compatibility Report",
            json.dumps(report, indent=2, default=str),
            file_name="contract_compatibility_report.json",
            mime="application/json",
        )
    else:
        st.info("The compatibility engine found no changes.")



def quarantine_page():
    hero(
        "Quarantine & Rejection Center",
        "Investigate rejected records, identify recurring quality issues, and trace failures to pipeline runs.",
    )

    datasets = fetch_all(
        "SELECT DISTINCT dataset FROM quarantine ORDER BY dataset"
    )
    dataset_options = ["All"] + [x["dataset"] for x in datasets]

    reasons = fetch_all(
        "SELECT DISTINCT reason_codes FROM quarantine "
        "ORDER BY reason_codes"
    )
    reason_options = ["All"] + [
        x["reason_codes"] for x in reasons if x["reason_codes"]
    ]

    run_options = fetch_all(
        "SELECT DISTINCT run_id FROM quarantine ORDER BY run_id DESC"
    )
    run_ids = ["All"] + [x["run_id"] for x in run_options]

    f1, f2, f3 = st.columns(3)
    with f1:
        selected_dataset = st.selectbox("Dataset", dataset_options)
    with f2:
        selected_reason = st.selectbox("Rejection reason", reason_options)
    with f3:
        selected_run = st.selectbox("Pipeline run", run_ids)

    query = "SELECT * FROM quarantine WHERE 1=1"
    params = []

    if selected_dataset != "All":
        query += " AND dataset = ?"
        params.append(selected_dataset)

    if selected_reason != "All":
        query += " AND reason_codes = ?"
        params.append(selected_reason)

    if selected_run != "All":
        query += " AND run_id = ?"
        params.append(selected_run)

    query += " ORDER BY quarantined_at DESC"
    records = fetch_all(query, params)

    st.divider()
    section("Quarantine Metrics")

    a, b, c = st.columns(3)
    a.metric("Matching Rejected Records", f"{len(records):,}")
    b.metric(
        "Distinct Reason Codes",
        len({r.get("reason_codes") for r in records}),
    )
    c.metric(
        "Affected Pipeline Runs",
        len({r.get("run_id") for r in records}),
    )

    if records:
        frame = pd.DataFrame(records)

        left, right = st.columns(2)

        with left:
            section("Rejections by Reason")
            reason_df = (
                frame.groupby("reason_codes")
                .size()
                .reset_index(name="Records")
                .sort_values("Records", ascending=False)
            )

            reason_chart = (
                alt.Chart(reason_df)
                .mark_bar(cornerRadiusEnd=6, color="#D9534F")
                .encode(
                    x=alt.X("Records:Q", title="Rejected records"),
                    y=alt.Y("reason_codes:N", sort="-x", title=None),
                    tooltip=["reason_codes:N", "Records:Q"],
                )
                .properties(height=max(180, len(reason_df) * 35))
                .configure(
                    background="#FFFFFF",
                    view={"stroke": "#E4ECE7"},
                    axis={
                        "labelColor": "#52665B",
                        "titleColor": "#344A3D",
                        "gridColor": "#E8EFEA",
                    },
                )
            )
            st.altair_chart(
                reason_chart,
                use_container_width=True,
                theme=None,
            )

        with right:
            section("Rejections by Dataset")
            dataset_df = (
                frame.groupby("dataset")
                .size()
                .reset_index(name="Records")
            )

            dataset_chart = (
                alt.Chart(dataset_df)
                .mark_arc(innerRadius=55, stroke="#FFFFFF")
                .encode(
                    theta=alt.Theta("Records:Q"),
                    color=alt.Color(
                        "dataset:N",
                        scale=alt.Scale(
                            range=["#16845B", "#3987C9", "#E6A23C"]
                        ),
                    ),
                    tooltip=["dataset:N", "Records:Q"],
                )
                .properties(height=240)
                .configure(
                    background="#FFFFFF",
                    view={"stroke": "#E4ECE7"},
                    legend={
                        "labelColor": "#40564B",
                        "titleColor": "#344A3D",
                    },
                )
            )
            st.altair_chart(
                dataset_chart,
                use_container_width=True,
                theme=None,
            )

        st.divider()
        section("Rejected Record Register")

        search = st.text_input(
            "Search rejected records",
            placeholder="Search row number, file, reason, or run ID...",
        )

        display_frame = frame.copy()
        if search:
            mask = display_frame.astype(str).apply(
                lambda col: col.str.contains(
                    search,
                    case=False,
                    na=False,
                    regex=False,
                )
            ).any(axis=1)
            display_frame = display_frame[mask]

        st.dataframe(
            display_frame,
            use_container_width=True,
            hide_index=True,
        )

        with st.expander("Inspect raw rejected records"):
            for record in records[:100]:
                st.markdown(
                    f"**{record['dataset']} ? Row {record['row_number']}**"
                )
                st.caption(
                    f"Reason: {record['reason_codes']} | "
                    f"Run: {record['run_id']}"
                )
                st.code(record["raw_record"], language="json")
                st.caption(record["reason_detail"])

        st.download_button(
            "Download Filtered Quarantine Report",
            json.dumps(records, indent=2, default=str),
            file_name="contractguard_quarantine_report.json",
            mime="application/json",
        )
    else:
        st.success("No quarantined records match the selected filters.")

    st.divider()
    section("Pipeline Run History")

    runs = fetch_all(
        "SELECT * FROM pipeline_runs ORDER BY started_at DESC"
    )

    if runs:
        st.dataframe(
            pd.DataFrame(runs),
            use_container_width=True,
            hide_index=True,
        )

        st.download_button(
            "Download Pipeline Run Report",
            json.dumps(runs, indent=2, default=str),
            file_name="contractguard_pipeline_runs.json",
            mime="application/json",
        )
    else:
        st.info("No pipeline runs are recorded yet.")



def data_explorer_page():
    hero(
        "Clean Data Explorer",
        "Explore ingested customer, order, and order-history records stored in SQLite.",
    )

    table_map = {
        "Customers": "customers_clean",
        "Orders": "orders_clean",
        "Order History": "order_history",
    }

    choice = st.selectbox("Select data table", list(table_map))
    table_name = table_map[choice]

    conn = get_connection()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) FROM {table_name}"
        ).fetchone()[0]

        columns = [
            row["name"]
            for row in conn.execute(
                f"PRAGMA table_info({table_name})"
            ).fetchall()
        ]

        records = [
            dict(row)
            for row in conn.execute(
                f"SELECT * FROM {table_name} ORDER BY rowid DESC LIMIT 1000"
            ).fetchall()
        ]
    finally:
        conn.close()

    st.divider()
    section("Table Overview")

    a, b, c = st.columns(3)
    a.metric("Total Database Records", f"{total:,}")
    b.metric("Columns", len(columns))
    c.metric("Records Loaded for Exploration", f"{len(records):,}")

    if not records:
        st.info(
            "This table currently contains no records. Run the ingestion "
            "pipeline to populate it."
        )
        return

    frame = pd.DataFrame(records)

    st.divider()
    section("Search and Filter")

    search = st.text_input(
        "Search across all columns",
        placeholder="Enter an ID, name, email, status, or value...",
    )

    filter_column = st.selectbox(
        "Optional column filter",
        ["No filter"] + columns,
    )

    filtered = frame.copy()

    if search:
        mask = filtered.astype(str).apply(
            lambda col: col.str.contains(
                search,
                case=False,
                na=False,
                regex=False,
            )
        ).any(axis=1)
        filtered = filtered[mask]

    if filter_column != "No filter":
        values = sorted(
            filtered[filter_column].dropna().astype(str).unique().tolist()
        )

        if values and len(values) <= 100:
            selected_value = st.selectbox(
                f"Filter {filter_column}",
                ["All values"] + values,
            )
            if selected_value != "All values":
                filtered = filtered[
                    filtered[filter_column].astype(str) == selected_value
                ]
        else:
            filter_text = st.text_input(
                f"Search within {filter_column}"
            )
            if filter_text:
                filtered = filtered[
                    filtered[filter_column].astype(str).str.contains(
                        filter_text,
                        case=False,
                        na=False,
                        regex=False,
                    )
                ]

    st.metric("Records Matching Filters", f"{len(filtered):,}")

    st.divider()
    section("Record Explorer")

    st.dataframe(
        filtered,
        use_container_width=True,
        hide_index=True,
    )

    st.download_button(
        "Export Filtered Records as CSV",
        filtered.to_csv(index=False),
        file_name=f"{table_name}_export.csv",
        mime="text/csv",
    )

    st.divider()
    section("Column Distribution")

    selected_column = st.selectbox(
        "Choose a column to visualize",
        columns,
        key="distribution_column",
    )

    if not filtered.empty:
        series = filtered[selected_column]

        if pd.api.types.is_numeric_dtype(series):
            numeric = pd.to_numeric(series, errors="coerce").dropna()

            if not numeric.empty:
                numeric_df = pd.DataFrame({
                    "Value": numeric,
                })

                distribution = (
                    alt.Chart(numeric_df)
                    .mark_bar(color="#16845B")
                    .encode(
                        x=alt.X(
                            "Value:Q",
                            bin=alt.Bin(maxbins=20),
                            title=selected_column,
                        ),
                        y=alt.Y("count():Q", title="Records"),
                        tooltip=["count():Q"],
                    )
                    .properties(height=280)
                )

                st.altair_chart(
                    distribution.configure(
                        background="#FFFFFF",
                        view={"stroke": "#E4ECE7"},
                        axis={
                            "labelColor": "#52665B",
                            "titleColor": "#344A3D",
                            "gridColor": "#E8EFEA",
                        },
                    ),
                    use_container_width=True,
                    theme=None,
                )
            else:
                st.info("No numeric values are available for this column.")
        else:
            category_df = (
                series.fillna("(empty)")
                .astype(str)
                .value_counts()
                .head(15)
                .rename_axis("Category")
                .reset_index(name="Records")
            )

            category_chart = (
                alt.Chart(category_df)
                .mark_bar(cornerRadiusEnd=6, color="#3987C9")
                .encode(
                    x=alt.X("Records:Q", title="Records"),
                    y=alt.Y("Category:N", sort="-x", title=None),
                    tooltip=["Category:N", "Records:Q"],
                )
                .properties(height=max(180, len(category_df) * 30))
                .configure(
                    background="#FFFFFF",
                    view={"stroke": "#E4ECE7"},
                    axis={
                        "labelColor": "#52665B",
                        "titleColor": "#344A3D",
                        "gridColor": "#E8EFEA",
                    },
                )
            )

            st.altair_chart(
                category_chart,
                use_container_width=True,
                theme=None,
            )


def main():
    with st.sidebar:
        st.markdown("# 🛡️ ContractGuard")
        st.caption("DATA QUALITY PLATFORM")
        st.divider()

        page = st.radio(
            "MAIN MENU",
            [
                "Dashboard",
                "Data Validation",
                "Contract Comparison",
                "Quarantine Center",
                "Clean Data Explorer",
            ],
        )

        st.divider()
        st.caption("SYSTEM")
        st.markdown("**Storage:** SQLite")
        st.markdown("**Validation:** Data Contracts")
        st.markdown("**Processing:** Python Pipeline")
        st.divider()
        st.caption("ContractGuard • Data Engineering")

    if page == "Dashboard":
        overview_page()
    elif page == "Data Validation":
        validation_page()
    elif page == "Contract Comparison":
        comparison_page()
    elif page == "Quarantine Center":
        quarantine_page()
    else:
        data_explorer_page()


if __name__ == "__main__":
    main()
