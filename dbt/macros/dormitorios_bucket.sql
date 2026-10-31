{% macro dormitorios_bucket(dormitorios_column) %}
    {#
      Mismo bucketing en las 3 fuentes -- extraído a macro porque
      BuscandoCasa mezcla tipos de propiedad donde dormitorios no aplica
      (terrenos, garages) y llega NULL, algo que no pasa en InfoCasas/Casasweb
      (esas filas se descartan en el extractor si falta dormitorios).
    #}
    case
        when {{ dormitorios_column }} is null then null
        when {{ dormitorios_column }} >= 3 then '3+'
        else cast({{ dormitorios_column }} as varchar)
    end
{% endmacro %}
