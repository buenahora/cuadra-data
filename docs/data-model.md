# Cuadra — Modelo de datos (inferido del prototipo)

> Este documento traduce las 20 pantallas del prototipo clickeable (`src/screens/`, datos hardcodeados en `src/data/`) a entidades candidatas para la data layer. Todo lo que sigue está **inferido de la UI existente**, no es un schema definitivo — es el punto de partida para que el equipo de backend lo discuta y ajuste. Los nombres de campo están en español para mantener consistencia con el dominio (barrio, expensas, avisos, etc.), tal como los usa el producto.

## 1. Vista general de entidades

```
User (usuario)
 ├─ publica → Listing (aviso)          [1 usuario : N avisos]
 ├─ envía  → Inquiry (consulta)         [1 usuario : N consultas]
 └─ guarda → Favorite (favorito)        [N:N con Listing]

Listing (aviso)
 ├─ pertenece a → Zone (barrio)
 ├─ tiene → Photo[] (3–10)
 ├─ tiene → Amenity[] (comodidades, N:N)
 ├─ acepta → Guarantee[] (garantías, N:N)
 ├─ recibe → Inquiry[] (consultas)
 └─ opcional: owner_contact (dueño real, si lo publica un corredor)

Zone (barrio)
 └─ agrega → MarketStat (estadística calculada, por tipología)

MarketStat (min/mediana/máximo/avisos activos, por zona+dormitorios+período)
 └─ se compone de → MarketSnapshot[] (serie histórica mensual)

Report (informe PDF de corredor)
 ├─ generado por → User (corredor)
 ├─ referencia → Zone + tipología + período
 └─ dirigido a → owner_contact (nombre del propietario destinatario)
```

## 2. `User` (usuario)

Pantallas: `LA` (login), `LB` (registro).

| Campo | Tipo | Notas |
|---|---|---|
| id | uuid | |
| nombre_completo | string | requerido en registro |
| email | string | requerido, único |
| telefono | string | opcional en registro |
| password_hash | string | mínimo 8 caracteres (regla de UI) |
| google_id | string? | login social "Continuar con Google" está en el prototipo pero sin lógica real |
| creado_en | timestamp | |

**No hay un campo `rol` explícito en las pantallas de auth.** El registro es genérico ("Creá tu cuenta") y sirve tanto para quien busca como para quien publica — de hecho el flujo del inquilino (`1D → LA/LB`) usa la misma pantalla de login que necesitaría un propietario. Dos interpretaciones posibles, a decidir con el equipo:

- **(a) Un usuario, múltiples capacidades:** cualquier usuario puede buscar, publicar y (si gestiona propiedades de terceros) actuar como corredor. El "grupo" es un comportamiento, no un rol fijo.
- **(b) Rol explícito** (`inquilino` / `propietario` / `corredor`) elegido en el registro o inferido la primera vez que publica un aviso a nombre de un tercero.

Dado que el corredor "gestiona avisos de varios propietarios desde un mismo perfil" (informe, meta de Diego), probablemente conviene un campo `es_corredor: boolean` o similar en vez de un enum rígido de rol único, para no bloquear a alguien que además busca su propio apartamento.

## 3. `Listing` (aviso)

Pantallas: `1C` (detalle), `2B–2E` (wizard de publicación), `2F` (mis publicaciones), `3B` (cartera).

