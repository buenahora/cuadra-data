# Cuadra — Estrategia de extracción de datos (MercadoLibre e InfoCasas)

> Plan para resolver el "arranque en frío" descrito en `market-intelligence.md §4`: de dónde sale la data real de avisos de alquiler. Todo lo de acá está basado en pruebas empíricas hechas hoy (curl/fetch reales contra ambos sitios), no en supuestos — están marcadas como tal en la Sección 0.

## 0. Lo que probé hoy (hechos, no supuestos)

**MercadoLibre:** tanto el sitio (`mercadolibre.com.uy/inmuebles/...`) como la API pública sin autenticar (`api.mercadolibre.com/sites/MLU/search`) devuelven **403 Forbidden** con un fetch simple, sin ejecutar JS. Su `robots.txt` además bloquea explícitamente bots de IA (`ClaudeBot`, `GPTBot`, `Amazonbot` → `Disallow: /`). Esto confirma que ML tiene protección agresiva a nivel de borde (consistente con lo que reportás — típico de Akamai/PerimeterX o similar), no es algo que se resuelva con un fetch más prolijo.

**InfoCasas:** un `curl` simple con user-agent de navegador normal (sin JS, sin cookies, sin proxy) devuelve **HTTP 200 con el contenido completo**, sin CAPTCHA ni challenge. Y hay más:

- Su `robots.txt` publica **22 sitemaps distintos**, incluyendo sitemaps específicos de propiedades (`properties-1-sitemap.xml`, `properties-2-sitemap.xml`, etc.) y un feed **RSS de listings** (`rss-listings`) — es una vía de descubrimiento estructurada que el sitio ofrece activamente, no algo que haya que inferir.
- Cada página de detalle de aviso trae embebido un **JSON completo y tipado** (`<script id="__NEXT_DATA__">`, típico de Next.js) con absolutamente todos los campos: `price`, `address`, `latitude/longitude`, `bedrooms`, `bathrooms`, `m2`, `facilities`, `owner` (agencia/teléfono/whatsapp), `images`, `description`, etc. No hace falta parsear HTML visual ni usar un LLM para extraer esto — es `json.loads()` directo.
- El frontend corre contra un backend GraphQL propio: `runtimeConfig.API_URL = "https://graph.infocasas.com.uy"`. No llegué a confirmar el path exacto del endpoint (un POST a `/` dio 404 — hace falta inspeccionar el Network tab del browser mientras se usa el buscador para encontrar la ruta real), pero es una pista fuerte de que existe una forma de pedir muchos avisos estructurados en un solo request, sin tocar HTML en absoluto.

**Conclusión que cambia el enfoque:** para InfoCasas, el scraping "tradicional" que asumías que no iba a andar, **en los hechos sí anda** con un fetch simple y educado. El bloqueo que viste probablemente vino del *patrón* de scraping (headers default de librería, sin pacing, pegándole a las combinaciones de filtros que el propio `robots.txt` marca como trampa para crawlers) y no de una protección técnica real. Para MercadoLibre la situación es distinta: ahí sí hay protección real y agresiva, y conviene una estrategia distinta desde el arranque.

## 1. Dos problemas distintos, no los mezcles

- **Acceso**: conseguir el contenido de la página sin que te bloqueen.
- **Extracción**: convertir ese contenido en datos estructurados (precio, dirección, dormitorios...).

Los LLMs (Haiku) resuelven muy bien el problema de **extracción** — son robustos a rediseños de la página porque entienden semántica en vez de depender de selectores CSS frágiles. Pero **no resuelven el problema de acceso por sí solos**: si no podés ni descargar la página, ningún LLM ayuda. Para InfoCasas casi no hace falta LLM para extracción (hay JSON perfecto ya). Para MercadoLibre, incluso resolviendo el acceso, probablemente sí convenga LLM para extracción porque las fichas varían más entre categorías.

