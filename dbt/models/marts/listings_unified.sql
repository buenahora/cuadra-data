-- Gold: estructura unificada de avisos across las 3 fuentes (InfoCasas,
-- Casasweb, BuscandoCasa), pensada para que la API lea de un solo lugar en
-- vez de conocer el shape de cada fuente. Union directa de los 3 modelos de
-- staging -- cada uno ya llega con el mismo shape de columnas (ver
-- dbt/models/staging/_staging.yml), así que acá no hay más lógica que juntar
-- + el filtro de alcance de abajo.
--
-- listing_id es único cross-fuente por construcción: cada extractor prefija
-- su propio ref con IC/CW/BC (ver docstrings de etl/extract/scrape_*.py).
--
-- fotos es LIST(VARCHAR) -- mismo nombre que ListingOut.fotos en el backend
-- (backend/app/schemas/listing.py), a propósito (ver conversación del
-- 2026-08-14: la idea es no distinguir avisos scrapeados de avisos
-- publicados en la plataforma). Hoy InfoCasas trae la galería completa
-- (gratis, ya viene en el JSON que se descarga); Casasweb/BuscandoCasa
-- traen un solo elemento (la portada) -- conseguir su galería completa
-- requiere ir a la ficha de detalle, no implementado (ver docstring de
-- cada extractor).
--
-- Alcance del producto (decidido -- ver conversación del 2026-08-13): solo
-- alquiler. venta/temporada quedan afuera de este mart (siguen existiendo en
-- bronze/silver -- stg_listings_buscandocasa no filtra nada -- por si hacen
-- falta para otro caso de uso más adelante). También se descartan avisos de
-- tipo_propiedad='apartamento' sin dormitorios (~28 filas, todas
-- BuscandoCasa, gap de parseo documentado en ese extractor) -- volumen
-- chico, no vale la pena investigar más por ahora. El resto de los tipos sin
-- dormitorios (terreno/local/oficina/garage/...) se conservan: ahí es
-- esperado, no un dato faltante.
--
-- Vigencia (decidido -- ver conversación del 2026-08-15, tras auditoría de
-- nulls/calidad): se descartan avisos con publicado_en más viejo que
-- `max_listing_age_years` (var en dbt_project.yml, hoy 1 año). infocasas
-- trae una cola de avisos con publicado_en de hasta 26 años atrás (algunos
-- con fecha placeholder 2000-01-01) -- poco creíble que sigan vigentes para
-- alquiler. Corte elegido por el usuario entre varias opciones evaluadas
-- (excluye ~579 de 8899 filas al momento de decidirlo).
--
-- direccion/latitud/longitud (agregado 2026-08-15, ver memoria de proyecto
-- "gold sin coordenadas/dirección" y auditoría de esa fecha): solo InfoCasas
-- las trae hoy -- mismo JSON ya descargado, sin request extra, ver
-- scrape_infocasas.py. NULL para Casasweb (no está disponible en la fuente,
-- confirmado) y BuscandoCasa (disponible parcialmente, ~20% de cobertura,
-- no implementado por ahora -- alcance de esta iteración fue solo InfoCasas).
--
-- OJO -- lo que este mart NO resuelve todavía (ver docs si se retoma):
--   Zonas: zone_id normaliza casing/acentos (macro zone_slug) pero no
--   reconcilia granularidad entre fuentes -- InfoCasas ya trae 81 barrios
--   propios (no solo los 15 canónicos de seed_barrios.csv), Casasweb ~52,
--   BuscandoCasa ~156 (nombres hiperlocales tipo "Aidy Grill"). Esto no es
--   nuevo de sumar fuentes -- ya pasaba con InfoCasas sola. Ningún mart
--   hace join contra seed_barrios (no hace falta), así que no se pierde
--   nada: los market_stats/market_snapshots/listing_market_position (ver
--   market_stats.sql) ya leen de acá, sobre las 3 fuentes, y confían en
--   `muestra_confiable` para no mostrar medianas de zonas con pocos avisos
--   -- mismo criterio de siempre, a más escala. Una tabla de mapeo de zonas
--   seguiría siendo valiosa para reducir la cola larga de zonas casi-
--   duplicadas, pero no es un bloqueante de correctitud.
--
--   Excepción (agregada 2026-08-19, macro barrio_canonico): los 13 casos
--   donde un barrio "fino" del seed no tiene límite propio en el INE y
--   coincidía polígono a polígono con un barrio oficial (ej. Villa
--   Biarritz/Punta Carretas) sí se fusionan acá, antes de llegar a este
--   mart -- si no, aparecían como zone_id separado en el dropdown de
--   barrios (S3D) aunque el mapa ya los mostrara superpuestos. El nombre
--   original queda en `subbarrio`, sin usarse todavía.

