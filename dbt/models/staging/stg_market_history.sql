with source as (
    select * from {{ source('raw', 'market_history_seed') }}
),

renamed as (
    select
        barrio,
        {{ zone_slug('barrio') }} as zone_id,
        dormitorios_bucket,
        cast(periodo as date) as periodo,
        precio_mediana
    from source
)

select * from renamed