## 2. Plan para InfoCasas (ordenado por velocidad/costo)

### Nivel 0 — **implementado 2026-08-13**, `etl/extract/scrape_infocasas.py`
Terminó siendo mejor que lo planteado originalmente acá: no hizo falta ni sitemap ni reversear GraphQL por separado. **Las propias páginas de resultados de búsqueda** (`infocasas.com.uy/alquiler/apartamentos/montevideo` y `/pagina2`, `/pagina3`, ...) traen embebido un `__NEXT_DATA__` con **21 avisos completos por página** (`fetchResult.searchFast.data`): precio, moneda, dormitorios, baños, m², barrio, fecha de publicación — todo. Total nominal reportado por el sitio: **~7.540 avisos activos de alquiler de apartamentos en Montevideo**, en ~360 páginas.
**Extracción:** `requests` simple (sin JS, sin browser), pacing de 1.2s entre requests (~7-9 min para el dataset completo), user-agent de navegador real, chequeo de `robots.txt` en runtime antes de arrancar (vía `urllib.robotparser`), normalización de precios en USD a UYU con el tipo de cambio que la propia respuesta trae.
**Costo:** ~$0. **Velocidad:** alta (~50 páginas/min → 1.000+ avisos/min). **Riesgo:** bajo, la ruta usada no está en ningún `Disallow` del `robots.txt`.
Reemplaza a `generate_synthetic_listings.py` para uso real; ese script se mantiene para tests/desarrollo offline (ver `etl/extract/scrape_infocasas.py` docstring).

**Gotcha real, encontrado y corregido el mismo día:** la primera corrida completa (359 páginas) devolvió solo 5.936 avisos únicos de un nominal de 7.525 — **21% perdido**. Causa: el orden default del sitio es "Popularidad" (`?order=2`, implícito), que se recalcula en vivo todo el tiempo (no solo por avisos nuevos — el score de avisos *ya vistos* cambia solo). Paginar por índice sobre una lista que se reordena sola pierde y duplica avisos sin aviso. Solución: mandar `?order=1` (precio ascendente) explícitamente — confirmado empíricamente estable entre páginas (el precio de un aviso existente no cambia en los ~9 min que dura la corrida). Con esto, el único drift restante es el volumen real de avisos publicados/pausados durante la corrida misma, mucho menor. Ver docstring de `scrape_infocasas.py` para el detalle; el log de cada corrida ahora imprime `cobertura vs. total nominal del sitio` al final para que esto sea visible, no silencioso.

### Nivel 1 — si el volumen alto empieza a generar bloqueos
Rotar user-agents realistas + delay aleatorio + registro de qué IDs ya se scrapearon (para no re-pedir). Sigue siendo HTTP simple, sin headless browser.
**Costo:** ~$0. **Setup:** horas.

### Nivel 2 — si igual empieza a bloquear por IP/volumen
Proxies rotativos delante del mismo fetch simple (Decodo/Scrape.do arrancan ~$29-49/mes).
**Costo:** bajo-medio. **Velocidad:** sigue siendo alta (no hay overhead de browser real).

### Nivel 3 — LLM para extracción (Haiku), solo como fallback
Si en algún momento InfoCasas deja de exponer `__NEXT_DATA__` (rediseño, ofuscación), se cambia *solo* la etapa de extracción: el fetch sigue siendo HTTP simple, pero en vez de `json.loads()` se manda el HTML recortado (solo el bloque de la ficha, no el layout completo) a Haiku con un JSON schema de salida. Con HTML recortado esto cuesta centavos de dólar cada 1000 páginas.
**Cuándo usarlo:** no de entrada — hoy no hace falta, InfoCasas ya da JSON limpio.

## 3. Plan para MercadoLibre (ordenado por velocidad/costo, dado que el acceso simple está bloqueado)

