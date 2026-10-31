# Cuadra — Motor de inteligencia de mercado

> El diferencial de negocio de Cuadra (ver `business.md`) no es publicar avisos, es **contextualizarlos con datos de mercado**. Este documento describe, a partir de cómo el prototipo consume estos datos hoy (hardcodeados), qué necesita construir la data layer para que sea real. Es la pieza más crítica y más riesgosa del roadmap porque depende de volumen de datos, no solo de código.

## 1. Qué promete el producto (de `business.md`)

1. Cada aviso muestra un indicador de si su precio está **por debajo / dentro / por encima** de la mediana del mercado, para su zona y tipo de apartamento.
2. Antes de publicar, el propietario puede consultar **mínimo, mediana y máximo** de su zona y tipología.
3. Se muestra **evolución histórica de precios** por zona (12 meses), con variación interanual.
4. El corredor puede generar un **informe PDF** con estos datos para justificar valuaciones ante un propietario.

Estas cuatro promesas son, en términos de datos, la misma fuente subyacente (avisos agregados por zona + tipología + tiempo) consumida de cuatro formas distintas.

## 2. Las tres dimensiones de segmentación

Todas las pantallas que muestran estadísticas de mercado (`S1C`, `S2A`, `S2D`, `S3A`) segmentan por la misma combinación:

- **Zona** (barrio, o zona dibujada a mano / radio desde un punto — ver `data-model.md §5`)
- **Dormitorios** (`1`, `2`, `3+`)
- **Metraje** (aparece como filtro en `S2A` y `S3A`: `< 40m²`, `40–60m²`, `60–80m²`, `> 80m²`) — en el prototipo el metraje es un filtro de UI pero **no está claro si afecta el cálculo de la mediana** o solo filtra qué comparables se listan en la tabla. Definir esto es una decisión de producto antes de implementar el motor: si el metraje entra al cálculo, cada combinación zona×dormitorios×metraje necesita volumen propio de avisos, lo cual multiplica el problema de "masa crítica" descrito en §4.

## 3. Cómo se calcula el chip "vs. mercado" (lógica ya definida en el prototipo)

El wizard de precio (`S2D`) sí tiene la lógica de clasificación implementada en el frontend — es la única regla de negocio explícita, y conviene preservarla al mover el cálculo al backend:

```
nivel = 'below'  si precio < mediana * 0.92   (más de 8% por debajo)
nivel = 'above'  si precio > mediana * 1.08   (más de 8% por encima)
nivel = 'in'     en cualquier otro caso
```

- El umbral (8%) está hardcodeado en el prototipo, no es un valor validado con datos reales. Al construir el motor real conviene dejarlo como parámetro configurable, no una constante de código, para poder ajustarlo con evidencia real de mercado.
- El copy que acompaña cada nivel ya está definido y es parte de la propuesta de valor (S2D `INFO`):
  - **below:** "Un precio bajo puede atraer más consultas, pero puede generar desconfianza sobre la calidad."
  - **in:** "Un precio dentro del rango de mercado recibe 3 veces más consultas." *(esta afirmación estadística — "3 veces más consultas" — no tiene fuente en el informe; si se mantiene en producción debe validarse con datos propios de la plataforma o quitarse el número específico.)*
  - **above:** "Un precio por encima del mercado puede reducir significativamente las consultas recibidas."

## 4. El problema de arranque en frío (cold start)

Este es el riesgo más grande del proyecto y no está resuelto en el material fuente — es una decisión de negocio pendiente (ver `business.md §8`).

Las medianas y el histórico de 12 meses **solo son confiables con volumen suficiente de avisos por combinación zona×dormitorios(×metraje)**. El propio prototipo lo reconoce implícitamente: solo 6 de los 15 barrios listados como opción de publicación tienen estadística de mercado calculada (`data-model.md §5`), y cada estadística muestra su tamaño de muestra ("Basado en 38 avisos activos").

En el día 1, Cuadra no tiene avisos propios ni histórico. Opciones a evaluar con el equipo (ninguna confirmada en el material fuente):