| Campo | Tipo | Origen en UI | Notas |
|---|---|---|---|
| id | uuid | | |
| titulo | string | S1C `h1` | ej. "Monoambiente luminoso en el Cordón" |
| descripcion | string(≤500) | S2E | límite de 500 caracteres en el wizard |
| direccion | string | S2B | |
| barrio | FK Zone | S2B (select de 15 barrios) | ver §5 |
| entre_calles | string? | S2B | referencia opcional |
| ocultar_direccion | boolean | S2B | "no mostrar la dirección exacta, solo el barrio" — afecta qué se expone en el aviso público |
| lat, lng | float | S1C usa Google Maps con coordenadas fijas | requiere geocoding real al guardar dirección |
| dormitorios | int | S2C stepper (0–8) | |
| banos | int | S2C stepper (1–6) | |
| metraje_m2 | int | S2C | |
| piso | int? | S2C | |
| planta_baja | boolean | S2C | |
| amueblado | enum('Sí','No','Parcial') | S2C | |
| permite_mascotas | boolean | S2C | |
| amenities | string[] (N:N) | S2C | ver lista en §4 |
| expensas_incluidas | boolean | S2C | |
| expensas_monto | decimal? | S2C | solo si `expensas_incluidas = false` |
| precio | decimal | S2D | mensual, en pesos uruguayos |
| precio_negociable | boolean | S2D | |
| garantias_aceptadas | string[] (N:N) | S2D | ver lista en §4 |
| fotos | Photo[] | S2E | mínimo 3, máximo 10 |
| disponible_desde | date | S2E | |
| disponible_hasta | date? | S2E | opcional, "sin límite" |
| estado | enum | S2F / S3B | ⚠️ ver nota de inconsistencia abajo |
| publicado_por | FK User | | quien crea el aviso (propietario o corredor) |
| owner_contact_nombre | string? | S3B ("C. Suárez", "J. Methol") | dueño real, cuando quien publica es un corredor y el dueño no es un usuario de la plataforma |
| lease_vencimiento | date? | S3B (`venc`) | solo aplica a avisos con estado "Alquilada" |
| creado_en, actualizado_en | timestamp | | |

**⚠️ Inconsistencia de estados a resolver:** el panel del propietario (`2F`) usa `Activo` / `Pausado`, mientras que la cartera del corredor (`3B`) usa `Disponible` / `Alquilada` / `En proceso`. Son dos vocabularios para conceptos parcialmente distintos: uno es "¿el aviso está visible?" (publicación) y el otro es "¿en qué etapa comercial está la propiedad?" (ocupación). Probablemente conviene **separar dos campos**:
- `estado_publicacion`: `activo | pausado | archivado` (visibilidad del aviso)
- `estado_comercial`: `disponible | en_proceso | alquilada` (estado real de la propiedad, relevante sobre todo para la vista de cartera del corredor)

### Contador de consultas
`2F` muestra un badge de cantidad de consultas por aviso (`q: 3`, `q: 1`, `q: 0`) — se deriva de contar `Inquiry` por `listing_id`, no es un campo propio.

## 4. Catálogos (`amenities` y `garantias`)

Amenities/comodidades (checklist multi-select, S2C):
`Cochera / Garaje`, `Balcón / Terraza`, `Ascensor`, `Aire acondicionado`, `Placares empotrados`, `Parrillero`, `Piscina`, `Gimnasio`, `Portería 24 hs`, `Calefacción`, `Lavadero`, `Seguridad / Cámaras`.

Garantías aceptadas (checklist multi-select, S2D) — específico del mercado de alquiler uruguayo:
`ANDA`, `Porto Seguro`, `Garantía de Contaduría (BHU)`, `Garantía propietaria`, `Depósito en efectivo`.

Ambos catálogos deberían ser tablas propias (no enums hardcodeados) porque son datos de dominio local que probablemente cambien (nuevas aseguradoras de garantías, nuevos amenities) sin requerir deploy de código.

## 5. `Zone` (barrio)

Dos listas de barrios aparecen en el prototipo y **no coinciden exactamente**, lo cual hay que unificar:

- **Barrios para elegir al publicar** (S2B, 15 barrios): Pocitos, Cordón, Parque Rodó, Punta Carretas, La Blanqueada, Tres Cruces, Palermo, Buceo, Malvín, Carrasco, Centro, Ciudad Vieja, Prado, Sayago, Cerro.
- **Barrios con estadística de mercado calculada** (S2A / S3A, 6 barrios): Pocitos, Cordón, Parque Rodó, Punta Carretas, La Blanqueada, Tres Cruces.

Esto sugiere que en el arranque real, **no todos los barrios tendrán suficiente volumen de avisos para calcular una mediana confiable** — el modelo debe soportar zonas "sin datos suficientes" (mostrar mensaje en vez de estadística inventada) y priorizar la cobertura de datos en los barrios de mayor demanda primero.

| Campo | Tipo | Notas |
|---|---|---|
| id | uuid/slug | ej. `cordon`, `pocitos` |
| nombre | string | |
| ciudad | string | hoy solo Montevideo |
| geometria | polygon (geo) | límite administrativo del barrio, para poder decir "esta dirección cae en Cordón" |

