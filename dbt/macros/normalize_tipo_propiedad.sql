{% macro normalize_tipo_propiedad(column_name) %}
    {#
      InfoCasas/Casasweb usan el plural de la ruta/parámetro de su API
      ('apartamentos', 'casas' -- ver --property-type en ambos extractores)
      donde BuscandoCasa usa singular ('apartamento', 'casa' -- ver
      PROPERTY_TYPES en scrape_buscandocasa.py) para el mismo tipo de
      propiedad. Se unifica a singular acá; el resto de los tipos
      (terreno/local/oficina/...) solo existen hoy en BuscandoCasa, ya en
      singular, y pasan sin cambios más allá de trim/lower.
    #}
    case
        when lower(trim({{ column_name }})) in ('apartamentos', 'apartamento') then 'apartamento'
        when lower(trim({{ column_name }})) in ('casas', 'casa') then 'casa'
        else lower(trim({{ column_name }}))
    end
{% endmacro %}
