"""
Job de Glue (python shell) del pipeline semanal: raw -> curated -> Postgres.
Lo despliega infra/pipeline.yaml (TransformJob) y lo invoca la state machine
después de la extracción.

Qué hace:
  1. lee los CSV que dejaron las Lambdas en s3://<raw>/fuente=*/fecha=<fecha>/
  2. los lleva al contrato de bronze (COLUMNAS, abajo), tipado y dedupeado
  3. escribe un parquet en s3://<curated>/avisos/fecha=<fecha>/
  4. carga esa partición en Postgres (reemplaza la de esa fecha: relanzar la
     corrida no duplica filas)

Es fuente-agnóstico a propósito: descubre las fuentes por el prefijo
`fuente=` del bucket raw y lleva cada archivo al mismo contrato de columnas.
Sumar una fuente nueva no requiere tocar este archivo -- alcanza con que su
extractor escriba las columnas de COLUMNAS (las que faltan quedan NULL y se
avisan en el log). Ver infra/README.md, "Sumar una fuente".

OJO -- lo que este job NO hace: la limpieza de silver (barrio canónico,
buckets de dormitorios, normalización de tipología) vive en dbt/macros/ y
dbt/models/staging/ y no está portada acá. Curated es bronze tipado, no
silver. Mientras el warehouse productivo no esté decidido
(config/connections.yml, entorno prod = TBD) el "precio vs. mercado" se
sigue calculando con dbt sobre DuckDB.

Dependencias: pandas y pyarrow vienen del library-set analytics; pg8000 viene
de deps.zip (--extra-py-files), que arma el CI.
"""

import argparse
import hashlib
import io
import json
import sys

import pandas as pd

# Contrato de bronze: las columnas que escribe cada extractor
# (write_csv() en etl/extract/scrape_infocasas.py es la referencia) y las que
# tiene la tabla `avisos` del catálogo de Glue (infra/pipeline.yaml).
# `datos_crudos` queda deliberadamente afuera de curated: es el JSON entero
# del aviso, útil para investigar sobre el raw, pesado para una tabla
# analítica.
TEXTO = [
    "listing_id",
    "fuente_datos",
    "barrio",
    "direccion",
    "operacion",
    "tipo_propiedad",
    "estado",
    "descripcion",
    "amenities",
    "fotos",
    # Fechas como string ISO, igual que en el CSV: el tipado fino es trabajo
    # de la capa silver (dbt), no de curated.
    "publicado_en",
    "ingestado_en",
]
ENTEROS = ["dormitorios", "banos", "metraje_m2", "foto_count"]
DECIMALES = ["latitud", "longitud", "precio"]
COLUMNAS = [*TEXTO, *ENTEROS, *DECIMALES, "hash_contenido"]

# Identidad de un aviso (para dedupear entre las tandas y entre los dos
# órdenes con que pagina el extractor de InfoCasas).
CLAVE = ["fuente_datos", "listing_id"]
# Lo que define "este aviso cambió" de una semana a la otra.
CAMPOS_DEL_HASH = ["fuente_datos", "listing_id", "barrio", "dormitorios", "metraje_m2", "precio", "estado"]

def _cliente(servicio: str):
    # boto3 viene en el runtime de Glue; se importa acá y no arriba para que
    # los tests del contrato de columnas (tests/unit/test_glue_transform.py)
    # no necesiten tenerlo instalado.
    import boto3

    return boto3.client(servicio)


