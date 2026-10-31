{% macro zone_slug(column_name) %}
    {#
      Normaliza un nombre de barrio ("Parque Rodó") a un id estable
      ("parque_rodo") para usar como FK/join key -- ver docs/data-model.md
      Sección 5. Implementado como reemplazos encadenados en vez de una
      función de acentos de ICU para no depender de una extensión de DuckDB.
    #}
    {#
      Jinja2 `set` dentro de un for-loop no persiste entre iteraciones (crea
      scope nuevo cada vuelta) -- por eso `ns` (namespace), que sí es mutable
      a través del loop. Sin esto, el macro compila pero no reemplaza nada.
    #}
    {#
      lower() va PRIMERO, no al final -- bug real encontrado el 2026-08-14:
      con lower() al final, los reemplazos de acento corrían sobre el string
      original, y la lista de reemplazo solo tiene minúsculas ("í", no "Í").
      Fuentes en MAYÚSCULAS con acento (BuscandoCasa: "MALVÍN NORTE") nunca
      matcheaban ningún reemplazo, y el lower() final los dejaba con el
      acento todavía puesto ("malvín_norte"), sin fusionar con la misma zona
      en minúsculas de otras fuentes ("malvin_norte") -- 16 avisos afectados.
    #}
    {%- set replacements = [
        ("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n"), (" ", "_")
    ] -%}
    {%- set ns = namespace(expr="lower(" ~ column_name ~ ")") -%}
    {%- for find, replace_with in replacements -%}
        {%- set ns.expr = "replace(" ~ ns.expr ~ ", '" ~ find ~ "', '" ~ replace_with ~ "')" -%}
    {%- endfor -%}
    {{ ns.expr }}
{% endmacro %}