### Opción A — API oficial de Developers (probar primero, pero con expectativas bajas)
**Actualización 2026-08-13, con pruebas reales:** esta opción era más optimista en la primera versión de este doc. Lo que confirmé con requests directos contra `api.mercadolibre.com`:

- `/categories/MLU1459` (metadata/taxonomía de "Inmuebles", sin auth) **funciona** — confirma `MLU1472 = Apartamentos`, 93.756 avisos totales en Uruguay (venta+alquiler). Es dato de referencia, no avisos individuales.
- `/sites/MLU/search`, `/sites/MLU` e **`/items/{id}`** (este último históricamente público) devuelven **403 `PA_UNAUTHORIZED_RESULT_FROM_POLICIES`** incluso sin nada raro en el request — ML cerró el acceso de lectura de forma amplia, no es algo puntual de inmuebles.
- El programa "Real Estate Experience" de ML está diseñado para que inmobiliarias **publiquen** sus propios avisos en ML (manejo de sellers, webhooks de agendamiento de visitas) — es el flujo inverso al que necesitamos (nosotros queremos *leer* el mercado, no publicar ahí).
- El "Developer Partner Program" (acceso más amplio) exige un **mínimo de GMV mensual transaccionado por país** — hay una barrera comercial real, no alcanza con un registro gratuito.
- `/oauth/token` sí responde normal (400 por falta de parámetros, no bloqueado), así que el flujo de auth en sí no está roto.

**No pude confirmar el paso final** (si un token de developer gratuito, ya autenticado, destraba `/sites/MLU/search`) porque requiere completar el registro y el login OAuth con una cuenta real de MercadoLibre — no es algo que pueda automatizar sin esas credenciales.
**Costo:** $0 si alcanza con el free tier. **Setup:** ~1 día (registro + flujo OAuth) para probarlo. **Riesgo:** bajo si funciona, pero **hay que tratarlo como "a validar", no como plan A confirmado** — dado que hasta `/items/{id}` está cerrado y el producto de inmuebles apunta a publicar-no-leer, es bastante posible que el free tier no alcance para búsqueda de mercado y haya que ir directo a la Opción B.
**Próximo paso concreto:** que alguien con cuenta de MercadoLibre registre una app, genere un access token, y pruebe un solo request autenticado a `/sites/MLU/search?category=MLU1472` — eso responde la pregunta en un ratito, mejor que seguir especulando.

### Opción B — Scraping-as-a-service especializado en anti-bot fuerte
Si la Opción A no cubre lo necesario. El 403 tanto en sitio como en API sin auth sugiere protección de nivel alto (tipo Akamai/PerimeterX). Para ese nivel, la comparativa 2026 reduce el campo a **Bright Data, Zyte u Oxylabs** (los únicos con proxy residencial + browser real al nivel de dar "señales de confianza de par").
**Costo:** Zyte arranca ~$1.01/1000 requests para targets fáciles pero escala fuerte en sitios protegidos; Bright Data arranca en $499/mes (71GB incluidos) — sensiblemente más caro que todo el plan de InfoCasas.
**Cuándo usarlo:** solo si la API oficial no expone algo específico que necesitás (ej. histórico de precio, algún campo particular).

### Opción C — Browser real controlado (Claude-in-Chrome), para volumen bajo
Sesión de Chrome real (cookies, fingerprint genuino, ritmo humano) para pulls puntuales o de bajo volumen — no escala a miles de páginas/hora, pero sirve para validar datos o llenar huecos puntuales sin construir infraestructura.
**Costo:** $0 adicional (ya disponible). **Riesgo:** bajo para volumen bajo; no es una solución de producción a escala.

### Opción D — Extracción con Haiku, se combina con A/B/C, no las reemplaza
Una vez resuelto el acceso (A, B o C), mandar el HTML/JSON obtenido a Haiku para normalizar campos tiene sentido porque las fichas de ML son menos uniformes entre categorías que las de InfoCasas.

## 4. Tabla resumen

