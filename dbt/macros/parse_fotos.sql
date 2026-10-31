{% macro parse_fotos(column_name) %}
    {#
      Bronze trae "fotos" como string separado por "|" (formato CSV, no
      puede llevar un array real) -- ver docstrings de etl/extract/scrape_*.py.
      Acá se despliega a LIST(VARCHAR) de verdad. string_split('', '|') NO
      da lista vacía (da ['']), por eso el case explícito para blank/null.

      Dedup preservando orden (list_filter quedándose con la primera
      ocurrencia de cada url, vía list_position) -- InfoCasas repite la
      misma url dentro de su galería en algunos avisos, y eso inflaba la
      cantidad de fotos mostrada en el front (encontrado 2026-08-20). No
      se usa list_distinct porque no preserva el orden original (afectaría
      qué foto queda de portada). Bronze histórico ya scrapeado también
      tiene el duplicado, por eso el dedup va acá y no solo en el scraper.
    #}
    case
        when {{ column_name }} is null or trim({{ column_name }}) = '' then cast([] as varchar[])
        else list_filter(
            string_split({{ column_name }}, '|'),
            (url, i) -> list_position(string_split({{ column_name }}, '|'), url) = i
        )
    end
{% endmacro %}
