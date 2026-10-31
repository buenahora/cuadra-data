# Language convention

- **Column/table names**: don't mix Spanish and English within the same table or model. This codebase's domain naming is Spanish (`barrio`, `dormitorios`, `precio`, `estado`, `publicado_en`, ...) — new columns must follow that, not introduce English ones alongside them (e.g. no `data_source`/`ingested_at` next to `barrio`/`precio`; use `fuente_datos`/`ingestado_en` instead).
- **Chat**: talk to the user in Spanish.
