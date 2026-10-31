-- Mediana/min/max/tamaño de muestra por zona+tipología, a partir de avisos
-- activos de las 3 fuentes (InfoCasas, Casasweb, BuscandoCasa -- ver
-- listings_unified). Es la fuente de la que se alimentan el chip "vs.
-- mercado" (S1C), la consulta de precio pre-publicación (S2A) y el
-- feedback en tiempo real del wizard de precio (S2D). Ver
-- docs/data-model.md Sección 8.
--
-- Lee de market_listings (no directo de listings_unified): ahí ya está el
-- filtro tipo_propiedad='apartamento' (MarketStat en backend/app/schemas/market.py
-- no tiene dimensión de tipo de propiedad -- mezclar casas/terrenos en la
-- misma mediana rompería la comparabilidad) y el filtro de outliers de
-- precio por zona+dormitorios (ver docstring de ese modelo).
--
-- Zonas: zone_id normaliza casing/acentos (macro zone_slug) pero no
-- reconcilia granularidad entre fuentes -- esto ya pasaba con InfoCasas
-- sola (81 zonas propias, ~74% cae en las 15 canónicas de seed_barrios.csv)
-- y sigue pasando acá, a más escala. `muestra_confiable` ya protege contra
-- zonas con pocos avisos por esto -- no se agregó ninguna lógica nueva de
-- filtrado, es el mismo criterio de siempre.
--
-- Agrupa por zone_id (no por barrio): con una sola fuente, barrio y zone_id
-- eran básicamente 1:1 (una grafía por zona); con 3 fuentes, la misma zona
-- puede llegar con grafías distintas (mayúsculas/espacios) y agrupar
-- también por barrio partía la zona en más de una fila -- eso rompía la
-- unicidad de zone_id+dormitorios_bucket, y listing_market_position (que
-- hace join por esas dos columnas) terminaba duplicando avisos por el
-- fan-out. `barrio` de salida es una grafía cualquiera pero estable
-- (min()) de las que llegaron para esa zona -- solo para mostrar, zone_id
-- sigue siendo la key real.
--
-- Contrato con el backend: backend/app/schemas/market.py `MarketStat` está
-- pensado 1:1 contra este modelo (ver comentario ahí). variacion_interanual_pct
-- sale de stg_market_history (no de market_snapshots, para no crear una
-- dependencia circular -- market_snapshots ya lee de este modelo). Sin
-- match a 12 meses atrás (fuera del histórico sembrado, o zona nueva sin
-- semilla) queda NULL, no 0 -- son cosas distintas ("no sabemos" vs
-- "no cambió").

with activos as (
    select * from {{ ref('market_listings') }}
),

agg as (
    select
        zone_id,
        min(barrio) as barrio,
        dormitorios_bucket,
        count(*) as avisos_activos,
        min(precio) as precio_min,
        median(precio) as precio_mediana,
        max(precio) as precio_max
    from activos
    group by 1, 3
),

hace_12_meses as (
    select zone_id, dormitorios_bucket, precio_mediana as precio_mediana_hace_12_meses
    from {{ ref('stg_market_history') }}
    where periodo = date_trunc('month', current_date - interval '12 months')
)

select
    agg.*,
    agg.avisos_activos >= {{ var('min_sample_size') }} as muestra_confiable,
    case
        when h.precio_mediana_hace_12_meses is not null and h.precio_mediana_hace_12_meses != 0
            then round(
                (agg.precio_mediana - h.precio_mediana_hace_12_meses) / h.precio_mediana_hace_12_meses * 100,
                1
            )
        else null
    end as variacion_interanual_pct,
    current_date as actualizado_en
from agg
left join hace_12_meses h
    on agg.zone_id = h.zone_id
    and agg.dormitorios_bucket = h.dormitorios_bucket
