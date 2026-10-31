"""
Corre el pipeline completo (extract -> load -> dbt seed/run/test) contra un
warehouse DuckDB y un directorio data/raw temporales y aislados (nunca toca
data/warehouse.duckdb ni data/raw/ reales -- ver CUADRA_RAW_DATA_DIR en
run_pipeline más abajo), y valida que los marts resultantes tengan la forma
esperada -- en particular, que las cifras recalculadas por dbt se acerquen a
las medianas que ya están hardcodeadas en el prototipo frontend
(frontend/src/screens/grupo2/S2A.jsx), que es de donde sale el dataset
sintético (ver etl/extract/).
"""

import subprocess
import sys

import duckdb
import pytest

from cuadra_data.config import (
    DATA_LAYER_ROOT,
    get_dbt_project_dir,
    get_dbt_profiles_dir,
)

# subset de docs/market-intelligence.md / S2A.jsx MARKET, usado solo para
# validar el pipeline, no para volver a definir la fuente de verdad.
EXPECTED = {
    ("cordon", "1"): dict(avisos=52, min=13000, max=25000, med=18000),
    ("pocitos", "2"): dict(avisos=24, min=22000, max=45000, med=32000),
    ("tres_cruces", "3+"): dict(avisos=10, min=24000, max=47000, med=34000),
}


@pytest.fixture(scope="module")
def warehouse_path(tmp_path_factory):
    return tmp_path_factory.mktemp("cuadra_test") / "warehouse.duckdb"


@pytest.fixture(scope="module", autouse=True)
def run_pipeline(warehouse_path, tmp_path_factory):
    raw_data_dir = tmp_path_factory.mktemp("cuadra_test_raw")
    env = {
        "CUADRA_DUCKDB_PATH": str(warehouse_path),
        "CUADRA_RAW_DATA_DIR": str(raw_data_dir),
        "DBT_PROFILES_DIR": str(get_dbt_profiles_dir()),
        "CUADRA_ENV": "dev",
        "PATH": __import__("os").environ["PATH"],
        "HOME": __import__("os").environ.get("HOME", ""),
    }
    dbt_project_dir = str(get_dbt_project_dir())

    steps = [
        [sys.executable, str(DATA_LAYER_ROOT / "etl/extract/generate_synthetic_listings.py")],
        [sys.executable, str(DATA_LAYER_ROOT / "etl/load/load_to_duckdb.py")],
        ["dbt", "seed", "--project-dir", dbt_project_dir, "--profiles-dir", env["DBT_PROFILES_DIR"]],
        ["dbt", "run", "--project-dir", dbt_project_dir, "--profiles-dir", env["DBT_PROFILES_DIR"]],
        ["dbt", "test", "--project-dir", dbt_project_dir, "--profiles-dir", env["DBT_PROFILES_DIR"]],
    ]
    for cmd in steps:
        result = subprocess.run(cmd, env=env, capture_output=True, text=True)
        assert result.returncode == 0, f"{' '.join(cmd)} failed:\n{result.stdout}\n{result.stderr}"


@pytest.fixture(scope="module")
def con(warehouse_path, run_pipeline):
    connection = duckdb.connect(str(warehouse_path), read_only=True)
    yield connection
    connection.close()


def test_market_stats_matches_prototype_within_tolerance(con):
    stats = con.execute(
        "select zone_id, dormitorios_bucket, avisos_activos, precio_min, precio_mediana, precio_max "
        "from market_stats"
    ).fetchall()
    by_key = {(r[0], r[1]): r for r in stats}

    for key, expected in EXPECTED.items():
        assert key in by_key, f"falta {key} en market_stats"
        _, _, avisos, precio_min, precio_mediana, precio_max = by_key[key]
        # market_stats filtra outliers de precio por zona+dormitorios (mediana
        # +- 1.5*IQR, ver dbt/models/marts/market_listings.sql, agregado
        # 2026-08-14). gen_prices() fuerza el min/max exacto dentro de la
        # muestra sintética (ver su docstring) -- cuando el resto de la
        # muestra (triangular alrededor de la mediana) queda apretada, esos
        # dos extremos forzados pueden caer como outliers legítimos y
        # filtrarse. Un puñado menos de avisos y un min/max más cerrado que
        # el original es exactamente lo esperado, no una regresión -- por
        # eso estas 3 aserciones toleran, no exigen exactitud.
        assert avisos >= expected["avisos"] - 5, f"{key}: muy pocos avisos tras el filtro de outliers"
        assert abs(precio_min - expected["min"]) / expected["min"] < 0.15
        assert abs(precio_max - expected["max"]) / expected["max"] < 0.15
        # la mediana es sintética/aleatoria (ver etl/extract/), se tolera +-15%
        assert abs(precio_mediana - expected["med"]) / expected["med"] < 0.15


def test_market_stats_bounds_are_ordered(con):
    bad = con.execute(
        "select count(*) from market_stats where not (precio_min <= precio_mediana and precio_mediana <= precio_max)"
    ).fetchone()[0]
    assert bad == 0


def test_thin_coverage_zones_lack_reliable_sample(con):
    row = con.execute(
        "select muestra_confiable from market_stats where zone_id = 'palermo' and dormitorios_bucket = '1'"
    ).fetchone()
    assert row is not None
    assert row[0] is False


def test_listing_market_position_levels_are_valid(con):
    levels = {
        r[0]
        for r in con.execute("select distinct nivel_mercado from listing_market_position").fetchall()
    }
    assert levels <= {"below", "in", "above", None}
    assert levels & {"below", "in", "above"}, "esperaba al menos un nivel calculado"


def test_market_snapshots_has_full_year_for_covered_zones(con):
    months = con.execute(
        "select count(*) from market_snapshots where zone_id = 'cordon' and dormitorios_bucket = '1'"
    ).fetchone()[0]
    assert months == 13  # 12 historicos sembrados + 1 calculado del mes actual
