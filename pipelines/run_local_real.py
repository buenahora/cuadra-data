"""
Orquestador de desarrollo local con datos REALES de InfoCasas, en vez del
generador sintético (ver pipelines/run_local.py para la variante sintética,
y docs/scraping-strategy.md para el research detrás de este extractor).

listings.csv sale de etl/extract/scrape_infocasas.py (avisos reales de
alquiler de apartamentos en Montevideo). market_history_seed.csv se sigue
generando con el generador sintético en modo --history-only: es un seed de
histórico (meses -12..-1) que ningún extractor real puede producir -- nadie
capturó snapshots antes de que la plataforma existiera (ver
dbt/models/staging/_sources.yml). El histórico real se construye mes a mes
hacia adelante desde que arranca Fase 2 (docs/roadmap.md), no acá.

Pasa argumentos extra de scrape_infocasas.py después de "--" para acotar la
corrida durante desarrollo, p.ej.:
    uv run python pipelines/run_local_real.py -- --max-pages 5

Run: uv run python pipelines/run_local_real.py
"""

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
    scrape_extra_args = sys.argv[1:]
    if scrape_extra_args and scrape_extra_args[0] == "--":
        scrape_extra_args = scrape_extra_args[1:]

    python = sys.executable
    env = dbt_subprocess_env()
    dbt_project_dir = get_dbt_project_dir()
    dbt_profiles_dir = get_dbt_profiles_dir()

    run_step(
        "extract: InfoCasas real (listings.csv)",
        [python, str(DATA_LAYER_ROOT / "etl/extract/scrape_infocasas.py"), *scrape_extra_args],
        env=env,
    )
    run_step(
        "extract: seed de histórico sintético (market_history_seed.csv, ver docstring)",
        [python, str(DATA_LAYER_ROOT / "etl/extract/generate_synthetic_listings.py"), "--history-only"],
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

    print(f"\nPipeline completo (datos reales de InfoCasas). Warehouse: {env['CUADRA_DUCKDB_PATH']}")


if __name__ == "__main__":
    main()
