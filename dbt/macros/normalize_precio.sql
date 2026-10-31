{% macro normalize_precio(precio_column) %}
    {#
      Redondea precio a pesos enteros (sin decimales -- ningún data source
      trae centavos con significado real) y lo fija a un tipo de dato único.
      Las 3 fuentes llegan a bronze con formatos distintos: "16000.0"
      (InfoCasas/sintético), "8300" (Casasweb), y BuscandoCasa además trae
      blanco en avisos de temporada sin monto fijo ("CONSULTAR"/"RESERVADO")
      -- try_cast preserva ese NULL en vez de fallar la corrida.
    #}
    cast(round(try_cast({{ precio_column }} as double), 0) as decimal(12, 0))
{% endmacro %}
