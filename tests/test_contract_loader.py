import copy
from pathlib import Path

import pytest
import yaml

from contractguard.application.contract_loader import (
    ContractError,
    load_contract,
    load_latest_contract,
)

CONTRACTS_DIR = Path(__file__).resolve().parent.parent / "contracts"

BASE = {
    "contract_name": "demo",
    "version": 1,
    "primary_key": ["id"],
    "columns": [
        {"name": "id", "type": "string", "nullable": False},
        {"name": "amount", "type": "integer", "nullable": True},
    ],
}


def write(tmp_path, data, name="demo_v1.yaml"):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def make(**overrides):
    data = copy.deepcopy(BASE)
    data.update(overrides)
    return data


# ---- real contracts -------------------------------------------------------

def test_loads_real_customers_v1():
    contract = load_contract(CONTRACTS_DIR / "customers_v1.yaml")
    assert contract.name == "customers" and contract.version == 1
    assert contract.primary_key == ("customer_id",)
    assert len(contract.columns) == 7


def test_loads_real_orders_v1_with_reference_key():
    contract = load_contract(CONTRACTS_DIR / "orders_v1.yaml")
    assert len(contract.columns) == 8
    assert contract.get_column("customer_id").references == "customers.customer_id"
    assert contract.get_column("quantity").max == 1000


def test_loads_real_orders_v2():
    contract = load_contract(CONTRACTS_DIR / "orders_v2.yaml")
    assert contract.version == 2 and len(contract.columns) == 9
    assert "price_inr" in contract.column_names


# ---- file problems --------------------------------------------------------

def test_missing_file_is_rejected(tmp_path):
    with pytest.raises(ContractError, match="not found"):
        load_contract(tmp_path / "nope_v1.yaml")


def test_invalid_yaml_is_rejected(tmp_path):
    path = tmp_path / "bad_v1.yaml"
    path.write_text("a: [unclosed", encoding="utf-8")
    with pytest.raises(ContractError, match="invalid YAML"):
        load_contract(path)


# ---- structure problems ---------------------------------------------------

def test_missing_required_key_is_rejected(tmp_path):
    data = make()
    del data["primary_key"]
    with pytest.raises(ContractError, match="missing required"):
        load_contract(write(tmp_path, data))


def test_unknown_column_type_is_rejected(tmp_path):
    data = make()
    data["columns"][1]["type"] = "decimal"
    with pytest.raises(ContractError, match="unsupported type"):
        load_contract(write(tmp_path, data))


def test_duplicate_column_names_are_rejected(tmp_path):
    data = make()
    data["columns"].append({"name": "amount", "type": "float", "nullable": True})
    with pytest.raises(ContractError, match="duplicate column"):
        load_contract(write(tmp_path, data))


def test_primary_key_must_exist_in_columns(tmp_path):
    with pytest.raises(ContractError, match="primary key column"):
        load_contract(write(tmp_path, make(primary_key=["ghost"])))


def test_primary_key_must_not_be_nullable(tmp_path):
    data = make()
    data["columns"][0]["nullable"] = True
    with pytest.raises(ContractError, match="must not be nullable"):
        load_contract(write(tmp_path, data))


def test_bad_regex_pattern_is_rejected(tmp_path):
    data = make()
    data["columns"][0]["pattern"] = "([unclosed"
    with pytest.raises(ContractError, match="invalid pattern"):
        load_contract(write(tmp_path, data))


def test_min_greater_than_max_is_rejected(tmp_path):
    data = make()
    data["columns"][1].update({"min": 10, "max": 1})
    with pytest.raises(ContractError, match="min is greater than max"):
        load_contract(write(tmp_path, data))


def test_empty_columns_list_is_rejected(tmp_path):
    with pytest.raises(ContractError, match="at least one column"):
        load_contract(write(tmp_path, make(columns=[])))


def test_unknown_column_key_is_rejected(tmp_path):
    data = make()
    data["columns"][0]["nullble"] = False
    with pytest.raises(ContractError, match="unknown key"):
        load_contract(write(tmp_path, data))


# ---- version lookup -------------------------------------------------------

def test_latest_contract_picks_highest_version():
    assert load_latest_contract(CONTRACTS_DIR, "orders").version == 2
    assert load_latest_contract(CONTRACTS_DIR, "customers").version == 1


def test_latest_contract_unknown_name_is_rejected():
    with pytest.raises(ContractError, match="no contract versions"):
        load_latest_contract(CONTRACTS_DIR, "payments")
