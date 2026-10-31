# Cuadra — Roadmap hacia la data layer

> Este roadmap parte de un supuesto: **el frontend (prototipo clickeable) ya existe y no cambia de alcance funcional** — el trabajo que sigue es construir la capa de datos y backend que lo sostenga con información real, en el orden que menos bloquea al equipo y menos riesgo de negocio genera. Se apoya en `business.md`, `data-model.md` y `market-intelligence.md`.

## Principio guía

El riesgo más grande del proyecto **no es técnico, es de datos**: el motor de "precio vs. mercado" (la propuesta de valor central) no funciona sin volumen de avisos por zona. Por eso el roadmap prioriza desbloquear la acumulación de datos propios lo antes posible, incluso con un motor de mercado simplificado, en vez de construir primero el motor de mercado "completo" sin tener qué agregar.

## Fase 0 — Decisiones de negocio previas (bloqueantes, no técnicas)

Antes de tocar schema, el equipo debería resolver las preguntas abiertas de `business.md §8`, en particular:
- ¿Rol explícito de usuario o comportamiento libre? (afecta el modelo de `User` day one — `data-model.md §2`)
- ¿Estrategia de arranque en frío para datos de mercado? (afecta si Fase 2 necesita un pipeline de ingesta externa o no — `market-intelligence.md §4`)
- ¿Cobertura geográfica inicial (solo Montevideo o más)?

No son decisiones técnicas, pero cambian el schema y el orden de las fases siguientes, así que conviene cerrarlas primero.

## Fase 1 — Núcleo: usuarios, avisos, contacto (habilita Grupos 1 y 2 sin inteligencia de mercado)

Objetivo: que el flujo `1A→1B→1C→1D→1D-ok` (buscar y contactar) y `2A→2B→2C→2D→2E→2E-ok` (publicar) funcionen con datos reales, aunque el "precio vs. mercado" todavía muestre un placeholder o quede oculto.

- `User`, autenticación (email/password; OAuth Google queda para después — está en la UI pero sin lógica real).
- `Listing` completo con todos sus campos (`data-model.md §3`), incluyendo el catálogo de `amenities` y `garantias` (§4) como tablas propias, no enums de código.
- `Zone` con las 15 barrios listados en el wizard (`data-model.md §5`) — sin estadística todavía, solo como catálogo para el selector y para poder geolocalizar avisos.
- Geocoding real de direcciones → `lat/lng` (hoy hardcodeado en el prototipo).
- Almacenamiento real de fotos (mínimo 3, máximo 10 por aviso, regla ya definida en la UI).
- `Inquiry` (formulario de contacto + panel "consultas recibidas" del propietario, S1D/S2G) y `Favorite` (guardar aviso, S1C).
- Resolver la inconsistencia de `estado` del aviso (`estado_publicacion` vs `estado_comercial`, `data-model.md §3`) antes de que ambas vistas (propietario y corredor) dependan de un campo mal definido.

**No entra en esta fase:** búsqueda por polígono dibujado ni por radio (puede simularse con filtro simple por barrio mientras tanto), chip de mercado real (puede mostrarse "sin datos suficientes" u ocultarse).

## Fase 2 — Motor de inteligencia de mercado (la pieza diferencial)

Objetivo: que el chip "vs. mercado", la consulta de precio pre-publicación (S2A) y la retroalimentación en tiempo real al fijar precio (S2D) usen datos reales, aunque sea con cobertura parcial.

- Definir y ejecutar la estrategia de arranque en frío decidida en Fase 0 (`market-intelligence.md §4`): si incluye ingesta externa, ese pipeline (scraping/compra de datos/carga manual) es prerrequisito de esta fase, no puede improvisarse a mitad de camino.
- Implementar `MarketStat` (cálculo min/mediana/máximo/avisos-activos por zona×dormitorios) con reglas explícitas de "muestra insuficiente" (`market-intelligence.md §6`).
- Empezar a capturar `MarketSnapshot` mensual **desde el primer mes de esta fase**, aunque el histórico de 12 meses no exista todavía — es la única forma de tener un gráfico de tendencia real dentro de un año (`market-intelligence.md §5`).
- Servicio de clasificación `below/in/above` reutilizado en S1C, S2A y S2D, con el umbral (hoy 8% hardcodeado en el prototipo) como parámetro configurable, no constante de código.
- Definir si el filtro de metraje (`< 40m²`, etc., visible en S2A/S3A) entra al cálculo de la mediana o es solo un filtro de la tabla de comparables — decisión pendiente señalada en `market-intelligence.md §2`.

