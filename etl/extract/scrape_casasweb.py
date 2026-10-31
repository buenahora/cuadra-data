"""
Extract stage — extractor real de Casasweb (segunda fuente además de
InfoCasas; ver docs/scraping-strategy.md para InfoCasas y la conversación
del mismo día para el research de Casasweb).

Escribe data/raw/listings_casasweb_{operation}_{property_type}.csv (ej.
listings_casasweb_alquiler_apartamentos.csv) -- un archivo por combinación,
para que correr distintos --operation/--property-type no se pisen entre sí.
Mismo contrato de columnas que
listings.csv de InfoCasas (listing_id, barrio, dormitorios, metraje_m2,
precio, estado, publicado_en), pero en un archivo separado: no pisa el
listings.csv de InfoCasas. Los listing_id de InfoCasas empiezan con "IC" y
los de Casasweb ya vienen prefijados "CW" en su propia referencia interna
(campo `ref` de la API), así que combinar ambas fuentes más adelante es un
merge directo por listing_id, sin colisiones. etl/load/load_to_duckdb.py
descubre y carga este archivo automáticamente (glob de data/raw/listings*.csv,
un raw.<nombre_de_archivo> por CSV) -- no requiere tocar RAW_TABLES.

Cómo funciona: a diferencia de InfoCasas (JSON embebido en HTML) y de la
primera exploración manual de Casasweb (HTML con regex sobre resultados.aspx),
Casasweb expone una API REST pública y documentada:
api.casasweb.com/openapi.json ("API para desarrolladores", linkeada desde
el footer del sitio -- Casasweb resultó ser el motor MLS que sincroniza
Gallito, Inmuebles.com.uy, InfoCasas, MercadoLibre y otros portales, no un
portal aislado). GET /propiedades?contrato=A&tipo=a&depto=1[&zona=][&dorm=][&banos=]
devuelve JSON tipado directo, sin HTML de por medio. robots.txt de
api.casasweb.com no tiene ningún Disallow.

Límite de resultados -- confirmado empíricamente hoy: cada combinación de
filtros devuelve como máximo PAGE_CAP=50 avisos, y no hay parámetro de
offset real (`desde`/`hasta` filtran precio, no paginan -- confirmado
probando ambos con resultados que no respetan el rango pedido). Para cubrir
un departamento completo hay que particionar la consulta hasta que cada
partición quede por debajo del tope:
  1. Por zona (barrio) -- GET /zonas?depto=<id> da la lista completa
     (81 zonas para Montevideo, depto=1).
  2. Si una zona sola pega el tope (ej. Pocitos), particionar además por
     dormitorios (0-5, ver DORM_VALUES).
  3. Si zona+dormitorios sigue en el tope (ej. Pocitos, 1 dormitorio),
     particionar además por baños (0-4, ver BANOS_VALUES).
Con esos 3 ejes esto cierra casi todo. Si una combinación zona+dorm+baños
*sigue* devolviendo exactamente PAGE_CAP avisos, se imprime una advertencia
explícita (mismo espíritu que el reporte de "cobertura vs. total nominal"
de scrape_infocasas.py) en vez de asumir en silencio que está completo --
no hay más ejes de partición documentados en la API para ese caso.

Estado del aviso: la API no expone un flag confiable de activo/pausado
(el campo `vis` no se corresponde con avisos visibles reales -- avisos que
aparecen en una búsqueda pública viven con `vis: false`). Como el endpoint
solo devuelve avisos actualmente publicados, todo se marca "activo".

Moneda: los avisos vienen casi siempre en pesos (`MonA: "$"`). Los que no,
se normalizan con un tipo de cambio fijo de referencia (no hay ningún campo
de tasa de cambio en la respuesta, a diferencia de InfoCasas) -- mismo
placeholder (40 UYU/USD) que usa scrape_infocasas.py.

Run: uv run python etl/extract/scrape_casasweb.py
     uv run python etl/extract/scrape_casasweb.py --max-zonas 5   # muestra rápida
"""

import argparse
import csv
import json
import sys
import time
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path

import requests

from cuadra_data.config import DATA_LAYER_ROOT

DATA_SOURCE = "casasweb"
BASE_HOST = "https://api.casasweb.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
DEFAULT_DELAY_SECONDS = 1.5  # ver fetch_propiedades: la API rate-limitea por ventana de tiempo
DEFAULT_FALLBACK_UYU_PER_USD = 40.0
PAGE_CAP = 50  # confirmado empíricamente: tope duro por combinación de filtros
MAX_RETRIES_429 = 5
INITIAL_BACKOFF_429_SECONDS = 5.0
DEPTO_MONTEVIDEO = 1
DORM_VALUES = range(0, 6)  # spec de la API: dorm entre 0 y 5
BANOS_VALUES = range(0, 5)  # spec de la API: banos entre 0 y 4
OPERATION_TO_CONTRATO = {"alquiler": "A", "venta": "V", "temporal": "T"}
PROPERTY_TYPE_TO_TIPO = {"apartamentos": "a", "casas": "c"}


