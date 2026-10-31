{% macro normalize_operacion(column_name) %}
    {#
      Casasweb usa 'temporal' (ver OPERATION_TO_CONTRATO en scrape_casasweb.py)
      donde BuscandoCasa usa 'temporada' (ver OPERATIONS en
      scrape_buscandocasa.py) para el mismo concepto -- mismo criterio de
      unificación de vocabulario que dormitorios_bucket/normalize_tipo_propiedad.
    #}
    case
        when lower(trim({{ column_name }})) = 'temporal' then 'temporada'
        else lower(trim({{ column_name }}))
    end
{% endmacro %}
