"""
Lambda de extracción de InfoCasas: el paso de scraping del pipeline semanal
(infra/pipeline.yaml → función `${Project}-${Environment}-scraper-infocasas`).

No reimplementa nada: usa etl/extract/scrape_infocasas.py, el mismo extractor
que se corre a mano en local. Lo único que agrega es lo que cambia en la nube:
dónde escribe (S3 en vez del disco) y en cuántas tandas se parte la corrida.

**Dos modos**, los dos invocados por la state machine:

- `{"modo": "plan", "fecha": "2026-10-30"}` pega una sola vez a la primera
  página de cada orden, lee cuántas páginas hay hoy y devuelve la lista de
  tandas a correr. El total de avisos del sitio cambia semana a semana, así
  que el plan se calcula en cada corrida en vez de quedar fijo en el template.

- `{"modo": "extract", "fecha": ..., "order": 1, "start_page": 1, "pages": 150}`
  corre esa tanda y sube un CSV a
  `s3://$RAW_BUCKET/fuente=infocasas/fecha=<fecha>/listings-order<order>-p<desde>-<hasta>.csv`.

Por qué en tandas: una corrida completa son ~359 páginas por orden × 2
órdenes a 1,2 s por request (~18 min) y el timeout máximo de Lambda es 15.
Las tandas corren **una después de la otra** (el Map de la state machine usa
MaxConcurrency 1), no en paralelo: así el ritmo de requests al sitio queda
igual que corriendo el extractor a mano. Ver docs/scraping-strategy.md.

**Contrato que cumple esta fuente** -- lo mismo que tiene que cumplir
cualquier fuente nueva (ver infra/README.md):

1. Escribe CSVs bajo `fuente=<nombre>/fecha=<YYYY-MM-DD>/` en el bucket raw.
2. Las columnas son las del contrato de bronze (las de `write_csv()` en
   etl/extract/scrape_infocasas.py).
3. La `fecha` se la da la state machine y es la misma para toda la corrida:
   relanzar la semana pisa los mismos objetos en vez de duplicarlos.
"""

import math
import os
import shutil
from pathlib import Path

import requests

import scrape_infocasas as infocasas

FUENTE = os.environ.get("FUENTE", "infocasas")
# El extractor cubre una sola combinación operación+tipo por corrida.
OPERACION = os.environ.get("OPERACION", "alquiler")
TIPO_PROPIEDAD = os.environ.get("TIPO_PROPIEDAD", "apartamentos")
CIUDAD = os.environ.get("CIUDAD", "montevideo")
DELAY_SEGUNDOS = float(os.environ.get("DELAY_SEGUNDOS", infocasas.DEFAULT_DELAY_SECONDS))
# 150 páginas ≈ 4,5 min a 1,2 s por request: holgado contra el timeout de 15
# min, incluso si el sitio responde más lento que de costumbre.
PAGINAS_POR_TANDA = int(os.environ.get("PAGINAS_POR_TANDA", "150"))

# Único directorio escribible en Lambda.
TMP_ROOT = Path("/tmp/cuadra-data")


def _s3():
    # boto3 viene en el runtime de Lambda; se importa acá y no arriba para que
    # los tests de las funciones puras no necesiten tenerlo instalado.
    import boto3

    return boto3.client("s3")


def _sesion() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": infocasas.USER_AGENT, "Accept-Language": "es-UY,es;q=0.9"})
    return session


def tandas_de(total_paginas: int, paginas_por_tanda: int) -> list[dict]:
    """Parte [1..total_paginas] en tandas de a lo sumo `paginas_por_tanda`."""
    if total_paginas < 1:
        return []
    if paginas_por_tanda < 1:
        raise ValueError(f"paginas_por_tanda tiene que ser >= 1, no {paginas_por_tanda}")
    cantidad = math.ceil(total_paginas / paginas_por_tanda)
    tandas = []
    for i in range(cantidad):
        start_page = i * paginas_por_tanda + 1
        # La última tanda no se pasa del total: así no se gastan requests en
        # páginas vacías (el extractor igual cortaría por hasMorePages).
        tandas.append({"start_page": start_page, "pages": min(paginas_por_tanda, total_paginas - start_page + 1)})
    return tandas


def clave_de_tanda(fuente: str, fecha: str, order: int, start_page: int, pages: int) -> str:
    """Clave en el bucket raw. Determinística: relanzar la misma corrida
    sobreescribe el mismo objeto."""
    return (
        f"fuente={fuente}/fecha={fecha}/"
        f"listings-order{order}-p{start_page}-{start_page + pages - 1}.csv"
    )


def _planificar(fecha: str) -> dict:
    session = _sesion()
    total_por_orden = {}
    tandas = []
    for order in infocasas.STABLE_ORDERS:
        total = infocasas.plan_pages(session, OPERACION, TIPO_PROPIEDAD, CIUDAD, order)
        total_por_orden[str(order)] = total
        for tanda in tandas_de(total, PAGINAS_POR_TANDA):
            # Cada tanda viaja con todo lo que la invocación necesita, así el
            # Map de la state machine la pasa tal cual como payload.
            tandas.append({"modo": "extract", "fecha": fecha, "order": order, **tanda})
        print(f"order={order}: {total} páginas -> {math.ceil(total / PAGINAS_POR_TANDA)} tanda(s)")

    return {
        "fuente": FUENTE,
        "fecha": fecha,
        "total_paginas": total_por_orden,
        "paginas_por_tanda": PAGINAS_POR_TANDA,
        "tandas": tandas,
    }


def _extraer(fecha: str, order: int, start_page: int, pages: int) -> dict:
    raw_bucket = os.environ["RAW_BUCKET"]

    rows = infocasas.scrape(
        OPERACION,
        TIPO_PROPIEDAD,
        CIUDAD,
        max_pages=pages,
        delay=DELAY_SEGUNDOS,
        orders=(order,),
        start_page=start_page,
    )

    clave = clave_de_tanda(FUENTE, fecha, order, start_page, pages)
    if not rows:
        # Tanda vacía: pasa si el sitio achicó el total entre el plan y esta
        # invocación. No es un fallo -- las otras tandas sí traen datos y el
        # job de Glue dedupea por listing_id.
        print(f"tanda sin avisos (order={order}, desde la página {start_page}) -- no se sube nada")
        return {"fuente": FUENTE, "fecha": fecha, "order": order, "start_page": start_page, "avisos": 0}

    infocasas.add_ingestion_metadata(rows, OPERACION, TIPO_PROPIEDAD)

    destino = TMP_ROOT / Path(clave).name
    shutil.rmtree(TMP_ROOT, ignore_errors=True)
    infocasas.write_csv(destino, rows)

    _s3().upload_file(str(destino), raw_bucket, clave)
    print(f"subido s3://{raw_bucket}/{clave} ({len(rows)} avisos, {destino.stat().st_size} bytes)")
    destino.unlink(missing_ok=True)

    return {
        "fuente": FUENTE,
        "fecha": fecha,
        "order": order,
        "start_page": start_page,
        "pages": pages,
        "avisos": len(rows),
        "clave": clave,
    }


def handler(event, context):
    modo = event.get("modo", "extract")
    fecha = event["fecha"]

    if modo == "plan":
        return _planificar(fecha)
    if modo == "extract":
        return _extraer(fecha, int(event["order"]), int(event["start_page"]), int(event["pages"]))
    raise ValueError(f"modo desconocido: {modo!r} (plan | extract)")
