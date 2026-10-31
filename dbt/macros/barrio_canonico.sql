{% macro barrio_canonico(column_name) %}
    {#
      Fusiona barrios "finos" que el INE no distingue con límite propio
      (mismo polígono que su barrio oficial en frontend/src/data/
      barrioBoundaries.js, ver conversación 2026-08-19) con el barrio
      oficial que sí tiene curaduría en seed_barrios.csv -- si no,
      listings_unified reporta la misma zona dos veces bajo zone_id
      distintos (ej. "villa_biarritz" con 75 avisos separado de
      "punta_carretas", cuando para el INE es la misma zona), y el
      dropdown de barrios (S3D) los mostraba como si fueran zonas
      distintas.

      villa_dolores -> Parque Batlle y bella_vista -> Capurro: estos dos
      pares no tienen barrio oficial propio (los dos lados son igual de
      "finos", ninguno tiene límite INE propio) -- se decidió con el
      usuario cuál nombre sobrevive, mismo criterio que en
      barrioBoundaries.js.

      El nombre tal cual llegó de la fuente se conserva en `subbarrio`
      (ver stg_listings*.sql) -- no se usa todavía, queda reservado para
      cuando el producto quiera filtrar a este nivel de detalle.
    #}
    {%- set fusiones = {
        'VILLA BIARRITZ': 'Punta Carretas',
        'POCITOS NUEVO': 'Pocitos',
        'PUERTO BUCEO': 'Buceo',
        'PUERTO DEL BUCEO': 'Buceo',
        'PARQUE MIRAMAR': 'Carrasco',
        'CARRASCO ESTE': 'Carrasco',
        'BARRIO PARQUES': 'Carrasco',
        'BARRA DE CARRASCO': 'Carrasco',
        'GOLF': 'Carrasco Norte',
        'GOES': 'La Comercial',
        'PASO MOLINO': 'La Teja',
        'VILLA DOLORES': 'Parque Batlle',
        'BELLA VISTA': 'Capurro',
    } -%}
    case upper(trim({{ column_name }}))
    {%- for fino, oficial in fusiones.items() %}
        when '{{ fino }}' then '{{ oficial }}'
    {%- endfor %}
        else trim({{ column_name }})
    end
{% endmacro %}
