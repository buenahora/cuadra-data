-- Avisos activos de apartamento en alquiler, con outliers de precio
-- removidos por zona+dormitorios (mediana ± 1.5*IQR -- ver conversación
-- del 2026-08-14: se encontraron precios delirantes en InfoCasas, ej. una
-- venta mal categorizada como alquiler saliendo ~250x la mediana de su
-- zona). Fuente compartida de market_stats y listing_market_position, para
-- no duplicar la lógica de outliers en los dos -- cualquier cambio al
-- criterio de outlier se hace acá una sola vez.
--
-- OJO: el filtro es mediana ± 1.5*IQR, no el fence clásico de Tukey
-- (Q1 - 1.5*IQR / Q3 + 1.5*IQR) -- decisión explícita tomada en la
-- conversación, no un error. Cambiar a los cuartiles como límites es
-- trivial si se prefiere después: reemplazar `mediana_bruta` por `q1`/`q3`
-- en la CTE `limites`.
--
-- Se salta el filtro para grupos con menos de min_sample_size avisos: con
-- tan pocos puntos el IQR no es confiable (puede dar 0 y filtrar avisos
-- legítimos), y esos grupos ya quedan marcados muestra_confiable=false en
-- market_stats de todas formas -- no hace falta protegerlos acá también.

with activos as (
    select *
    from {{ ref('listings_unified') }}
    where es_activo
      and tipo_propiedad = 'apartamento'
),

stats_por_grupo as (
    select
        zone_id,
        dormitorios_bucket,
        count(*) as n,
        median(precio) as mediana_bruta,
        quantile_cont(precio, 0.25) as q1,
        quantile_cont(precio, 0.75) as q3
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
    and a.dormitorios_bucket = l.dormitorios_bucket
where l.n < {{ var('min_sample_size') }}
   or a.precio between l.limite_inferior and l.limite_superior
