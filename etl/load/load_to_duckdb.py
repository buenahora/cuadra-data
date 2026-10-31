"""
Load stage — takes whatever extract/ produced in data/raw/*.csv and loads it
into the DuckDB warehouse file, under a `raw` schema. dbt's sources.yml then
points at these tables (see dbt/models/staging/_sources.yml); dbt itself
never touches data/raw/ directly.

Deliberately generic on two axes:
  1. It does not know or care whether a given listings*.csv came from the
     synthetic generator or a real extractor -- same contract of columns.
  2. It does not hardcode which *sources* exist. Every data/raw/listings*.csv
     is discovered by glob and loaded into its own bronze table, named after
     the file (listings_buscandocasa.csv -> raw.listings_buscandocasa). Adding
     a new source, or a new Casasweb operation/property-type combo, means
     dropping a CSV in data/raw/ -- no change needed here. Each such CSV is
     expected to carry the shared bronze contract (see etl/extract/*.py):
     listing_id, barrio, dormitorios, metraje_m2, precio, estado,
     publicado_en, fuente_datos, operacion, tipo_propiedad, ingestado_en.

market_history_seed.csv is the one fixed exception -- a single seed table,
not a per-source listings file, loaded by its literal name.

Run: uv run python etl/load/load_to_duckdb.py
"""

import duckdb

from cuadra_data.config import get_duckdb_path, get_raw_data_dir

MARKET_HISTORY_SEED = "market_history_seed.csv"


def load_csv(con: duckdb.DuckDBPyConnection, csv_path) -> None:
    table_name = csv_path.stem
    con.execute(
        f"CREATE OR REPLACE TABLE raw.{table_name} AS "
        f"SELECT * FROM read_csv_auto('{csv_path.as_posix()}', header=true)"
    )
    count = con.execute(f"SELECT count(*) FROM raw.{table_name}").fetchone()[0]
    print(f"loaded {count} rows -> raw.{table_name}")


def main():
    raw_dir = get_raw_data_dir()
    warehouse_path = get_duckdb_path()

    con = duckdb.connect(str(warehouse_path))
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")

    listings_csvs = sorted(raw_dir.glob("listings*.csv"))
    if not listings_csvs:
        raise FileNotFoundError(
            f"no se encontró ningún {raw_dir}/listings*.csv -- correr etl/extract/ primero."
        )
    for csv_path in listings_csvs:
        load_csv(con, csv_path)

    history_path = raw_dir / MARKET_HISTORY_SEED
    if not history_path.exists():
        raise FileNotFoundError(
            f"{history_path} not found -- run etl/extract/ first to produce it."
        )
    load_csv(con, history_path)

    con.close()
    print(f"warehouse: {warehouse_path}")


if __name__ == "__main__":
    main()
