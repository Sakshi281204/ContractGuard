# ContractGuard
### Contract-Driven Data Quality & Schema Evolution Platform

ContractGuard is a Python-based data engineering platform designed to validate, process, and monitor structured CSV data using versioned data contracts.

It helps identify schema mismatches, validate records, quarantine invalid data, detect breaking contract changes, and maintain reliable data ingestion through SQLite.

## Key Features

- **Data Contract Validation:** Validate CSV files against YAML-based schemas.
- **Schema Evolution:** Compare contract versions and identify breaking and non-breaking changes.
- **Data Ingestion:** Process valid records through a structured Python pipeline.
- **Data Quality Checks:** Detect missing values, invalid types, duplicate records, and constraint violations.
- **Quarantine Management:** Store rejected records with traceable rejection reasons.
- **SQLite Storage:** Maintain structured data, pipeline history, and quarantine records.
- **Idempotent Processing:** Prevent duplicate processing and duplicate records.
- **Data History:** Preserve historical records when newer versions arrive.
- **Interactive Dashboard:** Monitor validation, compatibility, ingestion, and rejected records.
- **Automated Testing:** Comprehensive pytest test suite.

## Technology Stack

| Technology | Purpose |
|---|---|
| Python | Core application |
| Pandas | Data processing |
| NumPy | Numerical operations |
| PyYAML | YAML contract loading |
| SQLite | Persistent data storage |
| Streamlit | Interactive dashboard |
| Pytest | Automated testing |

## Application Architecture

ContractGuard follows a layered architecture.

```text
              USER
               |
       +-------+--------+
       |                |
   Streamlit UI       CLI
       |                |
       +-------+--------+
               |
       APPLICATION LAYER
       |
       +-- Contract Loader
       +-- Data Validator
       +-- Ingestion Pipeline
       +-- Compatibility Checker
       +-- Data Generator
               |
       DATA ACCESS LAYER
       |
       +-- CSV Reader
       +-- SQLite Database
       +-- Repository
               |
       +-------+--------+
       |                |
   YAML Contracts    SQLite DB
       |
   CSV Data Files
```

## Dashboard Modules

The Streamlit dashboard provides five main sections:

| Module | Functionality |
|---|---|
| Dashboard | Monitor pipeline activity and data quality metrics |
| Data Validation | Upload CSV files and inspect validation results |
| Contract Comparison | Compare schema versions and assess compatibility |
| Quarantine Center | Review rejected records and rejection reasons |
| Clean Data Explorer | Explore stored datasets and records |

## Project Structure

```text
contractguard/
|
|-- contracts/
|   |-- customers_v1.yaml
|   |-- orders_v1.yaml
|   |-- orders_v2.yaml
|
|-- data/
|   |-- sample/
|   |   |-- input/
|   |   |-- expected/
|   |
|   |-- generated/
|       |-- input/
|       |-- expected/
|
|-- prompts/
|-- reports/
|
|-- src/
|   |-- contractguard/
|       |-- application/
|       |   |-- compatibility.py
|       |   |-- contract_loader.py
|       |   |-- generator.py
|       |   |-- pipeline.py
|       |   |-- validator.py
|       |
|       |-- data_access/
|       |   |-- db.py
|       |   |-- reader.py
|       |   |-- repository.py
|       |
|       |-- presentation/
|           |-- app.py
|           |-- cli.py
|           |-- __main__.py
|
|-- tests/
|   |-- test_compatibility.py
|   |-- test_contract_loader.py
|   |-- test_database.py
|   |-- test_generator.py
|   |-- test_pipeline.py
|   |-- test_reader.py
|   |-- test_repository.py
|   |-- test_validator.py
|
|-- requirements.txt
|-- pytest.ini
|-- README.md
```

## Installation

### 1. Clone the repository

```bash
git clone <YOUR_GITHUB_REPOSITORY_URL>
cd contractguard
```

### 2. Create a virtual environment

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```bash
python -m pip install -r requirements.txt
```

## Run the Dashboard

Start the Streamlit application:

```bash
python -m streamlit run src/contractguard/presentation/app.py
```

Open the local URL displayed in your terminal.

## Run Automated Tests

Execute the complete test suite:

```bash
python -m pytest -v
```

Collect tests without executing them:

```bash
python -m pytest --collect-only -q
```

The project currently contains **155 automated tests** covering validation, contract loading, compatibility, database operations, CSV reading, repositories, data generation, and pipeline processing.

## Data Contracts

Data contracts are defined using YAML files.

The project includes:

- `customers_v1.yaml`
- `orders_v1.yaml`
- `orders_v2.yaml`

These contracts define the expected structure and validation rules for incoming datasets.

Contract comparison helps identify schema changes that could affect downstream data consumers.

## Data Quality and Reliability

ContractGuard includes mechanisms for:

- Schema mismatch detection.
- Type and constraint validation.
- Duplicate detection.
- Invalid reference detection.
- Malformed CSV record handling.
- Rejection reason tracking.
- Idempotent ingestion.
- Historical record preservation.
- Transaction rollback and retry handling.

## Sample Data

The repository includes sample and generated CSV datasets for testing different ingestion scenarios, including:

- Valid customer and order records.
- Empty files.
- Header-only files.
- Missing required columns.
- Unexpected columns.
- Different contract versions.

Expected results are maintained separately from input data.

## Future Enhancements

- REST API integration.
- Cloud database support.
- Automated data quality alerts.
- Role-based dashboard access.
- Scheduled ingestion.
- Advanced data quality analytics.

## Author

Developed as a Data Engineering portfolio project.

**Project:** ContractGuard
**Domain:** Data Engineering | Data Quality | Schema Evolution
**Language:** Python

---