| Estrategia | Pros | Contras |
|---|---|---|
| **Solo datos propios** (esperar a que se acumule volumen orgánico publicando en Cuadra) | Datos limpios, 100% propios, sin riesgo legal de scraping | La promesa de valor ("sabé si el precio es justo") no funciona hasta tener volumen — puede tardar meses/años en barrios con poca actividad |
| **Ingesta de datos externos** (scraping de Gallito/Infocasas/MercadoLibre u otras fuentes públicas) | Resuelve el arranque en frío rápido | Riesgo legal/ToS de los portales de origen; datos de terceros no siempre confiables o actualizados; requiere pipeline de scraping + limpieza + deduplicación |
| **Dataset semilla curado manualmente** (releva​miento manual inicial, encuestas a corredores, compra de datos a terceros) | Control total de calidad | Costoso, no escala, se desactualiza rápido |
| **Híbrido**: semilla externa + reemplazo progresivo por datos propios a medida que crece el volumen orgánico | Balance realista | Requiere marcar el origen del dato (`fuente: externa | propia`) y decidir cuándo un barrio tiene "masa crítica" propia para dejar de depender de la fuente externa |

**Recomendación para el roadmap** (ver `roadmap.md`): no bloquear el lanzamiento del motor de mercado a tener datos perfectos — pero sí exponer siempre el tamaño de muestra y, si se usan datos externos, marcarlos como tales, para no violar la promesa de marca "Transparente" (`business.md §6`).

Para el "cómo" técnico de la opción de ingesta externa (InfoCasas y MercadoLibre concretamente, con pruebas empíricas de qué tan bloqueados están y qué approach conviene para cada uno), ver `scraping-strategy.md`.

## 5. Histórico: por qué hay que empezar a capturarlo ya

`MarketSnapshot` (`data-model.md §8`) es una foto mensual de la mediana por zona×dormitorios. **No se puede reconstruir retroactivamente si no se capturó en su momento** (salvo que se consiga data histórica externa). Esto tiene una implicación de secuencia importante para el roadmap: el job que genera snapshots mensuales debería correr desde el primer mes de operación, aunque el volumen de avisos sea bajo — de lo contrario, dentro de 12 meses seguirá sin haber gráfico de tendencia real que mostrar.

## 6. Qué necesita el backend, en términos de capacidades (no de stack)

1. **Job de agregación recurrente** (ej. mensual) que recalcule `MarketStat` por cada combinación zona×dormitorios(×metraje) a partir de `Listing.precio` de avisos activos, y escriba un punto nuevo en `MarketSnapshot`.
2. **Reglas de calidad de dato**: qué pasa si una combinación tiene 0, 1 o 2 avisos — la UI no debería mostrar una mediana con muestra insignificante como si fuera confiable (el prototipo no cubre este caso porque todos sus datos de ejemplo tienen volumen).
3. **Servicio de clasificación** que, dado un `precio` propuesto/publicado y su zona+dormitorios, devuelva `below | in | above` + porcentaje de diferencia — reutilizable tanto en la consulta de mercado (S2A) como en el wizard de precio (S2D) como en el detalle del aviso (S1C).
4. **Búsqueda geoespacial** sobre `Listing.lat/lng`: por polígono arbitrario (zona dibujada) y por radio desde un punto (búsqueda cerca del trabajo). No es parte del motor de mercado en sí, pero es la misma capa geoespacial que probablemente termine reutilizando `Zone.geometria` para asignar cada aviso a un barrio automáticamente al publicarlo.
5. **Generación de reportes**: renderizar a PDF la combinación de `MarketStat` + `MarketSnapshot` + listado de comparables para una zona/tipología/período, con las secciones que el corredor elija incluir (`data-model.md §9`).

## 7. Métricas que valdría la pena trackear desde el día uno

Aunque no están en el informe (es un proyecto de diseño, no de producto/analytics), para poder eventualmente validar afirmaciones como "un precio dentro de rango recibe más consultas" con datos propios en vez de una cifra sin fuente, conviene registrar desde el inicio:
- Consultas (`Inquiry`) por aviso, correlacionadas con el `nivel` (`below/in/above`) que tenía el aviso al momento de recibir cada consulta.
- Tiempo entre publicación y primera consulta, por nivel de precio.
- Tasa de avisos que pasan a "Alquilada" por nivel de precio.

Esto no es un requisito del MVP, pero si se captura desde temprano permite reemplazar afirmaciones de marketing sin respaldo por datos reales de la propia plataforma más adelante.
