"""
Extract stage — extractor real de InfoCasas (Nivel 0 de docs/scraping-strategy.md).

Reemplaza a generate_synthetic_listings.py para uso real (produce el mismo
data/raw/listings.csv, mismo contrato de columnas, para que etl/load/ y dbt/
no necesiten ningún cambio). generate_synthetic_listings.py se mantiene tal
cual para tests/desarrollo -- rápido, offline, reproducible, sin pegarle a
un sitio real en cada corrida.

Cómo funciona (ver docs/scraping-strategy.md para el research completo):
Las páginas de resultados de búsqueda de InfoCasas (SSR con Next.js) traen
embebido un <script id="__NEXT_DATA__"> con el JSON completo de los ~21
avisos de esa página (fetchResult.searchFast.data), incluyendo precio,
moneda, dormitorios, baños, m², barrio, dirección, coordenadas (lat/lon) y
fecha de publicación. No hace falta tocar páginas de detalle individuales ni
parsear HTML visual -- es `json.loads()` directo sobre cada página de
resultados paginados.

Verificado contra robots.txt real de infocasas.com.uy antes de esta
implementación: la ruta /alquiler/apartamentos/{ciudad}[/paginaN] no está en
ningún Disallow (lo que sí está bloqueado son combinaciones de filtros tipo
"*-y-*", que este extractor no usa). El chequeo se repite en runtime con
urllib.robotparser por si el sitio cambia las reglas más adelante.

Orden de resultados -- IMPORTANTE, ver docs/scraping-strategy.md: el orden
default del sitio es "Popularidad" (?order=2, implícito si no se manda el
parámetro), que se recalcula todo el tiempo (no solo por avisos nuevos, sino
porque el score de avisos ya vistos cambia solo). Paginar por índice sobre
una lista que se reordena sola en vivo pierde avisos -- una corrida completa
(359 páginas, ~9 min) perdió el 21% así.

Este extractor pagina con `?order=1` (precio ascendente) Y `?order=4` (m²
ascendente) -- dos corridas completas, no una -- y combina+dedupea el
resultado por listing_id. Cada campo individual es estable entre páginas
(el precio o el m² de un aviso no cambian en el rato que dura la corrida),
pero dentro de un mismo campo hay muchos empates (varios avisos a $13.000,
o a 20m²), y el orden relativo *entre empatados* no está garantizado --
eso seguía perdiendo ~10% de avisos usando un solo criterio de orden. Precio
y m² son campos suficientemente independientes como para que sea
improbable que el mismo aviso caiga en un empate en los dos a la vez, así
que la unión de ambas pasadas cierra casi todo lo que a cualquiera de las
dos le faltaba sola. Cuesta el doble de tiempo (~18 min en vez de ~9), pero
es la única forma confirmada de acercarse al total real sin depender de que
el sitio exponga un cursor/orden compuesto que no tiene.

Run: uv run python etl/extract/scrape_infocasas.py
     uv run python etl/extract/scrape_infocasas.py --max-pages 5   # muestra rápida
"""

import argparse
import csv
import json
import re
import sys
import time
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path

import requests

from cuadra_data.config import DATA_LAYER_ROOT

DATA_SOURCE = "infocasas"

BASE_HOST = "https://www.infocasas.com.uy"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
DEFAULT_DELAY_SECONDS = 1.2
DEFAULT_FALLBACK_UYU_PER_USD = 40.0  # solo si una página no trae ningún aviso en pesos de referencia
MAX_CONSECUTIVE_FAILURES = 5
ORDER_PRICE_ASC = 1
ORDER_M2_ASC = 4
# Ver docstring del módulo: los únicos dos órdenes confirmados estables entre
# páginas, combinados para que los empates de uno no coincidan con los del otro.
STABLE_ORDERS = (ORDER_PRICE_ASC, ORDER_M2_ASC)

NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)
LOCATION_POINT_RE = re.compile(r"POINT \(([-\d.]+) ([-\d.]+)\)")