| Sitio | Approach | Velocidad | Costo | Riesgo de bloqueo | Cuándo usarlo |
|---|---|---|---|---|---|
| InfoCasas | Nivel 0: páginas de resultados + `__NEXT_DATA__` | Alta | ~$0 | Bajo | **Hecho** (`etl/extract/scrape_infocasas.py`) |
| InfoCasas | Nivel 1: rotación de UA + pacing | Alta | ~$0 | Bajo | Si el volumen genera fricción |
| InfoCasas | Nivel 2: proxies rotativos | Alta | $29-49+/mes | Muy bajo | Si el volumen genera bloqueos por IP |
| InfoCasas | Nivel 3: extracción con Haiku (fallback) | Alta | Centavos/1000 págs | — | Solo si dejan de exponer JSON embebido |
| MercadoLibre | Opción A: API oficial Developers | Media (setup) / Alta (uso, si funciona) | $0 | Muy bajo, **si funciona** | Probar, pero no asumir que alcanza |
| MercadoLibre | Opción B: scraping-as-a-service (Bright Data/Zyte/Oxylabs) | Media | $$$ ($499+/mes o por request) | Bajo (pago justamente por eso) | Probable plan real, dado lo cerrado que está `/items` |
| MercadoLibre | Opción C: browser real (Claude-in-Chrome) | Baja (manual) | $0 | Bajo, a volumen bajo | Spot-checks, no producción |
| MercadoLibre | Opción D: extracción con Haiku | — | Centavos/1000 págs | — | Se combina con A/B/C |

## 5. Recomendación concreta de arranque

1. **InfoCasas — hecho:** `etl/extract/scrape_infocasas.py` reemplaza a `generate_synthetic_listings.py` para uso real. 7.540 avisos de alquiler de apartamentos en Montevideo en una corrida de ~7-8 min.
2. **MercadoLibre, primer paso:** que alguien con cuenta de ML registre una app developer y pruebe un request autenticado real a `/sites/MLU/search?category=MLU1472` — es la única forma de confirmar si el free tier alcanza o si hay que ir directo a un servicio pago. No asumir que la API gratis resuelve esto solo porque existe.
3. **Si el free tier no alcanza (escenario más probable dado lo que se vio hoy):** evaluar Zyte/Bright Data específicamente para MLU/inmuebles, o quedarse con pulls de bajo volumen vía browser real mientras no haya presupuesto para el servicio pago.

## 6. Compliance (bandera, no asesoría legal)

Antes de depender de esto para el negocio, revisar los Términos de Uso de ambos sitios — particularmente MercadoLibre, que suele restringir explícitamente el scraping automatizado de su marketplace en sus términos. La vía de API oficial es la más segura en términos de compliance por diseño. Esto es una bandera para que el equipo lo chequee con quien corresponda, no un análisis legal.

## 7. buscandocasa.com — research 2026-08-13, nivel tarjeta implementado

> Decisión tomada: **solo nivel tarjeta**, implementado en `etl/extract/scrape_buscandocasa.py` (ver §7.3). El nivel ficha de detalle (§7.4) queda documentado como extensión posible, sin implementar.

Portal inmobiliario uruguayo agregador (contrata con inmobiliarias, no dueño directo de los avisos). Sitio legado: ASP clásico sobre IIS/Windows, sin frontend moderno tipo Next.js/React.

### 7.1 Acceso (hechos, probados hoy con curl simple)

- **Sin `robots.txt`** (devuelve 404 de IIS) → sin restricciones declaradas a nivel de crawler.
- **HTML plano, sin JS necesario**: tanto las páginas de resultados como las fichas de detalle devuelven 200 con el contenido completo vía `curl` con user-agent de navegador. No hace falta browser real ni esperar renderizado.
- **Encoding ISO-8859-1**, no UTF-8 — hay que decodificar así explícitamente o se corrompen tildes/ñ/símbolos (`m²`, etc.).
- **Emails ofuscados** como entities HTML numéricas (`&#105;&#110;&#102;&#111;...`) en vez de texto plano — trivial de decodificar (`chr(int(n))` por cada entity) pero un parser ingenuo no los va a leer como email directamente.

