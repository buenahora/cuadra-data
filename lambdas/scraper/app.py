"""
Handler de las tres Lambdas de scraping del pipeline semanal
(infra/pipeline.yaml: scraper-infocasas, scraper-casasweb, scraper-buscandocasa).

Las tres comparten este mismo código: la variable de entorno FUENTE, que
setea CloudFormation, decide qué extractor correr. El CI sube el mismo zip
con los tres nombres de archivo que espera el template.

No reimplementa nada: llama al `main()` de etl/extract/scrape_*.py, el mismo
que se corre a mano en local. Lo único que cambia es dónde escribe: en
Lambda el disco es de solo lectura salvo /tmp, así que se redirige
DATA_LAYER_ROOT a /tmp antes de importar el extractor (los módulos hacen
`from cuadra_data.config import DATA_LAYER_ROOT`, o sea que leen el valor
una sola vez, al importarse) y después se sube lo que quedó en
/tmp/.../data/raw/ al bucket raw.

Límite a tener en cuenta: el timeout máximo de Lambda es 15 min y una
corrida completa de InfoCasas tarda ~18 (ver docstring de
etl/extract/scrape_infocasas.py). Por eso MAX_PAGES / MAX_ZONAS / COMBOS son
variables de entorno: hoy hay que acotar la corrida. Si se necesita la
corrida completa, el extractor tiene que pasar a Fargate o a un job de Glue
en vez de Lambda -- ver docs/scraping-strategy.md.
"""

import os
import shutil
import sys
from pathlib import Path

import boto3

TMP_ROOT = Path("/tmp/cuadra-data")

# Tiene que pasar antes de importar cualquier etl/extract/scrape_*.py.
import cuadra_data.config as _cfg  # noqa: E402

_cfg.DATA_LAYER_ROOT = TMP_ROOT

s3 = boto3.client("s3")


def _argv_de_fuente(fuente: str) -> tuple[str, list[str]]:
    """Devuelve (módulo, argv) para la fuente pedida. Los límites salen del
    entorno porque el timeout de Lambda no da para la corrida completa."""
    if fuente == "infocasas":
        argv = ["scrape_infocasas.py"]
        if os.environ.get("MAX_PAGES"):
            argv += ["--max-pages", os.environ["MAX_PAGES"]]
        return "scrape_infocasas", argv

    if fuente == "casasweb":
        argv = ["scrape_casasweb.py"]
        if os.environ.get("MAX_ZONAS"):
            argv += ["--max-zonas", os.environ["MAX_ZONAS"]]
        return "scrape_casasweb", argv

    if fuente == "buscandocasa":
        out = TMP_ROOT / "data" / "raw" / "listings_buscandocasa.csv"
        argv = ["scrape_buscandocasa.py", "--out", str(out)]
        if os.environ.get("COMBOS"):
            argv += ["--combos", *os.environ["COMBOS"].split()]
        return "scrape_buscandocasa", argv

    raise ValueError(f"FUENTE desconocida: {fuente!r} (infocasas | casasweb | buscandocasa)")


def handler(event, context):
    fuente = os.environ["FUENTE"]
    raw_bucket = os.environ["RAW_BUCKET"]
    # La fecha la calcula el primer estado de la state machine y es la misma
    # para toda la corrida: relanzar la semana pisa los mismos objetos en vez
    # de duplicarlos (ver infra/pipeline.yaml).
    fecha = event["fecha"]

    raw_dir = TMP_ROOT / "data" / "raw"
    shutil.rmtree(TMP_ROOT, ignore_errors=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    modulo, argv = _argv_de_fuente(fuente)
    extractor = __import__(modulo)

    sys.argv = argv
    try:
        extractor.main()
    except SystemExit as e:
        # Los extractores hacen sys.exit(1) cuando no obtuvieron ni un aviso.
        # Para la state machine eso es un fallo de la fuente, no un éxito
        # vacío: avisa por SNS y sigue con las otras.
        if e.code:
            raise RuntimeError(f"El extractor de {fuente} no devolvió avisos") from e

    archivos = sorted(p for p in raw_dir.glob("*.csv") if p.is_file())
    if not archivos:
        raise RuntimeError(f"El extractor de {fuente} no dejó ningún CSV en {raw_dir}")

    claves = []
    for archivo in archivos:
        clave = f"fuente={fuente}/fecha={fecha}/{archivo.name}"
        s3.upload_file(str(archivo), raw_bucket, clave)
        claves.append(clave)
        print(f"subido s3://{raw_bucket}/{clave} ({archivo.stat().st_size} bytes)")

    return {"fuente": fuente, "fecha": fecha, "archivos": claves}