def opciones(argv: list[str]) -> argparse.Namespace:
    """Glue pasa los DefaultArguments del job como --clave valor."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--fecha", required=True)
    parser.add_argument("--raw_bucket", required=True)
    parser.add_argument("--curated_bucket", required=True)
    parser.add_argument("--db_secret_arn", required=True)
    args, _ignorados = parser.parse_known_args(argv)
    return args


def leer_raw(raw_bucket: str, fecha: str) -> pd.DataFrame:
    """Todos los CSV de la fecha, de todas las fuentes, concatenados. La
    fuente sale del prefijo `fuente=<x>/` de la clave; si el archivo además
    trae la columna fuente_datos (lo esperable), gana la columna."""
    s3 = _cliente("s3")
    paginador = s3.get_paginator("list_objects_v2")
    partes = []
    for pagina in paginador.paginate(Bucket=raw_bucket):
        for obj in pagina.get("Contents", []):
            clave = obj["Key"]
            if f"/fecha={fecha}/" not in clave or not clave.endswith(".csv"):
                continue
            fuente = clave.split("fuente=", 1)[1].split("/", 1)[0]
            cuerpo = s3.get_object(Bucket=raw_bucket, Key=clave)["Body"].read()
            df = pd.read_csv(io.BytesIO(cuerpo), dtype=str, keep_default_na=False)
            if "fuente_datos" not in df.columns:
                df["fuente_datos"] = fuente
            print(f"leído s3://{raw_bucket}/{clave}: {len(df)} filas (fuente={fuente})")
            partes.append(df)

    if not partes:
        raise RuntimeError(
            f"No hay ningún CSV en s3://{raw_bucket} para fecha={fecha}. "
            f"¿Falló la extracción?"
        )
    return pd.concat(partes, ignore_index=True)


def normalizar(crudo: pd.DataFrame) -> pd.DataFrame:
    faltantes = [c for c in COLUMNAS if c != "hash_contenido" and c not in crudo.columns]
    if faltantes:
        # No es fatal: una fuente puede no tener un campo disponible (ver los
        # comentarios de stg_listings_casasweb.sql). Pero se avisa, porque si
        # falta algo que sí debería venir, el síntoma río abajo es una columna
        # entera en NULL sin ninguna señal.
        print(f"!! columnas del contrato que no vinieron en el raw: {', '.join(faltantes)}")

    salida = pd.DataFrame(index=crudo.index)
    for columna in TEXTO:
        valores = crudo[columna] if columna in crudo.columns else None
        salida[columna] = valores.replace("", None) if valores is not None else None
    for columna in ENTEROS:
        valores = crudo[columna] if columna in crudo.columns else None
        salida[columna] = (
            pd.to_numeric(valores, errors="coerce").astype("Int64") if valores is not None else pd.NA
        )
    for columna in DECIMALES:
        valores = crudo[columna] if columna in crudo.columns else None
        # astype("float64") explícito: si en una corrida todos los precios son
        # enteros, to_numeric devuelve int64 y el parquet sale con esa columna
        # como INT64, que no coincide con el `double` declarado en el catálogo
        # de Glue (AvisosTable) y Athena falla al leer la partición.
        salida[columna] = (
            pd.to_numeric(valores, errors="coerce").astype("float64")
            if valores is not None
            else float("nan")
        )

    def _hash(fila) -> str:
        texto = "|".join("" if pd.isna(v) else str(v) for v in fila)
        return hashlib.sha256(texto.encode("utf-8")).hexdigest()

    salida["hash_contenido"] = salida[CAMPOS_DEL_HASH].apply(_hash, axis=1)

    antes = len(salida)
    salida = salida[salida["listing_id"].notna()]
    # keep="last": si dos tandas trajeron el mismo aviso, el último archivo
    # leído gana (son idénticos salvo que el sitio lo haya editado en el medio).
    salida = salida.drop_duplicates(subset=CLAVE, keep="last")
    print(f"normalizado: {len(salida)} avisos únicos de {antes} filas crudas")
    return salida[COLUMNAS]


def escribir_curated(df: pd.DataFrame, curated_bucket: str, fecha: str) -> str:
    buffer = io.BytesIO()
    df.to_parquet(buffer, index=False, compression="snappy")
    clave = f"avisos/fecha={fecha}/avisos.parquet"
    _cliente("s3").put_object(Bucket=curated_bucket, Key=clave, Body=buffer.getvalue())
    print(f"escrito s3://{curated_bucket}/{clave} ({len(df)} filas)")
    return clave


def cargar_en_postgres(df: pd.DataFrame, db_secret_arn: str, fecha: str) -> None:
    import pg8000.dbapi  # de deps.zip

    secreto = json.loads(
        _cliente("secretsmanager").get_secret_value(SecretId=db_secret_arn)["SecretString"]
    )
    conn = pg8000.dbapi.connect(
        host=secreto["host"],
        port=int(secreto.get("port", 5432)),
        database=secreto.get("dbname", "alquileres"),
        user=secreto["username"],
        password=secreto["password"],
        ssl_context=True,
    )
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS avisos (
              fecha          date             NOT NULL,
              listing_id     text             NOT NULL,
              fuente_datos   text             NOT NULL,
              barrio         text,
              direccion      text,
              operacion      text,
              tipo_propiedad text,
              estado         text,
              descripcion    text,
              amenities      text,
              fotos          text,
              publicado_en   text,
              ingestado_en   text,
              dormitorios    integer,
              banos          integer,
              metraje_m2     integer,
              foto_count     integer,
              latitud        double precision,
              longitud       double precision,
              precio         double precision,
              hash_contenido text,
              PRIMARY KEY (fecha, fuente_datos, listing_id)
            )
            """
        )
        # Idempotencia: la corrida es dueña de su fecha y la reescribe entera.
        cur.execute("DELETE FROM avisos WHERE fecha = %s", (fecha,))
        filas = [
            (fecha, *(None if pd.isna(v) else v for v in fila))
            for fila in df[COLUMNAS].itertuples(index=False, name=None)
        ]
        cur.executemany(
            f"INSERT INTO avisos (fecha, {', '.join(COLUMNAS)}) "
            f"VALUES (%s, {', '.join(['%s'] * len(COLUMNAS))})",
            filas,
        )
        conn.commit()
        print(f"cargadas {len(filas)} filas en Postgres para fecha={fecha}")
    finally:
        conn.close()


def main() -> None:
    args = opciones(sys.argv[1:])
    crudo = leer_raw(args.raw_bucket, args.fecha)
    avisos = normalizar(crudo)
    escribir_curated(avisos, args.curated_bucket, args.fecha)
    cargar_en_postgres(avisos, args.db_secret_arn, args.fecha)


if __name__ == "__main__":
    main()
