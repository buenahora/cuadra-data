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

AVISOS_DDL = """
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

GOLD_SQL = '''
-- ---------------------------------------------------------------- gold en Postgres
-- Lo que acá se construye es lo que en el proyecto dbt son los marts
-- listings_unified / market_listings / market_stats. Se replica en SQL de
-- Postgres porque dbt no corre en AWS: la state machine es
-- Scraping -> Glue -> fin, así que sin esto la unica tabla que existe en la
-- base es `avisos` (cruda) y la API no tiene de donde leer.
--
-- La capa de staging del dbt (stg_listings) no se replica: su trabajo era
-- parsear strings de bronze a tipos (los 25 try_cast), y eso ya lo hizo
-- normalizar() antes de escribir `avisos`, cuyas columnas ya son integer /
-- double precision. Lo que se porta son los marts, que no tienen ni un
-- try_cast.
--
-- Diferencias de dialecto respecto del dbt, todas equivalentes:
--   median(x)            -> percentile_cont(0.5) within group (order by x)
--   quantile_cont(x, q)  -> percentile_cont(q)   within group (order by x)
--   string_split(x, '|') -> string_to_array(x, '|')
--   QUALIFY row_number() -> subconsulta con where rn = 1
--   list_filter/list_position (dedup de fotos preservando orden)
--                        -> unnest ... with ordinality + group by + min(orden)
--
-- Y una cosa que el dbt no tenía que resolver: `raw.listings` era una foto
-- única, mientras `avisos` acumula una corrida por fecha. Se toma solo la
-- fecha más reciente, que es el equivalente a esa foto -- y además hace que
-- un aviso que desapareció del sitio deje de aparecer, en vez de quedar
-- vivo para siempre.

drop table if exists listings_unified cascade;
create table listings_unified as
with ultima_corrida as (
    select * from avisos where fecha = (select max(fecha) from avisos)
),

-- Equivalente de stg_listings: solo las normalizaciones, sin los try_cast.
renombrado as (
    select
        listing_id,
        fuente_datos,
        trim(barrio) as subbarrio,
        -- macro barrio_canonico: funde los 13 barrios "finos" que el INE no
        -- distingue con su barrio oficial, para que la misma zona no quede
        -- partida en dos zone_id.
        case upper(trim(barrio))
            when 'VILLA BIARRITZ'    then 'Punta Carretas'
            when 'POCITOS NUEVO'     then 'Pocitos'
            when 'PUERTO BUCEO'      then 'Buceo'
            when 'PUERTO DEL BUCEO'  then 'Buceo'
            when 'PARQUE MIRAMAR'    then 'Carrasco'
            when 'CARRASCO ESTE'     then 'Carrasco'
            when 'BARRIO PARQUES'    then 'Carrasco'
            when 'BARRA DE CARRASCO' then 'Carrasco'
            when 'GOLF'              then 'Carrasco Norte'
            when 'GOES'              then 'La Comercial'
            when 'PASO MOLINO'       then 'La Teja'
            when 'VILLA DOLORES'     then 'Parque Batlle'
            when 'BELLA VISTA'       then 'Capurro'
            else trim(barrio)
        end as barrio,
        direccion,
        latitud,
        longitud,
        -- macro normalize_operacion
        case
            when lower(trim(operacion)) = 'temporal' then 'temporada'
            else lower(trim(operacion))
        end as operacion,
        -- macro normalize_tipo_propiedad
        case
            when lower(trim(tipo_propiedad)) in ('apartamentos', 'apartamento') then 'apartamento'
            when lower(trim(tipo_propiedad)) in ('casas', 'casa') then 'casa'
            else lower(trim(tipo_propiedad))
        end as tipo_propiedad,
        dormitorios,
        -- macro dormitorios_bucket
        case
            when dormitorios is null then null
            when dormitorios >= 3 then '3+'
            else dormitorios::text
        end as dormitorios_bucket,
        banos,
        metraje_m2,
        -- macro normalize_precio: avisos.precio ya es double precision, así
        -- que solo queda el redondeo a pesos enteros.
        round(precio::numeric, 0)::numeric(12, 0) as precio,
        estado,
        estado = 'activo' as es_activo,
        -- publicado_en / ingestado_en son text en avisos. Postgres no tiene
        -- try_cast: el regex hace de guarda para que una fecha malformada
        -- quede NULL en vez de tumbar la corrida entera.
        case
            when publicado_en ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
            then substring(publicado_en, 1, 10)::date
        end as publicado_en,
        descripcion,
        case
            when amenities is null or trim(amenities) = '' then array[]::text[]
            else string_to_array(amenities, '|')
        end as amenities,
        -- macro parse_fotos: separa por "|" y dedupe preservando el orden
        -- original (InfoCasas repite urls dentro de la galería, y eso
        -- inflaba la cantidad de fotos y podía cambiar la portada).
        case
            when fotos is null or trim(fotos) = '' then array[]::text[]
            else (
                select coalesce(array_agg(u order by o), array[]::text[])
                from (
                    select u, min(o) as o
                    from unnest(string_to_array(fotos, '|')) with ordinality as t(u, o)
                    where u <> ''
                    group by u
                ) d
            )
        end as fotos,
        foto_count,
        case
            when ingestado_en ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
            then replace(substring(ingestado_en, 1, 19), 'T', ' ')::timestamp
        end as ingestado_en
    from ultima_corrida
),

-- zone_id aparte: depende de `barrio` ya canonizado, así que no puede salir
-- en el mismo select. macro zone_slug, con replace encadenado en vez de
-- unaccent para no depender de una extensión de Postgres (mismo criterio que
-- el macro original, que lo evitaba para no depender de ICU en DuckDB).
con_zona as (
    select
        r.*,
        replace(replace(replace(replace(replace(replace(replace(
            lower(r.barrio),
        'á', 'a'), 'é', 'e'), 'í', 'i'), 'ó', 'o'), 'ú', 'u'), 'ñ', 'n'), ' ', '_') as zone_id
    from renombrado r
),

-- Alcance del producto, igual que en listings_unified.sql: solo alquiler,
-- sin apartamentos sin dormitorios, sin avisos más viejos que un año
-- (max_listing_age_years), y solo las fuentes con los campos completos.
filtrado as (
    select *
    from con_zona
    where operacion = 'alquiler'
      and not (tipo_propiedad = 'apartamento' and dormitorios is null)
      and publicado_en >= current_date - interval '1 year'
      and fuente_datos in ('infocasas', 'synthetic')
),

-- Dedup de re-listados: InfoCasas resubía el mismo aviso con listing_id
-- nuevo, y esa masa de precio idéntico fijaba la mediana del grupo. Firma
-- estricta a propósito, para no fusionar unidades distintas de un
-- desarrollo nuevo que comparten precio y metraje.
numerado as (
    select *,
        row_number() over (
            partition by fuente_datos, zone_id, tipo_propiedad, dormitorios, metraje_m2, precio
            order by publicado_en desc, listing_id
        ) as rn
    from filtrado
)
select
    listing_id, fuente_datos, barrio, subbarrio, zone_id, direccion,
    latitud, longitud, operacion, tipo_propiedad, dormitorios,
    dormitorios_bucket, banos, metraje_m2, precio, estado, es_activo,
    publicado_en, descripcion, amenities, fotos, foto_count, ingestado_en
from numerado
where rn = 1;

-- ---------------------------------------------------------------- market_listings
-- Avisos activos de apartamento en alquiler, sin outliers de precio por
-- zona+dormitorios. El filtro es mediana +/- 1.5*IQR, NO el fence clásico
-- de Tukey (q1/q3 como centro) -- decisión explícita del modelo original,
-- no un error al portar.
--
-- Los grupos con menos de min_sample_size avisos se saltean el filtro: con
-- tan pocos puntos el IQR puede dar 0 y descartar avisos legítimos, y esos
-- grupos ya quedan marcados muestra_confiable = false en market_stats.
drop table if exists market_listings cascade;
create table market_listings as
with activos as (
    select * from listings_unified
    where es_activo and tipo_propiedad = 'apartamento'
),
stats_por_grupo as (
    select
        zone_id,
        dormitorios_bucket,
        count(*) as n,
        percentile_cont(0.5)  within group (order by precio) as mediana_bruta,
        percentile_cont(0.25) within group (order by precio) as q1,
        percentile_cont(0.75) within group (order by precio) as q3
    from activos
    group by 1, 2
),
limites as (
    select
        zone_id,
        dormitorios_bucket,
        n,
        mediana_bruta - 1.5 * (q3 - q1) as limite_inferior,
        mediana_bruta + 1.5 * (q3 - q1) as limite_superior
    from stats_por_grupo
)
select a.*
from activos a
join limites l
    on a.zone_id = l.zone_id
   and a.dormitorios_bucket is not distinct from l.dormitorios_bucket
where l.n < 5
   or a.precio between l.limite_inferior and l.limite_superior;

-- ---------------------------------------------------------------- market_stats
-- Mediana/min/max y tamaño de muestra por zona+tipología. Agrupa por
-- zone_id y no por barrio: la misma zona puede llegar con grafías distintas
-- y agrupar por barrio la partiría en más de una fila, rompiendo la
-- unicidad de zone_id+dormitorios_bucket. `barrio` de salida es una grafía
-- estable (min()) solo para mostrar.
--
-- variacion_interanual_pct queda NULL: en el dbt salía de stg_market_history,
-- un seed que en Postgres todavía no existe. NULL y no 0 a propósito -- son
-- cosas distintas ("no sabemos" vs "no cambió"). Se puede llenar cuando
-- market_snapshots acumule 12 meses de corridas.
drop table if exists market_stats cascade;
create table market_stats as
with agg as (
    select
        zone_id,
        min(barrio) as barrio,
        dormitorios_bucket,
        count(*) as avisos_activos,
        min(precio) as precio_min,
        percentile_cont(0.5) within group (order by precio) as precio_mediana,
        max(precio) as precio_max
    from market_listings
    group by zone_id, dormitorios_bucket
)
select
    agg.*,
    agg.avisos_activos >= 5 as muestra_confiable,
    null::numeric as variacion_interanual_pct,
    current_date as actualizado_en
from agg;

-- ---------------------------------------------------------------- zonas
-- Catálogo de zonas para la API. Equivale a lo que el backend leía del
-- warehouse DuckDB uniendo seed_barrios con las zonas que aparecen en los
-- avisos: acá no hay seed, así que sale de los avisos directamente. El
-- backend necesita una fila por cada zone_id que aparezca en un aviso o la
-- ruta de listings falla con KeyError al buscar zones[listing.zone_id].
drop table if exists zonas cascade;
create table zonas as
select
    zone_id,
    min(barrio) as barrio,
    'Montevideo'::text as ciudad
from listings_unified
group by zone_id;

create index if not exists idx_ml_zona on market_listings (zone_id, dormitorios_bucket);
create index if not exists idx_lu_zona on listings_unified (zone_id);
'''

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
    parser.add_argument("--rds_ca_s3_uri", required=True)
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


def _contexto_ssl(uri_ca: str):
    """Contexto TLS que confía en la CA de RDS.

    El certificado de RDS lo firma una CA propia de Amazon que no está en el
    almacén de confianza del contenedor de Glue, así que ssl_context=True
    falla con "SSL: CERTIFICATE_VERIFY_FAILED ... self signed certificate in
    certificate chain". (La AMI de la app sí la trae, por eso el backend
    conecta sin esto.)

    El bundle llega por S3 y no por descarga directa: el job corre en subredes
    privadas sin NAT, así que no tiene salida a internet -- solo los VPC
    endpoints. El CI lo baja y lo sube junto al resto de los artefactos.

    Se verifica de verdad (create_default_context valida cadena y hostname);
    no se desactiva la verificación.
    """
    import ssl

    bucket, _, clave = uri_ca.removeprefix("s3://").partition("/")
    destino = "/tmp/rds-ca-bundle.pem"
    _cliente("s3").download_file(bucket, clave, destino)
    return ssl.create_default_context(cafile=destino)


def cargar_en_postgres(df: pd.DataFrame, db_secret_arn: str, fecha: str, uri_ca: str) -> None:
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
        ssl_context=_contexto_ssl(uri_ca),
    )
    try:
        cur = conn.cursor()
        cur.execute(AVISOS_DDL)
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

        # `avisos` sola no le sirve a la API: necesita las tablas gold. Se
        # construyen acá, en la misma conexión, porque dbt no corre en AWS
        # (ver el comentario arriba de GOLD_SQL).
        construir_gold(cur)
        conn.commit()
    finally:
        conn.close()


def construir_gold(cur) -> None:
    """Reconstruye listings_unified / market_listings / market_stats / zonas.

    Full refresh, no incremental: son tablas chicas (miles de filas) y se
    derivan enteras de la última corrida de `avisos`, así que recrearlas es
    más simple y más difícil de dejar inconsistente que mantenerlas al día.
    """
    cur.execute(GOLD_SQL)
    for tabla in ("listings_unified", "market_listings", "market_stats", "zonas"):
        cur.execute(f"SELECT COUNT(*) FROM {tabla}")
        print(f"gold {tabla}: {cur.fetchone()[0]} filas")


def main() -> None:
    args = opciones(sys.argv[1:])
    crudo = leer_raw(args.raw_bucket, args.fecha)
    avisos = normalizar(crudo)
    escribir_curated(avisos, args.curated_bucket, args.fecha)
    cargar_en_postgres(avisos, args.db_secret_arn, args.fecha, args.rds_ca_s3_uri)


if __name__ == "__main__":
    main()
