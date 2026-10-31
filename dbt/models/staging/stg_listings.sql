with source as (
    select * from {{ source('raw', 'listings') }}
),

renamed as (
    select
        listing_id,
        fuente_datos,
        trim(barrio) as subbarrio,
        {{ barrio_canonico('barrio') }} as barrio,
        {{ zone_slug(barrio_canonico('barrio')) }} as zone_id,
        nullif(trim(direccion), '') as direccion,
        try_cast(latitud as double) as latitud,
        try_cast(longitud as double) as longitud,
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
        nullif(trim(descripcion), '') as descripcion,
        case
            when amenities is null or trim(amenities) = '' then cast([] as varchar[])
            else string_split(amenities, '|')
        end as amenities,
        {{ parse_fotos('fotos') }} as fotos,
        try_cast(foto_count as integer) as foto_count,
        try_cast(ingestado_en as timestamp) as ingestado_en,
        -- Item completo del sitio, sin curar -- ver docstring de
        -- normalize_row() en scrape_infocasas.py. try_cast a JSON real (no
        -- solo varchar) para poder usar json_extract río abajo sin más
        -- casteo.
        try_cast(datos_crudos as json) as datos_crudos
    from source
)

select * from renamed
