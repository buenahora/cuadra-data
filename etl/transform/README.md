# etl/transform

La transformación pesada (agregaciones, medianas, clasificación below/in/above,
histórico) vive en `dbt/` como modelos SQL versionados y testeados — no acá.
Ver `dbt/models/staging/` y `dbt/models/marts/`.

Esta carpeta queda para transformaciones **previas a dbt** que no conviene
resolver en SQL: por ejemplo, geocoding de direcciones a lat/lng, limpieza de
texto libre, o parsing de fuentes externas con formatos irregulares (si en el
futuro se ingieren datos scrapeados, ver `docs/market-intelligence.md §4`).
Hoy está vacía porque el extractor actual (`etl/extract/generate_synthetic_listings.py`)
ya produce datos limpios y tipados.
