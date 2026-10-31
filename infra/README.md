# Infra del pipeline de datos

`pipeline.yaml` → stack `alquileres-<env>-pipeline`: buckets raw/curated,
tres Lambdas de scraping, job de Glue de transformación, catálogo de Glue +
Athena, state machine de Step Functions y el schedule semanal. Lo despliega el
CI (`.github/workflows/deploy.yml`) en cada push a `main`; no se toca a mano.

Qué sube el CI a `s3://<artefactos>/pipeline/<sha>/`:

| Artefacto | Sale de |
| --- | --- |
| `scraper-infocasas.zip`, `scraper-casasweb.zip`, `scraper-buscandocasa.zip` | `lambdas/scraper/` + `etl/extract/scrape_*.py` + `src/cuadra_data/` + `requests`. Es el **mismo zip** subido tres veces: la fuente la elige la env var `FUENTE` que setea el template |
| `transform.py` | `glue/transform.py` |
| `deps.zip` | `glue/requirements-glue.txt` (pg8000) |

La corrida semanal: `PrepararFecha` → scraping de las tres fuentes en paralelo
(si una falla, avisa por SNS y sigue) → job de Glue. Todas las escrituras usan
la fecha de la corrida en sus claves, así que relanzar la misma semana pisa los
mismos archivos en vez de duplicarlos.

Dos límites conocidos, anotados también en el código:

- El timeout máximo de Lambda es 15 min y una corrida completa de InfoCasas
  tarda ~18 (ver `etl/extract/scrape_infocasas.py`). Por eso el handler acepta
  `MAX_PAGES` / `MAX_ZONAS` / `COMBOS` por env var. Para la corrida completa
  hay que mover el extractor a Fargate o a un job de Glue.
- `glue/transform.py` hace un mapeo de columnas mínimo. La limpieza real está
  en `dbt/macros/` y `dbt/models/staging/` y todavía no está portada: el
  warehouse productivo no está decidido (`config/connections.yml`, entorno
  prod = TBD).

## Configuración del repo

Secrets: `AWS_DEPLOY_ROLE_ARN` (output `PipelineDeployRoleArn` del stack base),
`CFN_EXEC_ROLE_ARN` (output `CfnExecutionRoleArn`).

Variables: opcionales `AWS_REGION`, `PROJECT`, `ENVIRONMENT`,
`SCHEDULE_EXPRESSION`.

El runbook completo y la tabla de todos los secrets/variables están en el repo
del backend: `infra/README.md`.
