"""
DAG de Airflow para orquestar el pipeline de mercado -- HOY ESCRITO PERO NO
DESPLEGADO. No hay Airflow corriendo en este proyecto todavía.

Por qué está el archivo pero no el servidor: no existe todavía una fuente de
datos real que extraer periódicamente (ver docs/roadmap.md Fase 0/2 y
docs/market-intelligence.md Sección 4, "arranque en frío"). Levantar
webserver + scheduler + metadata db de Airflow para orquestar la generación
de datos sintéticos no aporta nada que pipelines/run_local.py no dé ya con
un solo comando. Este archivo documenta la orquestación pretendida para
cuando sí haya un extractor real (base operacional o fuente externa) y un
cronograma real (ver docs/market-intelligence.md Sección 5: los snapshots
mensuales hay que empezar a capturarlos apenas exista esa fuente).

Cada `@task` de acá es literalmente el mismo paso que pipelines/run_local.py
ya ejecuta secuencialmente en local -- pasar de uno a otro es wiring, no
reescritura (ver docs/roadmap.md "Principio guía").

Para activar esto de verdad:
  1. `uv sync --group airflow` (instala apache-airflow, no es dependencia
     por defecto del proyecto -- ver pyproject.toml).
  2. Levantar Airflow (ej. `docker-compose` con webserver+scheduler+DB de
     metadata -- no incluido en este repo todavía).
  3. Apuntar AIRFLOW_HOME a esta carpeta o copiar este archivo a la carpeta
     de dags que Airflow esté escaneando.
  4. Reemplazar el extractor sintético (etl/extract/generate_synthetic_listings.py)
     por el extractor real cuando exista.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime

from airflow.decorators import dag, task

from cuadra_data.config import (
    DATA_LAYER_ROOT,
    dbt_subprocess_env,
    get_dbt_project_dir,
    get_dbt_profiles_dir,
)


def _run(cmd: list[str]) -> None:
    env = dbt_subprocess_env()
    result = subprocess.run(cmd, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"Comando falló ({result.returncode}): {' '.join(cmd)}")


@dag(
    dag_id="cuadra_market_stats",
    description="Extract -> load -> dbt run/test para el motor de mercado de Cuadra.",
    schedule="@monthly",  # ver docs/market-intelligence.md §5: la captura mensual
    start_date=datetime(2026, 8, 1),
    catchup=False,
    tags=["cuadra", "market-intelligence"],
)
def cuadra_market_stats():
    @task
    def extract():
        _run([sys.executable, str(DATA_LAYER_ROOT / "etl/extract/generate_synthetic_listings.py")])

    @task
    def load():
        _run([sys.executable, str(DATA_LAYER_ROOT / "etl/load/load_to_duckdb.py")])

    @task
    def dbt_seed():
        _run([
            "dbt", "seed",
            "--project-dir", str(get_dbt_project_dir()),
            "--profiles-dir", str(get_dbt_profiles_dir()),
        ])

    @task
    def dbt_run():
        _run([
            "dbt", "run",
            "--project-dir", str(get_dbt_project_dir()),
            "--profiles-dir", str(get_dbt_profiles_dir()),
        ])

    @task
    def dbt_test():
        _run([
            "dbt", "test",
            "--project-dir", str(get_dbt_project_dir()),
            "--profiles-dir", str(get_dbt_profiles_dir()),
        ])

    extract() >> load() >> dbt_seed() >> dbt_run() >> dbt_test()


cuadra_market_stats()
