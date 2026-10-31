-- A diferencia de stg_listings/stg_listings_casasweb, operacion y
-- tipo_propiedad varían fila a fila acá -- este extractor mezcla las 21
-- combinaciones tipo×operación en un solo archivo (ver docstring de
-- scrape_buscandocasa.py). dormitorios/metraje_m2/precio pueden venir en
-- blanco para tipos de propiedad o avisos donde no aplican (terrenos,
-- avisos de temporada sin precio fijo) -- try_cast preserva esos NULL en
-- vez de descartar la fila o fallar la corrida.

with source as (
    select * from {{ source('raw', 'listings_buscandocasa') }}
),

renamed as (
    select
        listing_id,
        fuente_datos,
        trim(barrio) as subbarrio,
        {{ barrio_canonico('barrio') }} as barrio,
        {{ zone_slug(barrio_canonico('barrio')) }} as zone_id,
        -- Sin dirección ni coordenadas por ahora -- alcance de este cambio
        -- es solo InfoCasas (decidido 2026-08-15). BuscandoCasa sí tiene
        -- coordenadas parciales disponibles sin request extra (~20% de
        -- cobertura, ver docstring de scrape_buscandocasa.py y memoria de
        -- proyecto) -- pendiente para más adelante si hace falta.
        cast(null as varchar) as direccion,
        cast(null as double) as latitud,
        cast(null as double) as longitud,
        {{ normalize_operacion('operacion') }} as operacion,
        {{ normalize_tipo_propiedad('tipo_propiedad') }} as tipo_propiedad,
        try_cast(dormitorios as integer) as dormitorios,
        {{ dormitorios_bucket('try_cast(dormitorios as integer)') }} as dormitorios_bucket,
        try_cast(banos as integer) as banos,
        try_cast(metraje_m2 as integer) as metraje_m2,
        {{ normalize_precio('precio') }} as precio,
        estado,
        estado = 'activo' as es_activo,
        cast(try_cast(publicado_en as timestamp) as date) as publicado_en,
        -- descripcion sale de la sección COMENTARIOS de la ficha de detalle
        -- (un request extra por aviso, ver fetch_ficha_detalle() en
        -- scrape_buscandocasa.py) -- vacía si la corrida se hizo con
        -- --skip-detalle o si ese aviso no trajo ficha. Sin amenities propia
        -- todavía: la ficha sí trae comodidades marcadas/no marcadas
        -- (EQUIPAMIENTO, SERVICIOS Y ACCESORIOS, etc.), pero quedan dentro
        -- de datos_crudos sin aplanar a lista -- structure varía más entre
        -- tipos de propiedad que las facilities tipadas de InfoCasas.
        nullif(trim(descripcion), '') as descripcion,
        cast([] as varchar[]) as amenities,
        {{ parse_fotos('fotos') }} as fotos,
        try_cast(foto_count as integer) as foto_count,
        try_cast(ingestado_en as timestamp) as ingestado_en,
        try_cast(datos_crudos as json) as datos_crudos
    from source
)

select * from renamed
