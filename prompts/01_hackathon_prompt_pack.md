# ContractGuard — Hackathon AI Prompt Pack

## Purpose

This document records the AI-assisted planning, implementation, testing, debugging, and documentation workflow used for ContractGuard.

The prompts below are reconstructed and organized from the project development process. They represent the tasks and instructions used during development; they are not guaranteed to be verbatim copies of every original chat message.

---

## 1. Problem Understanding and Solution Planning

### Prompt 1.1 — Analyze the hackathon problem

Act as a senior data engineering architect. Analyze the hackathon problem statement and identify the business problem, users, expected outcomes, functional requirements, non-functional requirements, constraints, and evaluation criteria.

Propose a practical solution that demonstrates reliable data ingestion, schema validation, data quality, pipeline execution, and traceability.

### Prompt 1.2 — Define the solution

Design a project named ContractGuard that validates producer data against versioned data contracts before accepting it into a clean data layer.

Include:
- Business problem and proposed solution.
- Main features and user workflows.
- System architecture.
- Technology stack.
- Data flow from CSV input to validated output.
- Error handling and auditability.
- Assumptions, limitations, and future improvements.

Keep the solution achievable for a hackathon while demonstrating production-oriented engineering practices.

---

## 2. Architecture and Project Structure

### Prompt 2.1 — Design a layered architecture

Design a modular Python application using separate layers for:
- Presentation.
- Application and pipeline orchestration.
- Contract validation and compatibility.
- Data access and persistence.

Define the responsibilities of each layer and how they communicate.

Avoid unnecessary coupling and keep the code testable and maintainable.

### Prompt 2.2 — Create a maintainable project structure

Suggest a clean Python package structure for ContractGuard, including source code, contracts, sample data, generated data, reports, tests, documentation, and dashboard files.

Explain the responsibility of each important module.

---

## 3. Data Contracts and Schema Validation

### Prompt 3.1 — Define customer data contract

Create a YAML data contract for customer records.

Include:
- Required and optional fields.
- Data types.
- Nullability.
- Field constraints.
- Regular-expression patterns.
- Allowed values.
- Clear validation error messages.

### Prompt 3.2 — Define orders data contract

Create a YAML contract for order records with identifiers, customer references, timestamps, order status, quantity, price, currency, and optional coupon information.

Include type checks, required-field validation, allowed values, range checks, and referential integrity requirements.

### Prompt 3.3 — Support contract versioning

Design versioned contracts for orders.

Demonstrate compatibility checks between versions, including added or removed fields, changed constraints, and changes that could break existing producers.

Explain which changes are backward-compatible and which require migration or rejection.

### Prompt 3.4 — Implement validation

Implement reusable Python validation logic that:
- Loads YAML contracts.
- Checks CSV headers and records.
- Validates data types and required fields.
- Checks patterns, allowed values, ranges, and references.
- Separates valid records from rejected records.
- Produces understandable validation reports.

Handle malformed records without crashing the entire application.

---

## 4. Synthetic Data Generation

### Prompt 4.1 — Build a reproducible dataset generator

Create a Python generator that produces realistic synthetic customer and order CSV datasets.

Requirements:
- Reproducible output using a fixed random seed.
- Configurable record counts.
- Support large datasets.
- Generate valid and invalid records.
- Generate multiple batches.
- Keep expected results separate from input files.

### Prompt 4.2 — Generate edge cases

Include test scenarios for:
- Empty files.
- Header-only files.
- Malformed CSV rows.
- Extra columns.
- Missing required columns.
- Invalid data types.
- Out-of-range values.
- Null required fields.
- Invalid identifiers.
- Invalid customer references.
- Duplicate records.
- Conflicting duplicate keys.
- Updates and late-arriving records.
- Repeated batch submissions.

Generate expected results independently so that the pipeline can be checked against a known answer.

---

## 5. Data Pipeline and Database

### Prompt 5.1 — Implement pipeline orchestration

Build a Python pipeline that reads producer CSV files, validates them against contracts, separates accepted and rejected records, and writes clean records to a database.

Track:
- Run ID.
- Dataset name.
- Contract version.
- Source file.
- File hash.
- Start and completion timestamps.
- Pipeline status.
- Input, accepted, and rejected record counts.
- Failure reason.