def robots_allows(session: requests.Session, url: str) -> bool:
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(f"{BASE_HOST}/robots.txt")
    resp = session.get(f"{BASE_HOST}/robots.txt", timeout=15)
    resp.raise_for_status()
    rp.parse(resp.text.splitlines())
    return rp.can_fetch(USER_AGENT, url)


def fetch_zonas(session: requests.Session, depto: int) -> list[dict]:
    resp = session.get(f"{BASE_HOST}/zonas", params={"depto": depto}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_propiedades(session: requests.Session, params: dict) -> list[dict] | None:
    """La API rate-limitea por IP/ventana de tiempo (sin header Retry-After --
    confirmado empíricamente: ráfagas sin delay empiezan a devolver 429 a
    partir del pedido ~4-20, y una vez que aparece un 429 los siguientes
    pedidos inmediatos también fallan). Reintenta con backoff exponencial en
    vez de tratar 429 como una falla permanente -- a diferencia de un 4xx real,
    esperar unos segundos alcanza para que el próximo pedido pase."""
    backoff = INITIAL_BACKOFF_429_SECONDS
    for attempt in range(MAX_RETRIES_429 + 1):
        try:
            resp = session.get(f"{BASE_HOST}/propiedades", params=params, timeout=20)
        except requests.exceptions.RequestException as e:
            print(f"  !! error de red en /propiedades {params}: {e}")
            return None

        if resp.status_code == 429:
            if attempt == MAX_RETRIES_429:
                print(
                    f"  !! HTTP 429 persistente en /propiedades {params} tras "
                    f"{MAX_RETRIES_429} reintentos -- abandono este request"
                )
                return None
            print(f"  .. HTTP 429 en /propiedades {params}, espero {backoff:.0f}s y reintento")
            time.sleep(backoff)
            backoff *= 2
            continue

        if resp.status_code != 200:
            print(f"  !! HTTP {resp.status_code} en /propiedades {params}")
            return None
        try:
            return resp.json()
        except ValueError as e:
            print(f"  !! JSON inválido en /propiedades {params}: {e}")
            return None
    return None


def normalize_row(item: dict, fallback_uyu_per_usd: float) -> dict | None:
    barrio = item.get("Zona")
    dormitorios = item.get("dorms")
    ref = item.get("ref")
    precio_original = item.get("precioA")
    if not barrio or dormitorios is None or not ref or not precio_original:
        return None

    moneda = (item.get("MonA") or "").strip()
    precio_uyu = precio_original if moneda in ("", "$") else round(precio_original * fallback_uyu_per_usd)

    # La API normalmente solo trae un ID numérico (`foto`), sin URL --
    # reversado el 2026-08-14 contra la página pública de detalle
    # (casasweb.com/CW{ref}), que arma la portada como /fotos/{foto}u.jpg
    # ("u" = variante grande; confirmado, devuelve image/jpeg real). PERO
    # para avisos sindicados desde otras inmobiliarias/MLS (Casasweb agrega
    # Gallito/Inmuebles.com.uy/InfoCasas/MercadoLibre y otros, ver docstring
    # del módulo), `foto` a veces ya es una URL externa completa (ej. a
    # static.tokkobroker.com) en vez de un ID -- bug real encontrado el
    # 2026-08-14 al conectar esto al backend: armar casasweb.com/fotos/{foto}u.jpg
    # sobre una URL ya completa produce una URL duplicada/rota. Se usa tal
    # cual cuando ya es una URL. No hay conteo total de fotos en esta
    # respuesta -- pedirlo requeriría ir a la ficha de detalle por aviso,
    # fuera de alcance (mismo criterio "nivel tarjeta" que las otras fuentes).
    foto = item.get("foto")
    if not foto:
        foto_url = ""
    elif str(foto).startswith("http"):
        foto_url = str(foto)
    else:
        foto_url = f"https://casasweb.com/fotos/{foto}u.jpg"

    # datos_crudos -- agregado 2026-08-20 (re-ingesta completa tras el
    # incidente de borrado del mismo día): el item completo de /propiedades
    # tal cual, sin curar (titulo, Resumen, inmobiliaria, garaje, expensas,
    # Estado, etc.) -- ninguno de estos tiene columna propia hoy, pero ya
    # viajan gratis en la misma respuesta. Sin descripción/amenities/
    # dirección/coordenadas: investigado hoy (API documentada + página
    # pública de detalle), Casasweb no las expone en ningún lado -- no es
    # que falte capturarlas, no están disponibles en la fuente (ver
    # docstring del módulo y stg_listings_casasweb.sql).
    datos_crudos = json.dumps(item, ensure_ascii=False)

    return {
        "listing_id": ref,
        "barrio": barrio,
        "dormitorios": dormitorios,
        "banos": item.get("banos") if item.get("banos") is not None else "",
        "metraje_m2": item.get("areaEdificada") or "",
        "precio": precio_uyu,
        "estado": "activo",
        "publicado_en": item.get("fechaMod") or "",
        # Un solo elemento por ahora -- "fotos" (mismo nombre de columna que
        # InfoCasas/BuscandoCasa, y que ListingOut.fotos en el backend, ver
        # conversación 2026-08-14) queda listo para más de una URL el día
        # que se scrapee la ficha de detalle, pero hoy Casasweb solo expone
        # la portada.
        "fotos": foto_url,
        "foto_count": "",
        "datos_crudos": datos_crudos,
    }


def collect_zona(
    session: requests.Session,
    contrato: str,
    tipo: str,
    depto: int,
    zona: int,
    delay: float,
) -> list[dict]:
    """Trae todos los avisos de una zona, particionando por dormitorios y
    (si hace falta) baños cuando la respuesta pega el tope de PAGE_CAP. Ver
    docstring del módulo para el porqué de este esquema de 3 ejes."""
    base = {"contrato": contrato, "tipo": tipo, "depto": depto, "zona": zona}
    items = fetch_propiedades(session, base)
    time.sleep(delay)
    if items is None:
        return []
    if len(items) < PAGE_CAP:
        return items

    merged: dict[str, dict] = {}
    for dorm in DORM_VALUES:
        sub = fetch_propiedades(session, {**base, "dorm": dorm})
        time.sleep(delay)
        if sub is None:
            continue
        if len(sub) < PAGE_CAP:
            merged.update({it["ref"]: it for it in sub if it.get("ref")})
            continue
        for banos in BANOS_VALUES:
            subsub = fetch_propiedades(session, {**base, "dorm": dorm, "banos": banos})
            time.sleep(delay)
            if subsub is None:
                continue
            if len(subsub) >= PAGE_CAP:
                print(
                    f"  !! posible truncamiento: zona={zona} dorm={dorm} banos={banos} "
                    f"devolvió {len(subsub)} avisos (tope={PAGE_CAP}) -- puede faltar data, "
                    "no hay más ejes de partición disponibles en la API."
                )
            merged.update({it["ref"]: it for it in subsub if it.get("ref")})
    return list(merged.values())


def scrape(
    operation: str,
    property_type: str,
    depto: int,
    delay: float,
    max_zonas: int | None,
) -> list[dict]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    contrato = OPERATION_TO_CONTRATO[operation]
    tipo = PROPERTY_TYPE_TO_TIPO[property_type]

    probe_url = f"{BASE_HOST}/propiedades?contrato={contrato}&tipo={tipo}&depto={depto}"
    if not robots_allows(session, probe_url):
        raise RuntimeError(
            f"robots.txt de {BASE_HOST} no permite crawlear {probe_url} -- abortando."
        )

    zonas = fetch_zonas(session, depto)
    if max_zonas is not None:
        zonas = zonas[:max_zonas]
    print(f"{len(zonas)} zonas a recorrer para depto={depto}")

    rows: dict[str, dict] = {}
    skipped_incomplete = 0
    for i, z in enumerate(zonas, 1):
        zona_id = z["zona"]
        nombre = z["nombre_zona"]
        items = collect_zona(session, contrato, tipo, depto, zona_id, delay)
        print(f"[{i}/{len(zonas)}] {nombre} (zona={zona_id}): {len(items)} avisos")
        for item in items:
            row = normalize_row(item, DEFAULT_FALLBACK_UYU_PER_USD)
            if row is None:
                skipped_incomplete += 1
                continue
            rows[row["listing_id"]] = row

    print(
        f"Total: {len(rows)} avisos únicos en {len(zonas)} zonas -- "
        f"{skipped_incomplete} descartados por datos incompletos (sin barrio/dormitorios/precio/ref)"
    )
    return list(rows.values())


def add_ingestion_metadata(rows: list[dict], operation: str, property_type: str) -> None:
    """Ver docstring homólogo en scrape_infocasas.py -- constantes para toda
    la corrida, esta API también está scopeada a una sola combinación
    operation+property_type por invocación."""
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
        "dormitorios",
        "banos",
        "metraje_m2",
        "precio",
        "estado",
        "publicado_en",
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
    parser.add_argument("--operation", default="alquiler", choices=sorted(OPERATION_TO_CONTRATO))
    parser.add_argument("--property-type", default="apartamentos", choices=sorted(PROPERTY_TYPE_TO_TIPO))
    parser.add_argument("--depto", type=int, default=DEPTO_MONTEVIDEO)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS, help="segundos entre requests")
    parser.add_argument(
        "--max-zonas", type=int, default=None, help="límite de zonas a recorrer (default: todas) -- muestra rápida"
    )
    args = parser.parse_args()

    rows = scrape(args.operation, args.property_type, args.depto, args.delay, args.max_zonas)
    out_name = f"listings_casasweb_{args.operation}_{args.property_type}.csv"
    if not rows:
        print(f"No se obtuvo ningún aviso -- no se sobreescribe {out_name}", file=sys.stderr)
        sys.exit(1)

    add_ingestion_metadata(rows, args.operation, args.property_type)
    write_csv(DATA_LAYER_ROOT / "data" / "raw" / out_name, rows)


if __name__ == "__main__":
    main()
