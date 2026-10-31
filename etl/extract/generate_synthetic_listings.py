"""
Extract stage — stand-in extractor.

There is no real operational database or scraped source yet (see
docs/roadmap.md Fase 0/1 and docs/market-intelligence.md Seccion 4, "arranque
en frio"). Until one exists, this script *synthesizes* a plausible raw
listings dataset that reproduces the min/mediana/max/avisos-activos figures
already hardcoded in the frontend prototype (frontend/src/screens/grupo2/S2A.jsx),
so the rest of the pipeline (load -> dbt staging -> dbt marts) can be built
and tested against realistic-shaped data.

Swap this file for a real extractor (operational DB query, scraper, etc.)
later; nothing downstream (etl/load, dbt/) needs to change, since both
extractors are expected to produce the same raw/listings.csv shape.

Also writes small stub files for Casasweb/BuscandoCasa (listings_casasweb_*,
listings_buscandocasa.csv -- see stub_other_source_rows()), reusing this same
synthetic price distribution under different id prefixes, so a fully-
synthetic environment (no real scraper run) still has something for every
dbt source declared in dbt/models/staging/_sources.yml -- otherwise
stg_listings_casasweb/buscandocasa and listings_unified fail outright since
their source tables wouldn't exist.

market_history_seed.csv (months -12..-1) is a separate concern: it's a
one-time seed standing in for history no one could have captured before the
platform existed (see dbt/models/staging/_sources.yml), independent of
whether listings.csv comes from this generator or from a real extractor like
etl/extract/scrape_infocasas.py. --history-only regenerates just that file,
for use alongside a real listings extractor (see pipelines/run_local_real.py).

Run: uv run python etl/extract/generate_synthetic_listings.py
     uv run python etl/extract/generate_synthetic_listings.py --history-only
"""

import argparse
import csv
import os
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from cuadra_data.config import DATA_LAYER_ROOT, get_raw_data_dir

RANDOM_SEED = 42
TODAY = date(2026, 8, 1)  # fixed reference date so output is reproducible
DATA_SOURCE = "synthetic"

# Copied from frontend/src/screens/grupo2/S2A.jsx `MARKET` (2026-08-13).
# min/med/max are in UYU/month. avisos = active listing count. yoy = year-over-year change.
MARKET = {
    "Pocitos": {
        "1": {"min": 16000, "med": 22000, "max": 30000, "avisos": 38, "yoy": 0.09},
        "2": {"min": 22000, "med": 32000, "max": 45000, "avisos": 24, "yoy": 0.07},
        "3+": {"min": 35000, "med": 48000, "max": 65000, "avisos": 12, "yoy": 0.05},
    },
    "Cordón": {
        "1": {"min": 13000, "med": 18000, "max": 25000, "avisos": 52, "yoy": 0.11},
        "2": {"min": 18000, "med": 26000, "max": 38000, "avisos": 31, "yoy": 0.08},
        "3+": {"min": 28000, "med": 38000, "max": 52000, "avisos": 9, "yoy": 0.06},
    },
    "Parque Rodó": {
        "1": {"min": 14000, "med": 19500, "max": 27000, "avisos": 29, "yoy": 0.10},
        "2": {"min": 19000, "med": 28000, "max": 40000, "avisos": 18, "yoy": 0.08},
        "3+": {"min": 30000, "med": 42000, "max": 58000, "avisos": 7, "yoy": 0.05},
    },
    "Punta Carretas": {
        "1": {"min": 18000, "med": 24000, "max": 34000, "avisos": 21, "yoy": 0.08},
        "2": {"min": 26000, "med": 36000, "max": 50000, "avisos": 15, "yoy": 0.07},
        "3+": {"min": 38000, "med": 54000, "max": 75000, "avisos": 6, "yoy": 0.04},
    },
    "La Blanqueada": {
        "1": {"min": 12000, "med": 16500, "max": 23000, "avisos": 34, "yoy": 0.12},
        "2": {"min": 17000, "med": 24000, "max": 34000, "avisos": 22, "yoy": 0.09},
        "3+": {"min": 26000, "med": 36000, "max": 50000, "avisos": 8, "yoy": 0.07},
    },
    "Tres Cruces": {
        "1": {"min": 11000, "med": 15500, "max": 22000, "avisos": 41, "yoy": 0.13},
        "2": {"min": 16000, "med": 22500, "max": 32000, "avisos": 26, "yoy": 0.10},
        "3+": {"min": 24000, "med": 34000, "max": 47000, "avisos": 10, "yoy": 0.08},
    },
}

