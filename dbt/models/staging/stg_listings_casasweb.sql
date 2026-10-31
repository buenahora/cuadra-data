-- Une los archivos de Casasweb (uno por combinación operation+property-type,
-- ver docstring de scrape_casasweb.py) antes de aplicar la misma limpieza
-- que stg_listings.sql. Agregar una nueva combinación (ej. venta_apartamentos)
-- es: correr el extractor con esos flags, agregar la tabla a _sources.yml, y
-- sumarla acá al union.

with apartamentos as (
    select * from {{ source('raw', 'listings_casasweb_alquiler_apartamentos') }}
),

casas as (
    select * from {{ source('raw', 'listings_casasweb_alquiler_casas') }}
),

unioned as (
    select * from apartamentos
    union all
    select * from casas
),

renamed as (
    select
        listing_id,
        fuente_datos,
        trim(barrio) as subbarrio,
        {{ barrio_canonico('barrio') }} as barrio,
        {{ zone_slug(barrio_canonico('barrio')) }} as zone_id,
        -- Sin dirección ni coordenadas: ni la API documentada de Casasweb
        -- (43 campos, ninguno de geo) ni su página pública de detalle las
        -- exponen (investigado 2026-08-15, ver memoria de proyecto) --
        -- a diferencia de InfoCasas, no es algo que falte capturar, no está
        -- disponible en la fuente.
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
        -- Sin descripción ni amenities: investigado 2026-08-20 junto con
        -- dirección/coordenadas (ver comentario arriba) -- ni /propiedades
        -- ni la página pública de detalle las exponen.
        cast(null as varchar) as descripcion,
        cast([] as varchar[]) as amenities,
        {{ parse_fotos('fotos') }} as fotos,
        -- Sin conteo de fotos: la API de Casasweb no lo expone en /propiedades,
        -- solo el ID de portada (ver docstring de scrape_casasweb.py) -- pedirlo
        -- requeriría ir a la ficha de detalle por aviso, fuera de alcance.
        cast(null as integer) as foto_count,
        try_cast(ingestado_en as timestamp) as ingestado_en,
        try_cast(datos_crudos as json) as datos_crudos
    from unioned
)

select * from renamed