### 7.2 Estructura de URLs

- Buscador: `buscandocasa.com/bc/*.asp` (páginas estáticas de navegación). La home trae un form gigante (`f1`, ~250 campos ocultos: precio min/max, m² min/max, dormitorios `d0`-`d5`, baños `b1`-`b4`, garage `g0`-`g4`, distancia al mar, zona, etc.) que arma la búsqueda real por JS y la manda por AJAX — no es un `<form action=...>` tradicional. El endpoint real se identificó leyendo `0/0_v092.js` (función `listado_propiedades` → `xmlhttpPost2`) y `0/js/ajaxsbmt_v05.js` (`getquerystring2`, serializa todo `f1`): **`POST https://www.buscandocasa.com/0/0_in{op}_v30.asp?fav=0`** (`op` = `a`/`v`/`t`), con el snapshot default de `f1` como body salvo `op`/`inmueble`/`nresultados`. Ver detalle completo en el docstring de `etl/extract/scrape_buscandocasa.py`.
- Vista usada solo para la exploración inicial (ya no se usa en el extractor real): `buscandocasa.com/bc/0_promocion.asp?promo=1/2` — "últimos ingresos", ~40 avisos por página, sin filtros. El endpoint AJAX de arriba reemplaza esto por completo.
- Cada aviso individual vive en un **subdominio corto propio**: `https://<ref>.ver.uy` (ej. `696APa1340.ver.uy`, `259apv0016.ver.uy`). Ojo: ese `ref` del subdominio (usado solo para el link público) **no es el mismo string** que el `ref` interno devuelto por el endpoint de búsqueda (ej. `207AP2027`, sin letra de operación embebida) — ver §7.3 sobre por qué esto importa para la unicidad de `listing_id`. Tipos de propiedad confirmados por los nombres de campos ocultos del form (`nmg_<TIPO><OP>`) y por el `<select name="inop">` de la home: `AP`=apartamento, `CA`=casa, `CH`=chacra, `CO`=comercio, `ES`=especial, `GA`=garage, `LO`=local, `OF`=oficina, `TE`=terreno. Operación: `A`=alquiler, `V`=venta, `T`=temporada (no todos los tipos tienen las 3 — `CO` y `ES` solo tienen venta).

### 7.3 Nivel tarjeta — **implementado 2026-08-13**, `etl/extract/scrape_buscandocasa.py`

Terminó siendo más simple y más completo que la exploración inicial con `0_promocion.asp` (que solo daba "últimos ingresos"). El endpoint real de búsqueda (§7.2) acepta un parámetro `nresultados` sin límite práctico — probado hasta 100.000 y el sitio devuelve el total real completo **en un solo request por combinación tipo×operación**, sin paginar ni particionar (a diferencia de InfoCasas y Casasweb). Hay 21 combinaciones válidas de tipo×operación (9 venta + 8 alquiler + 4 temporada).

**Volumen real medido:** **1.271 avisos activos en total** en las 21 combinaciones — sensiblemente menor a los "+20.000" publicitados en el sitio (probablemente un acumulado histórico, no inventario vivo). A este volumen, correr las 21 combinaciones toma segundos.

**Dos gotchas reales, encontrados y corregidos el mismo día** (el primero causaba pérdida de datos, ojo si se retoca el extractor):