def category_url(operation: str, property_type: str, city: str, page: int, order: int) -> str:
    base = f"{BASE_HOST}/{operation}/{property_type}/{city}"
    path = base if page == 1 else f"{base}/pagina{page}"
    return f"{path}?order={order}"


def robots_allows(session: requests.Session, url: str) -> bool:
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(f"{BASE_HOST}/robots.txt")
    resp = session.get(f"{BASE_HOST}/robots.txt", timeout=15)
    resp.raise_for_status()
    rp.parse(resp.text.splitlines())
    return rp.can_fetch(USER_AGENT, url)


def fetch_page(session: requests.Session, url: str) -> dict | None:
    try:
        resp = session.get(url, timeout=20)
    except requests.exceptions.RequestException as e:
        print(f"  !! error de red en {url}: {e}")
        return None
    if resp.status_code != 200:
        print(f"  !! HTTP {resp.status_code} en {url}")
        return None
    m = NEXT_DATA_RE.search(resp.text)
    if not m:
        print(f"  !! no se encontró __NEXT_DATA__ en {url}")
        return None
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        print(f"  !! JSON inválido en {url}: {e}")
        return None
    return data["props"]["pageProps"]["fetchResult"]["searchFast"]


def normalize_row(item: dict, fallback_rate: list[float]) -> dict | None:
    """fallback_rate es una lista de 1 elemento (closure mutable) con el
    último tipo de cambio UYU/USD visto en la corrida, para poder normalizar
    avisos en dólares aunque su propia página no traiga ningún aviso en
    pesos de referencia."""
    neighbourhoods = item.get("locations", {}).get("neighbourhood") or []
    if not neighbourhoods:
        return None  # sin barrio no se puede agregar a market_stats, se descarta
    barrio = neighbourhoods[0]["name"]

    bedrooms = item.get("bedrooms")
    if bedrooms is None:
        return None

    banos = item.get("bathrooms")

    price = item.get("price") or {}
    currency_id = price.get("currency", {}).get("id")
    amount = price.get("amount")
    price_usd = item.get("price_amount_usd")
    if amount is None:
        return None

    if currency_id == 2:  # pesos uruguayos
        precio_uyu = amount
        rate = price.get("currency", {}).get("rate")
        if rate:
            fallback_rate[0] = rate
    else:  # dólares (currency_id == 1) u otra -- normalizar via price_amount_usd
        base = price_usd if price_usd is not None else amount
        precio_uyu = round(base * fallback_rate[0])

    # El mismo JSON ya trae la galería completa sin request extra -- se
    # captura entera (no solo la portada) desde el 2026-08-14, ver
    # conversación: "idealmente deberían ser un array de imágenes". Se
    # serializa como string separado por "|" porque es CSV -- dbt la
    # despliega a LIST(VARCHAR) real (ver stg_listings.sql). "|" no es un
    # caracter válido de URL sin encodear, no hay riesgo de colisión.
    # dict.fromkeys en vez de list(set(...)) para dedupear preservando el
    # orden original -- InfoCasas repite la misma url dentro de "images"
    # en algunos avisos (encontrado 2026-08-20 investigando conteos de
    # fotos inflados en el front), sin esto foto_count/fotos quedan con
    # duplicados.
    fotos = list(dict.fromkeys(im["image"] for im in (item.get("images") or []) if im.get("image")))

    # Dirección y coordenadas -- agregado 2026-08-15, mismo JSON ya
    # descargado, sin request extra (ver auditoría de nulls/calidad de esa
    # fecha). `address` viene poblado en la gran mayoría de los avisos
    # incluso cuando `showAddress` es false (el sitio no la muestra en la
    # UI pública, mismo criterio de negocio de mostrar la zona pero no la
    # calle exacta hasta el contacto) -- se captura igual, es el mismo dato
    # que ya viaja en la respuesta que cualquier visitante recibe al cargar
    # la página. `location_point` es un WKT "POINT (lon lat)" -- notar el
    # orden lon/lat (no lat/lon).
    location_point = item.get("locations", {}).get("location_point") or ""
    m_point = LOCATION_POINT_RE.match(location_point)
    longitud = m_point.group(1) if m_point else ""
    latitud = m_point.group(2) if m_point else ""

    # descripcion/amenities -- agregado 2026-08-20 (re-ingesta completa tras
    # el incidente de borrado del mismo día), mismo JSON ya descargado, sin
    # request extra. `description` es texto libre tal cual lo carga quien
    # publica el aviso (incluye a veces datos de contacto propios, no se
    # sanitiza). `facilities` es una lista de {id, name, group} -- se guarda
    # solo el nombre, pipe-separated, mismo criterio que "fotos" (dbt la
    # despliega a LIST(VARCHAR), ver stg_listings.sql).
    descripcion = item.get("description") or ""
    amenities = "|".join(f["name"] for f in (item.get("facilities") or []) if f.get("name"))

    # datos_crudos -- el item completo tal cual lo devuelve el sitio, sin
    # curar. InfoCasas trae muchísimos más campos de los que este extractor
    # normaliza a columnas propias (owner/inmobiliaria, technicalSheet con
    # ~15 campos más como antigüedad/orientación/piso/garage, condominium,
    # commonExpenses, etc.) -- en vez de ir agregando una columna nueva cada
    # vez que se necesita uno de esos campos, se guarda el JSON entero acá
    # (dbt lo tipa a JSON real en stg_listings.sql, queryable con
    # json_extract) y las columnas de arriba quedan para lo que ya se sabe
    # que se usa seguido (dormitorios, baños, precio, etc.).
    datos_crudos = json.dumps(item, ensure_ascii=False)

    return {
        "listing_id": f"IC{item['id']}",
        "barrio": barrio,
        "direccion": item.get("address") or "",
        "latitud": latitud,
        "longitud": longitud,
        "dormitorios": bedrooms,
        "banos": banos if banos is not None else "",
        "metraje_m2": item.get("m2") or item.get("m2Built") or "",
        "precio": precio_uyu,
        "estado": "activo" if item.get("active", True) else "pausado",
        "publicado_en": item.get("created_at") or "",
        "descripcion": descripcion,
        "amenities": amenities,
        "fotos": "|".join(fotos),
        "foto_count": item.get("image_count") or len(fotos) or "",
        "datos_crudos": datos_crudos,
    }