**Zona dibujada / radio desde un punto** (S1B, S1E, S3A "Dibujar zona") **no es una `Zone` persistida** — es un polígono o círculo ad-hoc generado por el usuario en tiempo real para filtrar resultados. Requiere que el backend soporte consultas geoespaciales arbitrarias sobre `Listing.lat/lng` (ej. PostGIS `ST_Within` / `ST_DWithin`), no solo joins contra la tabla `Zone`.

## 6. `Inquiry` (consulta)

Pantallas: `1D` (formulario de contacto), `2G` (consultas recibidas por el propietario).

| Campo | Tipo | Notas |
|---|---|---|
| id | uuid | |
| listing_id | FK Listing | |
| remitente_user_id | FK User? | S1D pide login antes de contactar, así que probablemente siempre hay usuario asociado |
| nombre, email, telefono | string | telefono opcional |
| fecha_mudanza | date? | opcional |
| mensaje | text | |
| respuesta | text? | S2G permite al propietario responder desde la plataforma |
| creado_en, respondido_en | timestamp | |

## 7. `Favorite` (favorito)

`1C` tiene un botón "Guardar aviso". Tabla puente simple `user_id` + `listing_id` + `creado_en`.

## 8. Motor de mercado — `MarketStat` y `MarketSnapshot`

Esto es el corazón de negocio de Cuadra y se detalla en `market-intelligence.md`, pero a nivel de entidades:

**`MarketStat`** (estadística vigente, recalculada periódicamente):
| Campo | Tipo |
|---|---|
| zone_id | FK Zone |
| dormitorios_bucket | enum('1','2','3+') |
| metraje_bucket | enum? | usado como filtro en S3A (`< 40m²`, `40–60m²`, `60–80m²`, `>80m²`), no confirmado si afecta el cálculo o solo filtra la tabla de comparables |
| precio_min, precio_mediana, precio_max | decimal | |
| avisos_activos | int | tamaño de muestra — importante mostrarlo siempre junto al dato (ya lo hace la UI: "Basado en 38 avisos activos") |
| variacion_interanual | decimal (%) | |
| actualizado_en | timestamp | |

**`MarketSnapshot`** (histórico mensual, alimenta el gráfico de tendencia de 12 meses):
| Campo | Tipo |
|---|---|
| zone_id | FK Zone |
| dormitorios_bucket | enum |
| periodo | date (mes) |
| precio_mediana | decimal |

Este histórico es lo que permite responder "¿subieron los precios en esta zona en el último año?" — uno de los tres problemas estructurales que el producto promete resolver. **No puede derivarse retroactivamente**: si la plataforma no captura snapshots desde el día uno, no habrá histórico real para mostrar en el año 1. Ver `roadmap.md` y `market-intelligence.md §Arranque en frío`.

## 9. `Report` (informe de corredor)

Pantalla `3C`. Es la única entidad orientada 100% al corredor.

| Campo | Tipo | Notas |
|---|---|---|
| id | uuid | |
| generado_por | FK User (corredor) | |
| titulo | string | ej. "Valuación Pocitos · 1 dorm" |
| destinatario_nombre | string | nombre del propietario receptor (no necesariamente un `User`) |
| zone_id, dormitorios_bucket | FK / enum | qué segmento de mercado cubre |
| periodo | enum('3 meses','6 meses','12 meses') | |
| secciones_incluidas | string[] | `precio_mediano`, `rango_de_precios`, `evolucion_historica`, `avisos_comparables` — todas configurables/desmarcables en la UI |
| pdf_url | string | archivo generado |
| generado_en | timestamp | |

## 10. Resumen de brechas frontend → backend

Cosas que hoy están **hardcodeadas en el prototipo** (`src/data/photos.js`, datos inline en cada screen) y que la data layer debe reemplazar con fuentes reales:

- Coordenadas de mapa fijas (`MARKERS`, `CENTER`, `LAT/LNG` en S1A/S1C) → requiere geocoding real de direcciones.
- Tabla `MARKET` completa en S2A/S3A (medianas, min/max, tendencias de 12 meses, cantidad de avisos) por barrio y dormitorios → requiere el motor de agregación descrito en `market-intelligence.md`.
- Fotos de stock (`data/photos.js`) → requiere upload/almacenamiento real de imágenes por aviso.
- Listas de "consultas recibidas" y "mi cartera" con nombres fijos → requieren las tablas `Inquiry` y `Listing` reales descritas arriba.
- Login con Google presente en la UI pero sin integración real (OAuth pendiente).
