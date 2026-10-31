"""
Orquestador de desarrollo local: extract -> load -> dbt run -> dbt test, en
un solo comando. Es el "poor man's Airflow" mientras no hay un scheduler real
corriendo (ver pipelines/dags/market_stats_dag.py y docs/roadmap.md Fase 2).

Cuando el DAG de Airflow se active de verdad, cada paso de este script pasa a
ser una task del DAG -- la lógica de cada paso no cambia, solo quién la
invoca y cuándo.

Escribe a data/raw_synthetic/ + data/warehouse_synthetic.duckdb, NUNCA a
data/raw/ ni data/warehouse.duckdb (esos son de run_local_real.py, ver ese
docstring) -- antes ambos pipelines escribían al mismo lugar y correr este
sin querer (ej. `start_stack.sh --with-pipeline` sobre un warehouse con
datos reales ya cargados) borraba silenciosamente miles de avisos reales
scrapeados, sin backup posible (incidente 2026-08-20). Los setdefault dejan
ganar a quien exporte CUADRA_RAW_DATA_DIR/CUADRA_DUCKDB_PATH a mano (mismo
criterio que el resto de config.py).

Run: uv run python pipelines/run_local.py
"""

import os
import subprocess
import sys

from cuadra_data.config import (
    DATA_LAYER_ROOT,
    dbt_subprocess_env,
    get_dbt_project_dir,
    get_dbt_profiles_dir,
)


def run_step(name: str, cmd: list[str], cwd=None, env=None) -> None:
    print(f"\n=== {name} ===")
    result = subprocess.run(cmd, cwd=cwd, env=env)
    if result.returncode != 0:
        print(f"\n!!! Falló el paso '{name}' (exit code {result.returncode}). Deteniendo pipeline.")
        sys.exit(result.returncode)


def main():
    python = sys.executable
    os.environ.setdefault("CUADRA_RAW_DATA_DIR", str(DATA_LAYER_ROOT / "data" / "raw_synthetic"))
    os.environ.setdefault("CUADRA_DUCKDB_PATH", str(DATA_LAYER_ROOT / "data" / "warehouse_synthetic.duckdb"))
    env = dbt_subprocess_env()
    dbt_project_dir = get_dbt_project_dir()
    dbt_profiles_dir = get_dbt_profiles_dir()

    run_step(
        "extract: generar/actualizar datos crudos",
        [python, str(DATA_LAYER_ROOT / "etl/extract/generate_synthetic_listings.py")],
        env=env,
    )
    run_step(
        "load: raw CSVs -> DuckDB (schema raw)",
        [python, str(DATA_LAYER_ROOT / "etl/load/load_to_duckdb.py")],
        env=env,
    )
    run_step(
        "dbt seed: catálogos de referencia (barrios, amenities, garantías)",
        ["dbt", "seed", "--project-dir", str(dbt_project_dir), "--profiles-dir", str(dbt_profiles_dir)],
        env=env,
    )
    run_step(
        "dbt run: staging + marts (market_stats, listing_market_position, market_snapshots)",
        ["dbt", "run", "--project-dir", str(dbt_project_dir), "--profiles-dir", str(dbt_profiles_dir)],
        env=env,
    )
    run_step(
        "dbt test: contratos de datos",
        ["dbt", "test", "--project-dir", str(dbt_project_dir), "--profiles-dir", str(dbt_profiles_dir)],
        env=env,
    )

    print(f"\nPipeline completo. Warehouse: {env['CUADRA_DUCKDB_PATH']}")


if __name__ == "__main__":
    main()