def scrape_one_order(
    session: requests.Session,
    operation: str,
    property_type: str,
    city: str,
    order: int,
    max_pages: int | None,
    delay: float,
    rows: dict[str, dict],
) -> tuple[int, int]:
    """Pagina un único orden de principio a fin, agregando a `rows` (mutada
    in-place, compartida entre pasadas de distinto orden para dedupear).
    Devuelve (nominal_total de esta pasada, avisos descartados por datos
    incompletos)."""
    first_url = category_url(operation, property_type, city, 1, order)
    if not robots_allows(session, first_url):
        raise RuntimeError(
            f"robots.txt de {BASE_HOST} no permite crawlear {first_url} -- abortando. "
            "Revisar docs/scraping-strategy.md antes de forzar esto."
        )

    fallback_rate = [DEFAULT_FALLBACK_UYU_PER_USD]
    page = 1
    consecutive_failures = 0
    skipped_incomplete = 0  # normalize_row devolvió None: sin barrio, sin dormitorios o sin precio
    nominal_total = None  # 'total' que reporta el sitio en la primera página (referencia, no garantía)

    while True:
        if max_pages is not None and page > max_pages:
            break

        url = category_url(operation, property_type, city, page, order)
        print(f"[order={order} pág {page}] {url}")
        result = fetch_page(session, url)

        if result is None:
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                print(
                    f"!! {MAX_CONSECUTIVE_FAILURES} fallos consecutivos -- "
                    "puede ser un bloqueo, corto acá en vez de insistir."
                )
                break
            time.sleep(delay)
            page += 1
            continue

        consecutive_failures = 0
        for item in result["data"]:
            row = normalize_row(item, fallback_rate)
            if row is None:
                skipped_incomplete += 1
                continue
            rows[row["listing_id"]] = row

        paginator = result["paginatorInfo"]
        if nominal_total is None:
            nominal_total = paginator.get("total")
        print(f"    {len(result['data'])} avisos, {len(rows)} acumulados (combinado), {paginator}")

        if not paginator.get("hasMorePages"):
            break

        page += 1
        time.sleep(delay)

    return nominal_total, skipped_incomplete