1. **El `ref` interno no es único entre combinaciones.** Identifica la *unidad*, no la *oferta comercial* — la misma unidad puede estar publicada en más de una combinación a la vez (ej. un apartamento en alquiler anual Y en temporada, con precios distintos, mismo `ref`). La primera corrida deduplicaba por `ref` a nivel global entre las 21 combinaciones y perdía **~90% de los avisos de temporada** por esto. Fix: `listing_id = "BC" + op_letter + ref`, y el dedup (salvaguarda, no confirmado que haga falta) queda scopeado a cada combinación por separado, nunca global.
2. **Precio "CONSULTAR"/"RESERVADO" en temporada.** ~90% de los avisos de temporada no traen un monto fijo (depende de la semana). Se conserva la fila con `precio` vacío en vez de descartarla — mismo criterio que se usa para `dormitorios`/`metraje_m2` en tipos donde no aplican (terrenos, locales, oficinas, garajes, comercios no tienen dormitorios; se dejan vacíos, no se descarta la fila). Esto es una diferencia deliberada respecto a InfoCasas, que sí descarta avisos sin dormitorios/precio — ahí tiene sentido porque su scope es un solo tipo de propiedad; acá el pedido fue explícitamente "todos los listings que se pueda" a través de todos los tipos.

**Cobertura de la corrida real:** 1.271/1.271 filas (100%, sin filas perdidas por parseo) — `barrio` presente en el 100% de las filas, `dormitorios` vacío en 318 filas (tipos sin ese concepto) y `precio` vacío en 242 filas (temporada con "CONSULTAR"). Escribe a `data/raw/listings_buscandocasa.csv`, mismo contrato de columnas que InfoCasas/Casasweb.

**Nota de datos, no de código:** un aviso (`029AP334 Valdi`, de la inmobiliaria "URUGUARGEN") tiene un espacio literal dentro de su `ref` — parece un error de carga de esa inmobiliaria en el sitio, no algo que el extractor deba "arreglar" (se conserva tal cual, es un identificador válido igual).

### 7.4 Extensión futura (no implementada) — ficha de detalle completa

Cada ficha (`<ref>.ver.uy`) expone un esquema mucho más rico que la tarjeta: ~70-80 campos organizados en secciones fijas — UBICACIÓN; operación (VENTA con precio/financiación/permuta, o ALQUILER con mensualidad/gastos comunes/garantías aceptadas); DETALLES (superficie edificada, piso, año de construcción, conservación); ESTACIONAMIENTO; DORMITORIOS; BAÑOS; INTERIOR; EXTERIOR; EQUIPAMIENTO; SERVICIOS Y ACCESORIOS; COMODIDADES DEL EDIFICIO; ÁREAS RECREATIVAS; COMENTARIOS (descripción libre) — más datos de la inmobiliaria (nombre, RUT, teléfonos, email, contacto).

**Gotcha técnico si se implementa:** los amenities (piscina, ascensor, wifi, etc.) *no* se pueden extraer por presencia del texto del label — el label se renderiza siempre, esté marcado o no. El estado real vive en la clase CSS del contenedor: `<div class="ficha_item">` (marcado) vs `<div class="ficha_item_n">` (no marcado), con `<img src="ok.svg">` / `<img src="no.svg">` adentro. Un parser tiene que matchear ese patrón (clase + ícono), no el texto.

Ejemplos ya extraídos y validados hoy como prueba de concepto (no versionados en el repo): una ficha de alquiler (`696APa1340`, 70 campos) y una de venta (`259apv0016`, 72 campos), parseadas a JSON completo.

**Cuándo valdría la pena:** si se necesita alimentar con datos reales los catálogos hardcodeados de `data-model.md §4` (amenities, garantías aceptadas), o para enriquecer avisos puntuales. Antes de invertir en esto conviene revisar cuánto overlap hay con lo que InfoCasas ya da en su JSON embebido (§2) — puede que no haga falta una fuente nueva para esos mismos campos.

### 7.5 Compliance

Mismo flag que en §6: sin `robots.txt` no hay señal explícita de restricción, pero no se revisaron los Términos de Uso del sitio hoy — pendiente antes de depender de esto en producción.
