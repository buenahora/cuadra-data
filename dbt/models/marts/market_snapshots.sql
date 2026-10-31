-- Serie histórica mensual por zona+tipología: histórico sembrado (meses
-- -12..-1) + el punto de este mes, calculado en vivo desde market_stats.
-- Ver docs/market-intelligence.md Sección 5 -- correr este modelo una vez
-- por mes, siempre, es lo que construye el gráfico de tendencia de 12 meses
-- que hoy en el prototipo está hardcodeado (S2A/S3A `trend`).

with historical as (
    select
        zone_id,
        barrio,
        dormitorios_bucket,
        periodo,
        precio_mediana,
        'historico_semilla' as fuente
    from {{ ref('stg_market_history') }}
),

actual as (
    select
        zone_id,
        barrio,
        dormitorios_bucket,
        -- cast a date explícito: date_trunc() devuelve timestamp en DuckDB,
        -- y el union con `historical.periodo` (date) sin este cast sube
        -- toda la columna a timestamp -- MarketSnapshot.periodo en el
        -- backend espera date (backend/app/schemas/market.py).
        cast(date_trunc('month', current_date) as date) as periodo,
        precio_mediana,
        'calculado' as fuente
    from {{ ref('market_stats') }}
    where muestra_confiable
)

select * from historical
union all
select * from actual
order by zone_id, dormitorios_bucket, periodo
