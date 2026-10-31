-- Test singular: falla si alguna fila de market_stats tiene precio_min >
-- precio_mediana o precio_mediana > precio_max. dbt considera el test
-- exitoso si esta query devuelve 0 filas.

select *
from {{ ref('market_stats') }}
where not (precio_min <= precio_mediana and precio_mediana <= precio_max)
