-- Clasificación below/in/above de cada aviso activo frente a la mediana de
-- su zona+tipología, sobre las 3 fuentes (ver market_stats.sql). Es
-- exactamente el dato que pinta el MarketChip en el detalle de aviso (S1C)
-- y en el wizard de publicación (S2D). Lee de market_listings, mismo
-- alcance (apartamento, sin outliers de precio) que market_stats -- un
-- aviso que quedó afuera por outlier (ver docstring de market_listings.sql,
-- ej. una venta mal categorizada como alquiler) tampoco tiene sentido
-- clasificarlo acá.

with listings as (
    select * from {{ ref('market_listings') }}
),

stats as (
    select zone_id, dormitorios_bucket, precio_mediana, muestra_confiable
    from {{ ref('market_stats') }}
)

select
    l.listing_id,
    l.barrio,
    l.zone_id,
    l.dormitorios_bucket,
    l.precio,
    s.precio_mediana,
    s.muestra_confiable,
    case
        when s.muestra_confiable
            then {{ market_level('l.precio', 's.precio_mediana') }}
        else null
    end as nivel_mercado,
    case
        when s.muestra_confiable
            then round(abs(l.precio - s.precio_mediana) / s.precio_mediana * 100, 1)
        else null
    end as diferencia_pct
from listings l
left join stats s
    on l.zone_id = s.zone_id
    and l.dormitorios_bucket = s.dormitorios_bucket
