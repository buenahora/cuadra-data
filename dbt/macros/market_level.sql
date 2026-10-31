{% macro market_level(precio_column, mediana_column) %}
    {#
      Clasificación below/in/above de un precio frente a la mediana de su
      zona+tipología. Replica la lógica ya definida (y hoy hardcodeada) en
      frontend/src/screens/grupo2/S2D.jsx -- ver docs/market-intelligence.md
      Sección 3. Los umbrales viven en dbt_project.yml `vars`, no acá, para
      poder ajustarlos sin tocar SQL.
    #}
    case
        when {{ precio_column }} < {{ mediana_column }} * {{ var('market_below_threshold') }} then 'below'
        when {{ precio_column }} > {{ mediana_column }} * {{ var('market_above_threshold') }} then 'above'
        else 'in'
    end
{% endmacro %}