**Criterio de salida de esta fase:** al menos los 6 barrios que ya tienen estadística en el prototipo (Pocitos, Cordón, Parque Rodó, Punta Carretas, La Blanqueada, Tres Cruces) muestran datos reales con tamaño de muestra visible; el resto de barrios puede seguir mostrando "sin datos suficientes" sin bloquear el lanzamiento.

## Fase 3 — Búsqueda geoespacial libre

Objetivo: los flujos `1A→1B` (dibujar zona en el mapa) y `1A→1E→1C` (buscar cerca de un punto) dejan de ser mockups y consultan `Listing` por geometría real.

- Consulta por polígono arbitrario sobre `lat/lng` de avisos (zona dibujada a mano).
- Consulta por radio desde un punto + orden por distancia (S1E, "9 apartamentos", "orden: por distancia a pie").
- Si se quiere replicar exactamente "a pie del trabajo" (S1F, distancia caminando, no lineal), se necesita una capa de ruteo peatonal, no solo distancia euclidiana — evaluar si el MVP se conforma con distancia en línea recta primero.
- Comparador de hasta 4 avisos (S1F) — probablemente no necesita persistencia backend, es estado de sesión del cliente, salvo que se quiera guardar comparaciones entre sesiones.

## Fase 4 — Corredor: cartera y reportes

Objetivo: el flujo `3A→3C` (análisis de zona → generar informe) y `3B` (mi cartera) quedan operativos para el Grupo 3.

- Extender `Listing` para soportar `owner_contact_nombre` y `lease_vencimiento` (propiedades de terceros gestionadas por un corredor, `data-model.md §3`).
- Vista de cartera con filtros por `estado_comercial` (S3B: Todas/Alquiladas/Disponibles/En proceso).
- Generación real de `Report` en PDF (S3C): título, destinatario, período, secciones configurables, usando los datos de `MarketStat`/`MarketSnapshot` de Fase 2 — por eso esta fase depende de que Fase 2 ya tenga cobertura razonable, al menos en las zonas de la cartera de corredores piloto.

## Fase 5 — Endurecimiento y features de negocio pendientes de decidir

No hay pantallas en el prototipo para estas features, pero se desprenden de las preguntas abiertas de `business.md §8` y conviene planificarlas antes de escalar:

- Verificación de identidad de quien publica (propietario vs. corredor matriculado).
- Modelo de monetización (informes pagos para corredores, avisos destacados, etc. — a definir).
- Métricas de producto descritas en `market-intelligence.md §7` (correlación consultas↔nivel de precio) para reemplazar afirmaciones sin fuente ("un precio en rango recibe 3 veces más consultas") por datos reales propios.
- Expansión geográfica más allá de Montevideo, si aplica.

## Dependencias entre fases (resumen)

```
Fase 0 (decisiones de negocio)
   ↓
Fase 1 (usuarios, avisos, contacto)  ──────────────┐
   ↓                                                │
Fase 2 (motor de mercado) ← depende de Fase 0       │
   ↓                                                │
Fase 3 (búsqueda geoespacial) ← puede ir en paralelo con Fase 2, ambas dependen solo de Fase 1
   ↓
Fase 4 (cartera + reportes de corredor) ← depende de Fase 2 (necesita MarketStat real)
   ↓
Fase 5 (monetización, verificación, analytics)
```

La dependencia más importante a respetar: **Fase 2 debe empezar a capturar histórico mensual lo antes posible**, incluso en paralelo con el resto del desarrollo de Fase 1, porque es la única fase cuyo valor depende del tiempo transcurrido y no solo de las horas de desarrollo invertidas.