# Barrios that exist as a publishing option in the wizard (frontend S2B.jsx)
# but have no market stats yet -- a handful of active listings each, on
# purpose, to exercise the "muestra insuficiente" path (see
# docs/market-intelligence.md Seccion 4/6): too few listings to trust a median.
THIN_COVERAGE_BARRIOS = ["Palermo", "Buceo", "Malvín", "Carrasco", "Centro"]

METRAJE_RANGE = {"1": (32, 55), "2": (50, 90), "3+": (85, 160)}
BEDROOM_COUNT = {"1": lambda: 1, "2": lambda: 2, "3+": lambda: random.choice([3, 4, 5])}

# realistic states beyond "activo", to prove the "avisos activos" filter in
# staging actually filters something instead of trivially passing everything through
OTHER_ESTADOS = ["pausado", "alquilada", "en_proceso"]
INACTIVE_NOISE_RATE = 0.12  # extra inactive rows injected per zone+bucket, as a fraction of `avisos`


def make_listing_id(counter: int) -> str:
    return f"L{counter:05d}"


def gen_prices(min_p: int, med_p: int, max_p: int, n: int, rng: random.Random) -> list[float]:
    """n prices in [min_p, max_p] with mode ~ med_p. Forces the exact min/max
    into the sample so the recomputed aggregate reproduces the source bounds
    exactly; the median will land close to (not exactly) med_p, which is
    expected for synthetic data and is asserted with a tolerance downstream."""
    if n <= 2:
        return [min_p, max_p][:n]
    middle = [round(rng.triangular(min_p, max_p, med_p), -2) for _ in range(n - 2)]
    return [float(min_p), float(max_p), *middle]


def gen_listing_rows(rng: random.Random, id_counter: list[int]) -> list[dict]:
    rows: list[dict] = []

    def add_zone(barrio: str, bucket: str, stats: dict):
        prices = gen_prices(stats["min"], stats["med"], stats["max"], stats["avisos"], rng)
        lo, hi = METRAJE_RANGE[bucket]
        for precio in prices:
            id_counter[0] += 1
            published_days_ago = rng.randint(1, 540)
            rows.append(
                {
                    "listing_id": make_listing_id(id_counter[0]),
                    "barrio": barrio,
                    "dormitorios": BEDROOM_COUNT[bucket](),
                    "metraje_m2": rng.randint(lo, hi),
                    "precio": precio,
                    "estado": "activo",
                    "publicado_en": (TODAY - timedelta(days=published_days_ago)).isoformat(),
                }
            )
        # noise: a few listings in this zone/bucket that are NOT active,
        # so market_stats must filter on estado rather than count everything.
        noise_n = max(1, round(stats["avisos"] * INACTIVE_NOISE_RATE))
        for _ in range(noise_n):
            id_counter[0] += 1
            precio = round(rng.triangular(stats["min"], stats["max"], stats["med"]), -2)
            published_days_ago = rng.randint(1, 540)
            rows.append(
                {
                    "listing_id": make_listing_id(id_counter[0]),
                    "barrio": barrio,
                    "dormitorios": BEDROOM_COUNT[bucket](),
                    "metraje_m2": rng.randint(lo, hi),
                    "precio": precio,
                    "estado": rng.choice(OTHER_ESTADOS),
                    "publicado_en": (TODAY - timedelta(days=published_days_ago)).isoformat(),
                }
            )

    for barrio, buckets in MARKET.items():
        for bucket, stats in buckets.items():
            add_zone(barrio, bucket, stats)

    # thin-coverage barrios: 1-3 active listings each, no target stats to hit,
    # prices roughly in the range of similar central barrios.
    for barrio in THIN_COVERAGE_BARRIOS:
        for bucket in ("1", "2"):
            n = rng.randint(1, 3)
            lo, hi = METRAJE_RANGE[bucket]
            price_lo, price_hi = (14000, 26000) if bucket == "1" else (20000, 38000)
            for _ in range(n):
                id_counter[0] += 1
                published_days_ago = rng.randint(1, 540)
                rows.append(
                    {
                        "listing_id": make_listing_id(id_counter[0]),
                        "barrio": barrio,
                        "dormitorios": BEDROOM_COUNT[bucket](),
                        "metraje_m2": rng.randint(lo, hi),
                        "precio": round(rng.uniform(price_lo, price_hi), -2),
                        "estado": "activo",
                        "publicado_en": (TODAY - timedelta(days=published_days_ago)).isoformat(),
                    }
                )

    return rows


