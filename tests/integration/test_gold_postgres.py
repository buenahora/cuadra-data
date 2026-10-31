"""El SQL gold del job de Glue, contra un Postgres de verdad.

glue/transform.py construye en Postgres lo que el proyecto dbt construye en
DuckDB (listings_unified / market_listings / market_stats / zonas), porque
dbt no corre en AWS. Ese SQL es una traducción de dialecto -- percentile_cont
por median, string_to_array por string_split, subconsulta por QUALIFY -- y
una traducción solo se valida ejecutándola.

Se saltea si no hay un Postgres escuchando, así que no rompe el CI. Para
correrlo:

    docker run -d --name cuadra-pg-test -e POSTGRES_PASSWORD=test \\
      -e POSTGRES_DB=alquileres -p 55432:5432 postgres:16-alpine
    uv run pytest tests/integration/test_gold_postgres.py
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import socket
from datetime import date, timedelta

import pytest

PG = {
    "host": os.environ.get("CUADRA_TEST_PG_HOST", "127.0.0.1"),
    "port": int(os.environ.get("CUADRA_TEST_PG_PORT", "55432")),
    "database": os.environ.get("CUADRA_TEST_PG_DB", "alquileres"),
    "user": os.environ.get("CUADRA_TEST_PG_USER", "postgres"),
    "password": os.environ.get("CUADRA_TEST_PG_PASSWORD", "test"),
}


def _hay_postgres() -> bool:
    try:
        with socket.create_connection((PG["host"], PG["port"]), timeout=1):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _hay_postgres(),
    reason=f"sin Postgres en {PG['host']}:{PG['port']} -- ver el docstring del módulo",
)


def _transform():
    """glue/transform.py no es parte del paquete instalable (lo sube el CI
    como script del job), así que se carga por ruta."""
    ruta = pathlib.Path(__file__).resolve().parents[2] / "glue" / "transform.py"
    spec = importlib.util.spec_from_file_location("glue_transform", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


HOY = date.today()
RECIENTE = (HOY - timedelta(days=30)).isoformat()
VIEJO = (HOY - timedelta(days=500)).isoformat()

# (listing_id, barrio, operacion, tipo_propiedad, estado, dormitorios,
#  metraje_m2, precio, publicado_en, fotos, amenities)
AVISOS = [
    # --- Punta Carretas, 2 dormitorios: grupo con n >= 5, para que el filtro
    #     de outliers se aplique de verdad.
    ("IC1", "Punta Carretas", "alquiler", "apartamentos", "activo", 2, 70, 30000.0, RECIENTE, "a.jpg|b.jpg|a.jpg", "Ascensor|Piscina"),
    ("IC2", "Punta Carretas", "alquiler", "apartamentos", "activo", 2, 72, 31000.0, RECIENTE, "c.jpg", ""),
    ("IC3", "Punta Carretas", "alquiler", "apartamentos", "activo", 2, 75, 32000.0, RECIENTE, "", None),
    ("IC4", "Punta Carretas", "alquiler", "apartamentos", "activo", 2, 68, 29000.0, RECIENTE, None, "GYM"),
    ("IC5", "Punta Carretas", "alquiler", "apartamentos", "activo", 2, 71, 30500.0, RECIENTE, "d.jpg", ""),
    # Outlier brutal en ese mismo grupo: una venta mal categorizada.
    ("IC6", "Punta Carretas", "alquiler", "apartamentos", "activo", 2, 70, 7000000.0, RECIENTE, "", ""),
    # --- Villa Biarritz: barrio "fino" que barrio_canonico funde en Punta
    #     Carretas, así que su zone_id tiene que ser punta_carretas.
    ("IC7", "Villa Biarritz", "alquiler", "apartamentos", "activo", 1, 45, 22000.0, RECIENTE, "e.jpg", ""),
    # --- Parque Rodó: acentos, para zone_slug.
    ("IC8", "Parque Rodó", "alquiler", "apartamentos", "activo", 1, 40, 21000.0, RECIENTE, "f.jpg", ""),
    # --- Lo que tiene que quedar afuera ---
    ("IC9",  "Pocitos", "venta",    "apartamentos", "activo", 2, 80, 250000.0, RECIENTE, "", ""),
    ("IC10", "Pocitos", "alquiler", "apartamentos", "activo", None, 50, 25000.0, RECIENTE, "", ""),
    ("IC11", "Pocitos", "alquiler", "apartamentos", "activo", 2, 60, 26000.0, VIEJO, "", ""),
    # --- Re-listado: misma firma que IC12, distinto listing_id. Queda uno.
    ("IC12", "Malvín", "alquiler", "apartamentos", "activo", 3, 90, 40000.0, RECIENTE, "", ""),
    ("IC13", "Malvín", "alquiler", "apartamentos", "activo", 3, 90, 40000.0, (HOY - timedelta(days=60)).isoformat(), "", ""),
    # --- Pausado: entra a listings_unified pero no a market_listings.
    ("IC14", "Buceo", "alquiler", "apartamentos", "pausado", 2, 65, 27000.0, RECIENTE, "", ""),
    # --- Una casa: entra a unified, no a market_listings (solo apartamentos).
    ("IC15", "Prado", "alquiler", "casas", "activo", 3, 120, 45000.0, RECIENTE, "", ""),
]


@pytest.fixture
def conn():
    import pg8000.dbapi

    transform = _transform()
    c = pg8000.dbapi.connect(**PG)
    cur = c.cursor()
    for tabla in ("market_stats", "market_listings", "zonas", "listings_unified", "avisos"):
        cur.execute(f"DROP TABLE IF EXISTS {tabla} CASCADE")
    cur.execute(transform.AVISOS_DDL)

    def insertar(fecha: str, avisos: list) -> None:
        for (lid, barrio, operacion, tipo, estado, dorm, m2, precio, pub, fotos, amen) in avisos:
            cur.execute(
                "INSERT INTO avisos (fecha, listing_id, fuente_datos, barrio, direccion,"
                " operacion, tipo_propiedad, estado, descripcion, amenities, fotos,"
                " publicado_en, ingestado_en, dormitorios, banos, metraje_m2, foto_count,"
                " latitud, longitud, precio, hash_contenido)"
                " VALUES (%s,%s,'infocasas',%s,'Una calle 123',%s,%s,%s,'desc',%s,%s,%s,"
                " %s,%s,1,%s,0,-34.9,-56.2,%s,'h')",
                (fecha, lid, barrio, operacion, tipo, estado, amen, fotos, pub,
                 f"{pub}T00:00:00", dorm, m2, precio),
            )

    # Una corrida vieja que el gold tiene que ignorar entera: si se colara,
    # IC_FANTASMA aparecería en listings_unified.
    insertar((HOY - timedelta(days=7)).isoformat(), [
        ("IC_FANTASMA", "Pocitos", "alquiler", "apartamentos", "activo", 2, 60, 26000.0, RECIENTE, "", ""),
    ])
    insertar(HOY.isoformat(), AVISOS)
    c.commit()

    transform.construir_gold(cur)
    c.commit()
    yield cur
    c.close()


def _ids(cur, tabla: str) -> set[str]:
    cur.execute(f"SELECT listing_id FROM {tabla}")
    return {f[0] for f in cur.fetchall()}


def test_solo_entra_la_corrida_mas_reciente(conn):
    assert "IC_FANTASMA" not in _ids(conn, "listings_unified")


def test_filtros_de_alcance(conn):
    ids = _ids(conn, "listings_unified")
    assert "IC9" not in ids, "venta no es alquiler"
    assert "IC10" not in ids, "apartamento sin dormitorios"
    assert "IC11" not in ids, "más viejo que un año"


def test_dedup_de_relistados_deja_el_mas_reciente(conn):
    ids = _ids(conn, "listings_unified")
    assert len(ids & {"IC12", "IC13"}) == 1
    assert "IC12" in ids, "con misma firma gana el publicado_en más reciente"


def test_barrio_canonico_y_zone_slug(conn):
    conn.execute("SELECT listing_id, zone_id, barrio FROM listings_unified WHERE listing_id IN ('IC7','IC8')")
    por_id = {f[0]: (f[1], f[2]) for f in conn.fetchall()}
    # Villa Biarritz se funde en Punta Carretas.
    assert por_id["IC7"] == ("punta_carretas", "Punta Carretas")
    # Acentos y espacios.
    assert por_id["IC8"] == ("parque_rodo", "Parque Rodó")


def test_fotos_dedup_preservando_orden(conn):
    conn.execute("SELECT fotos FROM listings_unified WHERE listing_id = 'IC1'")
    assert conn.fetchone()[0] == ["a.jpg", "b.jpg"]
    conn.execute("SELECT fotos FROM listings_unified WHERE listing_id = 'IC3'")
    assert conn.fetchone()[0] == [], "string vacío da lista vacía, no ['']"


def test_amenities_se_parten_por_pipe(conn):
    conn.execute("SELECT amenities FROM listings_unified WHERE listing_id = 'IC1'")
    assert conn.fetchone()[0] == ["Ascensor", "Piscina"]
    conn.execute("SELECT amenities FROM listings_unified WHERE listing_id = 'IC3'")
    assert conn.fetchone()[0] == []


def test_dormitorios_bucket(conn):
    conn.execute("SELECT listing_id, dormitorios_bucket FROM listings_unified WHERE listing_id IN ('IC1','IC8','IC12')")
    por_id = dict(conn.fetchall())
    assert por_id["IC1"] == "2"
    assert por_id["IC8"] == "1"
    assert por_id["IC12"] == "3+", "3 o más colapsa en 3+"


def test_tipo_propiedad_se_normaliza_a_singular(conn):
    conn.execute("SELECT DISTINCT tipo_propiedad FROM listings_unified")
    assert {f[0] for f in conn.fetchall()} == {"apartamento", "casa"}


def test_market_listings_solo_apartamentos_activos(conn):
    ids = _ids(conn, "market_listings")
    assert "IC14" not in ids, "pausado no es activo"
    assert "IC15" not in ids, "una casa no entra en las estadísticas"


def test_market_listings_descarta_el_outlier(conn):
    """El grupo punta_carretas/2 tiene 6 avisos (n >= min_sample_size), así
    que el filtro mediana +/- 1.5*IQR se aplica y la venta mal categorizada
    de 7.000.000 queda afuera."""
    ids = _ids(conn, "market_listings")
    assert "IC6" not in ids
    assert {"IC1", "IC2", "IC3", "IC4", "IC5"} <= ids


def test_grupos_chicos_se_saltean_el_filtro(conn):
    """malvin/3+ tiene un solo aviso: con n < min_sample_size el IQR da 0 y
    filtraría avisos legítimos, así que no se aplica."""
    assert "IC12" in _ids(conn, "market_listings")


def test_market_stats_mediana_y_muestra_confiable(conn):
    conn.execute(
        "SELECT avisos_activos, precio_min, precio_mediana, precio_max, muestra_confiable,"
        " variacion_interanual_pct, barrio"
        " FROM market_stats WHERE zone_id = 'punta_carretas' AND dormitorios_bucket = '2'"
    )
    n, pmin, mediana, pmax, confiable, variacion, barrio = conn.fetchone()
    assert n == 5, "los 5 legítimos, sin el outlier"
    assert float(pmin) == 29000
    assert float(pmax) == 32000
    assert float(mediana) == 30500
    assert confiable is True
    assert variacion is None, "sin histórico es NULL, no 0 -- 'no sabemos'"
    assert barrio == "Punta Carretas"


def test_market_stats_marca_muestra_no_confiable(conn):
    conn.execute(
        "SELECT avisos_activos, muestra_confiable FROM market_stats"
        " WHERE zone_id = 'malvin' AND dormitorios_bucket = '3+'"
    )
    n, confiable = conn.fetchone()
    assert n == 1
    assert confiable is False


def test_zonas_cubre_todas_las_zonas_de_los_avisos(conn):
    conn.execute("SELECT zone_id FROM zonas")
    zonas = {f[0] for f in conn.fetchall()}
    conn.execute("SELECT DISTINCT zone_id FROM listings_unified")
    en_avisos = {f[0] for f in conn.fetchall()}
    # Si falta una, la ruta de listings del backend falla con KeyError.
    assert en_avisos <= zonas
    assert "punta_carretas" in zonas and "parque_rodo" in zonas