### Prompt 5.2 — Implement database persistence

Design a SQLite schema for:
- Contract versions.
- Pipeline runs.
- Clean customer records.
- Clean order records.
- Order history.
- Quarantined records.

Include appropriate primary keys, foreign keys, constraints, and indexes.

### Prompt 5.3 — Add idempotency and history

Implement idempotent processing so that resubmitting the same source file does not create duplicate results.

Support updates, late-arriving records, and historical tracking of order changes.

### Prompt 5.4 — Handle failures safely

Implement transaction handling and rollback behavior.

Ensure that failed processing does not leave partially committed database changes. Record failure information and make pipeline runs auditable.

---

## 6. Frontend and Dashboard

### Prompt 6.1 — Build the Streamlit dashboard

Create a clean, professional Streamlit dashboard for ContractGuard.

Include:
- Overview and pipeline metrics.
- Data validation and CSV upload.
- Contract comparison.
- Validation errors and quarantined records.
- Pipeline run history.
- Reports and record counts.

Use clear navigation, readable tables, status indicators, helpful messages, and a consistent visual design.

### Prompt 6.2 — Improve user experience

Improve the dashboard UX with a professional layout, consistent spacing, useful metrics, clear error messages, and understandable workflows.

Make it suitable for a technical hackathon demonstration.

### Prompt 6.3 — Add dashboard evidence

Prepare screenshots showing the major ContractGuard dashboard features and organize them in a dedicated dashboard folder for project documentation and review.

---

## 7. Testing and Debugging

### Prompt 7.1 — Create automated tests

Create pytest tests for:
- Contract loading.
- Contract validation.
- Contract compatibility.
- CSV reading.
- Database operations.
- Pipeline execution.
- Duplicate detection.
- Idempotency.
- Quarantine handling.
- Error handling.
- Dataset generation.

Cover successful cases, malformed input, empty input, and domain-specific edge cases.

### Prompt 7.2 — Debug errors systematically

When an error occurs:
1. Identify the exact failing module and function.
2. Explain the root cause.
3. Propose the smallest safe correction.
4. Preserve existing functionality.
5. Add or update a regression test.
6. Rerun the relevant tests.

Do not silently remove existing features to make tests pass.

### Prompt 7.3 — Verify the complete application

Run the automated test suite and verify the CLI, dashboard, database integrity, and sample pipeline execution.

Check that reports and expected results are consistent with actual processing outcomes.

---

## 8. Documentation and Final Review

### Prompt 8.1 — Prepare project documentation

Write a README explaining:
- Problem statement.
- Solution overview.
- Architecture.
- Technology stack.
- Project structure.
- Installation.
- Execution instructions.
- Dashboard usage.
- Sample datasets.
- Validation and pipeline behavior.
- Testing.
- Known limitations.
- Future improvements.

### Prompt 8.2 — Perform final hackathon audit

Act as a hackathon evaluator and review ContractGuard against the supplied problem statement and evaluation criteria.

Check:
- Functional completeness.
- Architecture and code quality.
- Data validation and quality.
- Error handling and recovery.
- Reproducibility.
- Testing coverage.
- Performance evidence.
- Documentation.
- GitHub repository readiness.

Identify missing evidence and risks. Do not claim a requirement is complete unless it has been verified.

---

## 9. AI Assistance and Human Verification

AI assistance was used as a development aid for planning, architecture, code implementation guidance, debugging, testing suggestions, and documentation.

AI-generated suggestions were treated as proposals rather than automatically trusted outputs.

Project verification included:
- Running automated tests.
- Executing the CLI.
- Launching the Streamlit dashboard.
- Checking database integrity.
- Reviewing sample pipeline outputs.
- Inspecting repository files and Git status.

The developer remains responsible for reviewing, understanding, testing, and explaining the submitted implementation.

---

## 10. Development Principles

The development process followed these principles:

1. Keep modules separated by responsibility.
2. Use versioned data contracts.
3. Validate data before database insertion.
4. Preserve rejected records for investigation.
5. Track pipeline execution and outcomes.
6. Support reproducible test datasets.
7. Protect database consistency through transactions.
8. Test edge cases as well as successful workflows.
9. Document assumptions and limitations.
10. Verify implementation claims before submission.
