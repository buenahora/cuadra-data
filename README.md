# Cuadra — Data Layer

Motor de datos del proyecto Cuadra: el pipeline que calcula "precio vs.
mercado" (mediana, rango, histórico) por zona y tipología de apartamento —
la pieza diferencial del producto (ver `docs/business.md` y
`docs/market-intelligence.md`).

**Estado actual: funciona de punta a punta en local, con dos extractores
intercambiables.** `pipelines/run_local.py` usa datos sintéticos;
`pipelines/run_local_real.py` usa avisos reales de alquiler scrapeados de
InfoCasas (`etl/extract/scrape_infocasas.py`, ver `docs/scraping-strategy.md`).
No hay todavía backend operacional propio ni Airflow desplegado, y
MercadoLibre sigue sin resolver (bloqueado a nivel de borde, ver
`docs/scraping-strategy.md §3`). Ver `Qué es real y qué no` más abajo antes
de asumir que algo de esto está en producción.

## Quickstart

Requisitos: [`uv`](https://docs.astral.sh/uv/) (Python) — no hace falta
Python ni dbt preinstalados, `uv` los resuelve.

```bash
cd data_layer
uv sync                        # crea .venv e instala dbt-core, dbt-duckdb, duckdb, etc.
uv run python pipelines/run_local.py        # extract sintético -> load -> dbt seed -> dbt run -> dbt test
uv run python pipelines/run_local_real.py   # ídem, pero listings.csv real (scraping InfoCasas)
```

`run_local_real.py` acepta argumentos de `scrape_infocasas.py` después de `--`,
por ejemplo `-- --max-pages 5` para una corrida rápida en desarrollo (la
corrida completa son ~360 páginas, ~7-8 min, ~7.500 avisos).

Eso deja un warehouse DuckDB poblado en `data/warehouse.duckdb`, que se puede explorar directo:

```bash
uv run python -c "
import duckdb
con = duckdb.connect('data/warehouse.duckdb', read_only=True)
print(con.execute('select * from market_stats order by avisos_activos desc limit 10').df())
"
```

Correr los tests (unitarios + integración, corre el pipeline completo contra un warehouse temporal aislado):

```bash
uv run pytest -v
```

## Estructura

```
config/       Conexiones y variables de entorno centralizadas (profiles.yml de dbt, connections.yml legible, .env.example)
data/         raw/ (CSVs generados por extract, no versionados) y processed/ (vacío por ahora)
docs/         business.md, roadmap.md, data-model.md, market-intelligence.md -- el contexto de negocio y de producto
dbt/          El proyecto dbt: staging/, marts/, seeds/ (catálogos), macros/, tests/
etl/          extract/ (genera datos), load/ (los sube al warehouse), transform/ (vacío -- ver etl/transform/README.md)
pipelines/    run_local.py (orquestador de un solo comando) y dags/ (DAG de Airflow, escrito pero no desplegado)
src/          cuadra_data/ -- config loader compartido, importado por etl/pipelines/tests
tests/        unit/ (config loader) e integration/ (pipeline completo end-to-end)
```

## Por qué este stack

- **dbt-duckdb para desarrollo local:** cero infraestructura que levantar,
  `uv sync` alcanza. Los modelos SQL (`dbt/models/`) no dependen de DuckDB
  específicamente — si más adelante el volumen de datos o la necesidad de
  escritura concurrente desde el backend lo justifican, se cambia el target
  en `config/profiles.yml` (Postgres, Snowflake, lo que sea) sin tocar un
  solo modelo. Esa es la razón real de separar `config/` del resto: cambiar
  de warehouse es editar un archivo, no reescribir el pipeline.
- **Airflow escrito pero no desplegado:** `pipelines/dags/market_stats_dag.py`
  existe y documenta la orquestación pretendida (mensual, ver
  `docs/market-intelligence.md §5`), pero levantar webserver+scheduler+DB de
  metadata para orquestar un único job que corre contra datos sintéticos no
  aporta nada que `pipelines/run_local.py` no dé ya. Se activa cuando haya
  una fuente de datos real y un cronograma real que valga la pena
  automatizar — instrucciones en el docstring de ese archivo.
- **Terraform / Docker:** no hay infraestructura de nube que provisionar
  todavía (no hay ni siquiera decidido dónde correrá esto en producción, ver
  `docs/business.md §8`). Se suman cuando haya algo real que provisionar.

## Qué es real y qué no

| Pieza | Estado |
|---|---|
| `dbt/models/staging`, `dbt/models/marts` | SQL real, corre y pasa tests (30/30) |
| `data/raw/listings.csv` vía `pipelines/run_local.py` | **Sintético.** Generado por `etl/extract/generate_synthetic_listings.py` a partir de las medianas ya hardcodeadas en `frontend/src/screens/grupo2/S2A.jsx`, reproduce esos min/max/avisos exactamente y la mediana dentro de ±15% (es una distribución triangular con semilla fija, no datos reales) |
| `data/raw/listings.csv` vía `pipelines/run_local_real.py` | **Real.** Scrapeado de InfoCasas (`etl/extract/scrape_infocasas.py`) — avisos activos de alquiler de apartamentos en Montevideo, precio/barrio/dormitorios/m² reales. No tiene aún un scheduler que lo re-corra periódicamente (ver DAG más abajo) |
| `data/raw/market_history_seed.csv` (ambos pipelines) | **Sintético siempre**, a propósito: simula haber capturado histórico mensual desde antes de que la plataforma existiera — en producción esto no existe, hay que empezar a capturarlo desde el día 1 (ver `docs/market-intelligence.md §5`) |
| MercadoLibre como fuente | **No resuelto.** Sitio y API sin auth devuelven 403 (bloqueo de borde real, no un tema de headers); el "Developer Partner Program" con más acceso exige GMV transaccionado mínimo. Ver `docs/scraping-strategy.md §3` para el detalle y las opciones evaluadas (servicio anti-bot pago, o pulls de bajo volumen vía navegador real) |
| Umbral below/in/above (±8%, en `dbt_project.yml` vars) | Copiado del prototipo frontend, **no validado con datos reales** |
| `min_sample_size` (5 avisos) | Valor de partida, **no validado** |
| `pipelines/dags/market_stats_dag.py` | Escrito, sintácticamente correcto, **no desplegado** — requiere `uv sync --group airflow` + infraestructura de Airflow que hoy no existe |

## Config y secretos

Nada de conexión está hardcodeado en el código. `config/profiles.yml` (dbt)
y `src/cuadra_data/config.py` (Python) leen todo de variables de entorno,
con defaults documentados en `config/connections.yml`. Para overridear algo:

```bash
cp config/.env.example .env   # gitignored
# editar .env
```

## Próximos pasos

Ver `docs/roadmap.md` Fase 2 en adelante y `docs/scraping-strategy.md` para
el detalle de extracción. En orden de impacto real (no de facilidad
técnica): (1) InfoCasas ya resuelto (`pipelines/run_local_real.py`) — correr
la extracción completa (sin `--max-pages`) y decidir cadencia; (2)
MercadoLibre sigue sin resolver — validar si el Developer Program alcanza o
hace falta un servicio anti-bot pago (`docs/scraping-strategy.md §3-5`); (3)
recién ahí tiene sentido desplegar el DAG de Airflow para capturar snapshots
mensuales de verdad, sobre la fuente que se termine de decidir.

## Deploy

El CI despliega solo. En cada push a `main`, `.github/workflows/deploy.yml`
corre los tests, arma los artefactos (zip de las Lambdas de scraping,
`glue/transform.py` y `deps.zip`), los sube al bucket de artefactos y hace
`aws cloudformation deploy` de `infra/pipeline.yaml` con `CodeVersion=<sha>`.

Qué despliega ese stack, qué artefacto sale de dónde y los dos límites
conocidos (timeout de Lambda vs. duración del scraping; la lógica de dbt que
el job de Glue todavía no replica): `infra/README.md`.

El runbook completo de infra vive en el repo del backend
(`infra/README.md` de `cuadra-backend`).