def scrape(
    operation: str,
    property_type: str,
    city: str,
    max_pages: int | None,
    delay: float,
    orders: tuple[int, ...] = STABLE_ORDERS,
) -> list[dict]:
    """Pagina cada orden en `orders` por separado y combina los resultados
    dedupeados por listing_id (ver docstring del módulo: un solo orden deja
    ~10% afuera por empates; combinar dos criterios independientes cierra
    casi todo eso)."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "es-UY,es;q=0.9"})

    rows: dict[str, dict] = {}  # listing_id -> row, compartido entre pasadas
    nominal_total = None
    total_skipped_incomplete = 0

    for order in orders:
        pass_nominal_total, skipped_incomplete = scrape_one_order(
            session, operation, property_type, city, order, max_pages, delay, rows
        )
        nominal_total = nominal_total or pass_nominal_total
        total_skipped_incomplete += skipped_incomplete

    coverage = f"{len(rows)}/{nominal_total}" if nominal_total else str(len(rows))
    print(
        f"Total: {len(rows)} avisos únicos combinando {len(orders)} orden(es) "
        f"(cobertura vs. total nominal del sitio: {coverage}) -- "
        f"{total_skipped_incomplete} descartados por datos incompletos (sin barrio/dormitorios/precio)"
    )
    return list(rows.values())


def add_ingestion_metadata(rows: list[dict], operation: str, property_type: str) -> None:
    """Agrega las columnas de bronze (fuente_datos/operacion/tipo_propiedad/
    ingestado_en) in-place -- constantes para toda la corrida, ya que este
    extractor está scopeado a una sola combinación operation+property_type
    (a diferencia de scrape_buscandocasa.py, que mezcla las 21 en un archivo)."""
    ingestado_en = datetime.now(timezone.utc).isoformat()
    for row in rows:
        row["fuente_datos"] = DATA_SOURCE
        row["operacion"] = operation
        row["tipo_propiedad"] = property_type
        row["ingestado_en"] = ingestado_en


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "listing_id",
        "barrio",
        "direccion",
        "latitud",
        "longitud",
        "dormitorios",
        "banos",
        "metraje_m2",
        "precio",
        "estado",
        "publicado_en",
        "descripcion",
        "amenities",
        "fotos",
        "foto_count",
        "fuente_datos",
        "operacion",
        "tipo_propiedad",
        "ingestado_en",
        "datos_crudos",
    ]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows -> {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation", default="alquiler", help="alquiler | venta")
    parser.add_argument("--property-type", default="apartamentos")
    parser.add_argument("--city", default="montevideo")
    parser.add_argument("--max-pages", type=int, default=None, help="límite de páginas (default: todas)")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS, help="segundos entre requests")
    args = parser.parse_args()

    rows = scrape(args.operation, args.property_type, args.city, args.max_pages, args.delay)
    if not rows:
        print("No se obtuvo ningún aviso -- no se sobreescribe data/raw/listings.csv", file=sys.stderr)
        sys.exit(1)

    add_ingestion_metadata(rows, args.operation, args.property_type)
    write_csv(DATA_LAYER_ROOT / "data" / "raw" / "listings.csv", rows)


if __name__ == "__main__":
    main()