with unioned as (
    select * from {{ ref('stg_listings') }}
    union all
    select * from {{ ref('stg_listings_casasweb') }}
    union all
    select * from {{ ref('stg_listings_buscandocasa') }}
),

filtered as (
    select *
    from unioned
    where operacion = 'alquiler'
      and not (tipo_propiedad = 'apartamento' and dormitorios is null)
      and publicado_en >= current_date - interval '{{ var("max_listing_age_years") }}' year
      -- Alcance temporal (decidido 2026-08-20, junto con la re-ingesta que
      -- agregó banos/descripcion/amenities/datos_crudos): de las 3 fuentes,
      -- InfoCasas es la única con esos campos completos hoy -- Casasweb no
      -- los tiene disponibles en la fuente, BuscandoCasa solo parcialmente.
      -- Mientras el front no distinga campos faltantes por fuente, se
      -- restringe el gold/API a InfoCasas para no mostrar avisos con huecos
      -- de datos mezclados con avisos completos. Casasweb/BuscandoCasa
      -- siguen ingestados enteros en bronze/staging (stg_listings_casasweb,
      -- stg_listings_buscandocasa) -- sacar este filtro alcanza para
      -- volver a sumarlas acá el día que se resuelva esa UX.
      and fuente_datos = 'infocasas'
),

-- Dedup de re-listados (encontrado 2026-08-19, investigando outliers en
-- Punta Gorda): InfoCasas resubía el mismo aviso con un listing_id nuevo
-- cada vez que se refrescaba (10 filas "Hernani y Palermo/1655/1660/1700",
-- misma zona+tipo+dormitorios+metraje+precio, publicado_en un día distinto
-- cada una) -- se detectaban como 10 avisos activos distintos y esa masa de
-- precio idéntico terminaba fijando la mediana del bucket 3+ dormitorios de
-- Punta Gorda muy por debajo de los avisos legítimos del grupo (ver
-- market_listings.sql para el filtro de outliers, que no ataca esto: no son
-- outliers, son la mayoría del grupo). Firma de dedup deliberadamente
-- estricta (zona+tipo+dormitorios+metraje+precio, misma fuente) para no
-- fusionar unidades distintas de un desarrollo nuevo que legítimamente
-- comparten precio y metraje pero difieren en algo más -- si esa firma
-- completa coincide, nos quedamos con el más reciente.
deduped as (
    select *
    from filtered
    qualify row_number() over (
        partition by fuente_datos, zone_id, tipo_propiedad, dormitorios, metraje_m2, precio
        order by publicado_en desc, listing_id
    ) = 1
)

select
    listing_id,
    fuente_datos,
    barrio,
    subbarrio,
    zone_id,
    direccion,
    latitud,
    longitud,
    operacion,
    tipo_propiedad,
    dormitorios,
    dormitorios_bucket,
    banos,
    metraje_m2,
    precio,
    estado,
    es_activo,
    publicado_en,
    descripcion,
    amenities,
    fotos,
    foto_count,
    ingestado_en
from deduped