def gen_history_rows(rng: random.Random) -> list[dict]:
    """Months -12..-1 relative to TODAY (the current month is computed live
    by the dbt marts from listing data, not seeded here -- see
    docs/market-intelligence.md Seccion 5 on why history can't be
    reconstructed after the fact and must be captured going forward)."""
    rows = []
    for barrio, buckets in MARKET.items():
        for bucket, stats in buckets.items():
            med_now = stats["med"]
            med_12mo_ago = med_now / (1 + stats["yoy"])
            for months_back in range(12, 0, -1):
                # linear interpolation with small noise, monotonic-ish upward drift
                t = (12 - months_back) / 11
                base = med_12mo_ago + (med_now - med_12mo_ago) * t
                noisy = base * rng.uniform(0.985, 1.015)
                period = _shift_month(TODAY, -months_back)
                rows.append(
                    {
                        "barrio": barrio,
                        "dormitorios_bucket": bucket,
                        "periodo": period.isoformat(),
                        "precio_mediana": round(noisy, -2),
                    }
                )
    return rows


def _shift_month(d: date, delta_months: int) -> date:
    month_index = d.month - 1 + delta_months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def add_ingestion_metadata(rows: list[dict]) -> None:
    """Ver docstring homólogo en scrape_infocasas.py -- mismo contrato de
    columnas de bronze que los extractores reales, para que raw.listings no
    cambie de forma según cuál de los dos lo haya producido."""
    ingestado_en = datetime.now(timezone.utc).isoformat()
    for row in rows:
        row["fotos"] = ""
        row["foto_count"] = ""
        # direccion/latitud/longitud: sin dato sintético -- ver
        # scrape_infocasas.py, mismo contrato de columnas de bronze.
        row["direccion"] = ""
        row["latitud"] = ""
        row["longitud"] = ""
        row["fuente_datos"] = DATA_SOURCE
        row["operacion"] = "alquiler"
        row["tipo_propiedad"] = "apartamentos"
        row["ingestado_en"] = ingestado_en


def stub_other_source_rows(base_rows: list[dict], prefix: str, offset: int, n: int, tipo_propiedad: str) -> list[dict]:
    """A handful of rows reusing the InfoCasas-shaped synthetic price
    distribution, just relabeled -- stands in for Casasweb/BuscandoCasa in a
    fully-synthetic dev/test environment (no real scraper run), so their
    dbt sources (see dbt/models/staging/_sources.yml) and everything
    downstream (stg_listings_casasweb/buscandocasa, listings_unified) have
    something to read. `offset` keeps id suffixes from colliding when two
    stub groups share the same `prefix` (e.g. Casasweb apartamentos/casas)."""
    rows = []
    for row in base_rows[offset : offset + n]:
        stub = dict(row)
        stub["listing_id"] = f"{prefix}{stub['listing_id'][1:]}"
        stub["tipo_propiedad"] = tipo_propiedad
        rows.append(stub)
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows -> {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--history-only",
        action="store_true",
        help="regenerate market_history_seed.csv only, leave listings.csv untouched",
    )
    args = parser.parse_args()

    # Fuente falsa de datos, aislada del raw dir real por default (ver
    # incidente 2026-08-20 en docstring de pipelines/run_local.py: antes
    # este generador y los scrapers reales compartían raw dir, y correr
    # este script pisaba avisos reales sin aviso). --history-only es la
    # excepción: existe justamente para escribir market_history_seed.csv
    # junto a datos reales (ver pipelines/run_local_real.py), así que ese
    # modo respeta el raw dir real de siempre. setdefault: si ya hay
    # CUADRA_RAW_DATA_DIR seteada (a mano o por run_local.py), gana esa.
    if not args.history_only:
        os.environ.setdefault("CUADRA_RAW_DATA_DIR", str(DATA_LAYER_ROOT / "data" / "raw_synthetic"))

    rng = random.Random(RANDOM_SEED)
    raw_dir = get_raw_data_dir()

    if not args.history_only:
        id_counter = [0]
        listings = gen_listing_rows(rng, id_counter)
        add_ingestion_metadata(listings)
        write_csv(raw_dir / "listings.csv", listings)

        write_csv(
            raw_dir / "listings_casasweb_alquiler_apartamentos.csv",
            stub_other_source_rows(listings, "CW", offset=0, n=15, tipo_propiedad="apartamentos"),
        )
        write_csv(
            raw_dir / "listings_casasweb_alquiler_casas.csv",
            stub_other_source_rows(listings, "CW", offset=15, n=8, tipo_propiedad="casas"),
        )
        write_csv(
            raw_dir / "listings_buscandocasa.csv",
            stub_other_source_rows(listings, "BC", offset=0, n=15, tipo_propiedad="apartamento"),
        )

    history = gen_history_rows(rng)
    write_csv(raw_dir / "market_history_seed.csv", history)


if __name__ == "__main__":
    main()
