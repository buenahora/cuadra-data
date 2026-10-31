"""
Job de Glue (python shell) del pipeline semanal: raw -> curated -> Postgres.
Lo despliega infra/pipeline.yaml (TransformJob) y lo invoca la state machine
después del scraping.

Qué hace:
  1. lee los CSV que dejaron las Lambdas en s3://<raw>/fuente=*/fecha=<fecha>/
  2. los normaliza al esquema de la tabla `avisos` del catálogo de Glue
  3. escribe un parquet en s3://<curated>/avisos/fecha=<fecha>/
  4. carga esa partición en Postgres (reemplaza la de esa fecha: relanzar la
     corrida no duplica filas)

OJO -- la normalización de acá es deliberadamente mínima (mapeo de columnas,
dedupe y hash). La limpieza real (barrio canónico, buckets de dormitorios,
normalización de precio/moneda, tipología) vive hoy en dbt/macros/ y
dbt/models/staging/. Mientras el warehouse productivo no esté decidido (ver
docs/business.md §8 y config/connections.yml, entorno prod = TBD) esa lógica
NO está portada acá: este job deja los datos crudos-pero-tipados en curated y
en Postgres, y el "precio vs. mercado" se sigue calculando con dbt sobre
DuckDB.

Dependencias: pandas y pyarrow vienen del library-set analytics; pg8000 viene
de deps.zip (--extra-py-files), que arma el CI.
"""

import argparse
import hashlib
import io
import json
import sys

import boto3
import pandas as pd

# Columnas de la tabla `avisos` del catálogo (ver infra/pipeline.yaml).
COLUMNAS = [
    "fuente",
    "id_aviso",
    "url",
    "barrio",
    "tipologia",
    "dormitorios",
    "metros",
    "precio",
    "moneda",
    "hash_contenido",
]

# Cómo se llama cada columna del catálogo en los CSV de etl/extract/.
# Varias fuentes traen nombres distintos para el mismo dato.
ORIGEN = {
    "id_aviso": ["listing_id"],
    "url": ["url", "link"],
    "barrio": ["barrio"],
    "tipologia": ["tipo_propiedad", "tipologia"],
    "dormitorios": ["dormitorios"],
    "metros": ["metraje_m2", "metros", "superficie_m2"],
    "precio": ["precio"],
    "moneda": ["moneda"],
}

s3 = boto3.client("s3")


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
    """Un DataFrame por CSV de la fecha, concatenados. La fuente sale del
    prefijo fuente=<x>/ de la clave, no del contenido del archivo."""
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
            df["fuente"] = fuente
            print(f"leído s3://{raw_bucket}/{clave}: {len(df)} filas")
            partes.append(df)

    if not partes:
        raise RuntimeError(
            f"No hay ningún CSV en s3://{raw_bucket} para fecha={fecha}. "
            f"Fallaron las tres Lambdas de scraping?"
        )
    return pd.concat(partes, ignore_index=True)


def normalizar(crudo: pd.DataFrame) -> pd.DataFrame:
    salida = pd.DataFrame(index=crudo.index)
    salida["fuente"] = crudo["fuente"]

    for destino, candidatos in ORIGEN.items():
        columna = next((c for c in candidatos if c in crudo.columns), None)
        salida[destino] = crudo[columna] if columna else None

    salida["dormitorios"] = pd.to_numeric(salida["dormitorios"], errors="coerce").astype("Int32")
    salida["metros"] = pd.to_numeric(salida["metros"], errors="coerce")
    salida["precio"] = pd.to_numeric(salida["precio"], errors="coerce")

    # El hash permite detectar, semana a semana, si un aviso cambió de precio
    # o de descripción sin tener que comparar campo por campo.
    def _hash(fila) -> str:
        crudo_txt = "|".join("" if pd.isna(v) else str(v) for v in fila)
        return hashlib.sha256(crudo_txt.encode("utf-8")).hexdigest()

    salida["hash_contenido"] = salida[
        ["fuente", "id_aviso", "barrio", "dormitorios", "metros", "precio", "moneda"]
    ].apply(_hash, axis=1)

    antes = len(salida)
    salida = salida[salida["id_aviso"].notna() & (salida["id_aviso"] != "")]
    salida = salida.drop_duplicates(subset=["fuente", "id_aviso"], keep="last")
    print(f"normalizado: {len(salida)} avisos únicos de {antes} filas crudas")
    return salida[COLUMNAS]


def escribir_curated(df: pd.DataFrame, curated_bucket: str, fecha: str) -> str:
    buffer = io.BytesIO()
    df.to_parquet(buffer, index=False, compression="snappy")
    clave = f"avisos/fecha={fecha}/avisos.parquet"
    s3.put_object(Bucket=curated_bucket, Key=clave, Body=buffer.getvalue())
    print(f"escrito s3://{curated_bucket}/{clave} ({len(df)} filas)")
    return clave


def cargar_en_postgres(df: pd.DataFrame, db_secret_arn: str, fecha: str) -> None:
    import pg8000.dbapi  # de deps.zip

    secreto = json.loads(
        boto3.client("secretsmanager").get_secret_value(SecretId=db_secret_arn)["SecretString"]
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
              fecha          date         NOT NULL,
              fuente         text         NOT NULL,
              id_aviso       text         NOT NULL,
              url            text,
              barrio         text,
              tipologia      text,
              dormitorios    integer,
              metros         double precision,
              precio         double precision,
              moneda         text,
              hash_contenido text,
              PRIMARY KEY (fecha, fuente, id_aviso)
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
