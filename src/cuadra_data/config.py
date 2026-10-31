"""
Shared config loader. Único lugar del proyecto que sabe cómo resolver rutas
y variables de entorno -- etl/, pipelines/ y tests/ importan esto en vez de
hardcodear rutas o duplicar lógica de carga de .env.

Fuente de verdad legible para humanos: config/connections.yml (qué existe,
para qué sirve, valores por defecto). Este módulo solo traduce eso + el
entorno real (.env, variables ya exportadas) a valores concretos.
"""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

DATA_LAYER_ROOT = Path(__file__).resolve().parents[2]


def _load_connections_doc() -> dict:
    path = DATA_LAYER_ROOT / "config" / "connections.yml"
    with path.open() as f:
        return yaml.safe_load(f)


def load_env() -> None:
    """Carga cuadra-data/.env si existe (no falla si no está: en dev, los
    defaults de config/connections.yml alcanzan)."""
    env_path = DATA_LAYER_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)


def get_environment() -> str:
    return os.environ.get("CUADRA_ENV", "dev")


def get_duckdb_path() -> Path:
    """Ruta absoluta al archivo DuckDB del warehouse. CUADRA_DUCKDB_PATH, si
    está seteada, gana; si no, se usa el default documentado en
    config/connections.yml para el entorno actual."""
    if "CUADRA_DUCKDB_PATH" in os.environ:
        return Path(os.environ["CUADRA_DUCKDB_PATH"]).resolve()

    env = get_environment()
    doc = _load_connections_doc()
    try:
        default = doc["environments"][env]["warehouse"]["default"]
    except KeyError as e:
        raise RuntimeError(
            f"No hay CUADRA_DUCKDB_PATH seteada y config/connections.yml no "
            f"define un default de warehouse para el entorno '{env}'."
        ) from e
    return (DATA_LAYER_ROOT / default).resolve()


def get_raw_data_dir() -> Path:
    """Directorio donde etl/extract/ escribe sus CSVs y etl/load/ los lee.
    CUADRA_RAW_DATA_DIR, si está seteada, gana (usado por tests para no
    pisar el data/raw/ real -- ver tests/integration/); si no, el default es
    cuadra-data/data/raw."""
    if "CUADRA_RAW_DATA_DIR" in os.environ:
        return Path(os.environ["CUADRA_RAW_DATA_DIR"]).resolve()
    return (DATA_LAYER_ROOT / "data" / "raw").resolve()


def get_dbt_profiles_dir() -> Path:
    return (DATA_LAYER_ROOT / "config").resolve()


def get_dbt_project_dir() -> Path:
    return (DATA_LAYER_ROOT / "dbt").resolve()


def dbt_subprocess_env() -> dict:
    """Variables de entorno completas (heredando las actuales) necesarias
    para invocar `dbt` como subproceso apuntando al warehouse y perfil
    correctos."""
    load_env()
    env = os.environ.copy()
    env["CUADRA_DUCKDB_PATH"] = str(get_duckdb_path())
    env["DBT_PROFILES_DIR"] = str(get_dbt_profiles_dir())
    env["CUADRA_ENV"] = get_environment()
    return env
