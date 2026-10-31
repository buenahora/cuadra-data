# Infra del pipeline de datos

`pipeline.yaml` → stack `alquileres-<env>-pipeline`: buckets raw/curated, la
Lambda de scraping, el job de Glue de transformación, el catálogo de Glue +
Athena, la state machine de Step Functions y el schedule semanal. Lo despliega
el CI (`.github/workflows/deploy.yml`) en cada push a `main`; no se toca a mano.

Hoy hay **una sola fuente conectada: InfoCasas**. Es el POC de la cadena
completa; las otras dos (`etl/extract/scrape_casasweb.py`,
`scrape_buscandocasa.py`) corren en local pero no están desplegadas todavía
— sumarlas es el procedimiento del final de este README.

## La corrida semanal

```
Scheduler (lunes 06:00 Montevideo)
  └─ PrepararFecha        la fecha sale de la hora programada; todas las
     │                    escrituras la usan en sus claves, así que relanzar
     │                    la semana pisa los mismos objetos
     ├─ Scraping (Parallel, una rama por fuente)
     │   └─ InfoCasas
     │       ├─ PlanificarInfoCasas   1 request: cuántas páginas hay hoy
     │       └─ TandasInfoCasas       Map, MaxConcurrency 1: una invocación
     │                                por tanda de páginas, en serie
     └─ Transformar        job de Glue: raw -> curated -> Postgres
```

**Por qué en tandas.** Una corrida completa de InfoCasas son ~359 páginas por
orden × 2 órdenes a 1,2 s por request (~18 min) y el timeout máximo de Lambda
es 15. El paso de planificación pregunta cuántas páginas hay *hoy* (el total
cambia semana a semana) y parte la corrida en tandas de `PaginasPorTanda`
páginas (default 150 ≈ 5 min). Las tandas corren **una después de la otra**:
el extractor ya espera entre requests y correrlas en paralelo multiplicaría el
ritmo contra el sitio. Ver `docs/scraping-strategy.md`.

**Si algo falla**, falla la corrida entera y no se transforma nada a medias.
La alerta la manda `ExecutionFailedRule` al topic de alertas del stack base;
no hay un paso de aviso dentro de la state machine.

## El contrato entre capas

Esto es lo que hace que sumar una fuente sea mecánico. Son tres puntos:

**1. Dónde escribe el extractor.** CSVs en el bucket raw, bajo
`fuente=<nombre>/fecha=<YYYY-MM-DD>/<lo-que-sea>.csv`. La fecha la da la state
machine. Un extractor puede escribir un archivo o veinte (InfoCasas escribe
uno por tanda) — el job de Glue lee todos los de la fecha.

**2. Qué columnas tiene ese CSV** (contrato de bronze). La referencia es
`write_csv()` en `etl/extract/scrape_infocasas.py`:

```
listing_id  barrio  direccion  latitud  longitud  dormitorios  banos
metraje_m2  precio  estado  publicado_en  descripcion  amenities  fotos
foto_count  fuente_datos  operacion  tipo_propiedad  ingestado_en  datos_crudos
```

Las que una fuente no tenga disponibles van vacías (Casasweb, por ejemplo, no
expone dirección ni coordenadas en ningún endpoint). `glue/transform.py` las
deja en NULL y lo avisa en el log; no se cae. `datos_crudos` es el JSON entero
del aviso sin curar: se queda en raw, no llega a curated.

**3. Qué hace el job de Glue con eso.** Lee todas las fuentes de la fecha,
tipa las columnas, dedupea por `fuente_datos + listing_id`, escribe un parquet
en `curated/avisos/fecha=<fecha>/` y carga esa partición en Postgres
(borrando antes la de esa fecha, así relanzar no duplica). El job es
fuente-agnóstico: **no hay que tocarlo para sumar una fuente**.

La misma lista de columnas vive en tres lugares y los tres tienen que moverse
juntos si el contrato cambia: `write_csv()` de cada extractor, `COLUMNAS` en
`glue/transform.py`, y `AvisosTable` en `infra/pipeline.yaml`.

Lo que curated **no** es: silver. La limpieza real (barrio canónico, buckets
de dormitorios, normalización de tipología) sigue viviendo en `dbt/macros/` y
`dbt/models/staging/`, que hoy corren sobre DuckDB en local. Portar eso
depende de qué warehouse productivo se elija (`config/connections.yml`,
entorno prod = TBD).

## Qué sube el CI

| Artefacto | Sale de |
| --- | --- |
| `scraper-infocasas.zip` | `lambdas/scraper/app.py` + `etl/extract/scrape_infocasas.py` + `src/cuadra_data/` + `lambdas/requirements-lambda.txt` |
| `transform.py` | `glue/transform.py` |
| `deps.zip` | `glue/requirements-glue.txt` (pg8000) |

Antes de subir, el workflow importa el handler desde el zip armado: una
dependencia que falte no se nota al empaquetar, explota en el cold start de la
primera corrida, una semana después del deploy.

## Sumar una fuente

Con el contrato de arriba, son cuatro pasos y ninguno toca el job de Glue:

1. **Extractor** en `etl/extract/`, escribiendo las columnas del contrato.
   Si la corrida puede pasar de ~12 min, que acepte un rango de páginas
   (`--start-page` / `--max-pages`, como InfoCasas) para poder partirla.
2. **Handler** en `lambdas/`: los dos modos (`plan` y `extract`) si va en
   tandas, o solo `extract` si entra en una invocación. Copiar
   `lambdas/scraper/app.py`.
3. **Infra** en `pipeline.yaml`: otra `AWS::Lambda::Function` igual a
   `InfoCasasFunction` con su `FUENTE`, su log group, el ARN en
   `StateMachineRole` y en `DefinitionSubstitutions`, y **otra rama** en el
   `Parallel` de `Scraping`.
4. **CI**: otro zip en el paso de empaquetado y su `aws s3 cp`.

Si la fuente nueva falla, hoy falla la corrida entera (una sola rama). Con dos
o más fuentes conviene revisar esa decisión: lo razonable pasa a ser avisar y
seguir con las otras.

## Configuración del repo

Secrets: `AWS_DEPLOY_ROLE_ARN` (output `PipelineDeployRoleArn` del stack base),
`CFN_EXEC_ROLE_ARN` (output `CfnExecutionRoleArn`).

Variables (todas opcionales): `AWS_REGION`, `PROJECT`, `ENVIRONMENT`,
`SCHEDULE_EXPRESSION`, `PAGINAS_POR_TANDA`.

El runbook completo y la tabla de todos los secrets/variables están en el repo
del backend: `infra/README.md`.
