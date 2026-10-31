import os

from cuadra_data.config import (
    DATA_LAYER_ROOT,
    get_dbt_profiles_dir,
    get_dbt_project_dir,
    get_duckdb_path,
    get_environment,
    get_raw_data_dir,
)


def test_default_environment_is_dev(monkeypatch):
    monkeypatch.delenv("CUADRA_ENV", raising=False)
    assert get_environment() == "dev"


def test_duckdb_path_env_var_override(monkeypatch, tmp_path):
    override = tmp_path / "custom.duckdb"
    monkeypatch.setenv("CUADRA_DUCKDB_PATH", str(override))
    assert get_duckdb_path() == override.resolve()


def test_duckdb_path_falls_back_to_connections_yml_default(monkeypatch):
    monkeypatch.delenv("CUADRA_DUCKDB_PATH", raising=False)
    monkeypatch.setenv("CUADRA_ENV", "dev")
    path = get_duckdb_path()
    assert path == (DATA_LAYER_ROOT / "data" / "warehouse.duckdb").resolve()


def test_dbt_dirs_point_inside_data_layer():
    assert get_dbt_profiles_dir() == (DATA_LAYER_ROOT / "config").resolve()
    assert get_dbt_project_dir() == (DATA_LAYER_ROOT / "dbt").resolve()


def test_raw_data_dir_env_var_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CUADRA_RAW_DATA_DIR", str(tmp_path))
    assert get_raw_data_dir() == tmp_path.resolve()


def test_raw_data_dir_falls_back_to_data_layer_default(monkeypatch):
    monkeypatch.delenv("CUADRA_RAW_DATA_DIR", raising=False)
    assert get_raw_data_dir() == (DATA_LAYER_ROOT / "data" / "raw").resolve()
