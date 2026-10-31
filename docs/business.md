# Cuadra — Business Overview

> Fuente: `Proyecto Final UXUI/Informe Final UXUI.pdf`, `Manual Identidad Cuadra.html`, `src/data/screens.js` y los 20 flujos del prototipo clickeable (`cuadra/src/screens/`). Este documento consolida esa investigación para que sirva de contexto de negocio al diseñar la data layer. No inventa datos de negocio (pricing, revenue) que no estén en el material original — donde falta información se marca explícitamente como **pendiente de definir**.

## 1. Qué es Cuadra

Cuadra es una plataforma web de alquileres residenciales en Uruguay (foco inicial: Montevideo) que centraliza avisos de alquiler y los **contextualiza con datos de mercado**. No es solo un portal de listados: su diferencial es mostrar, para cada aviso, si el precio está por debajo, dentro o por encima de la mediana del mercado para esa zona y tipo de apartamento, con evolución histórica de precios.

**Tagline:** "El precio justo, cuadra por cuadra."
**Promesa:** Mostrarte el precio del mercado, sin vueltas.
**Propósito:** Que comprar, alquilar o publicar una propiedad sea una decisión con datos, no con corazonadas.

## 2. El problema (por qué existe Cuadra)

El informe identifica tres problemas estructurales del mercado de alquileres uruguayo, sin resolver hoy por portales existentes (Gallito, Infocasas, MercadoLibre):

1. **Precios sin contexto.** Los portales muestran listados pero ningún indica si un precio es alto, bajo o acorde a mercado para esa zona. El usuario debe llamar y comparar manualmente para saberlo.
2. **Búsqueda atada a límites administrativos.** Filtrar por "barrio" no refleja cómo la gente piensa dónde quiere vivir (ej. radio caminable desde el trabajo o la escuela de los hijos, cruzando límites de barrio).
3. **Sin histórico de precios.** No hay forma de saber si los precios subieron en una zona en el último año, lo que impide negociar con datos o decidir el timing de una decisión.

**Implicación directa para la data layer:** el producto vive o muere por la calidad y cobertura de los datos de mercado (agregaciones por zona/tipo/tamaño + series históricas). Ver `market-intelligence.md`.

## 3. Grupos de usuarios y proto-personajes

Cuadra atiende **tres grupos de usuario con necesidades distintas**, cada uno con su propio flujo completo en el prototipo. Esto importa para la data layer porque cada grupo necesita entidades y permisos distintos (ver `data-model.md`).

### Grupo 1 — Inquilino buscador
**Persona:** Matías Fernández, 27 años, diseñador gráfico, Montevideo Centro.
**Frase:** "Quiero saber si el precio es justo antes de llamar. No quiero negociar a ciegas."

- **Contexto:** busca su primer apartamento solo, sin experiencia negociando alquileres, se siente en desventaja frente a propietarios que conocen mejor el mercado.
- **Comportamiento:** busca desde el celular (incluso en el transporte), guarda avisos que le interesan, los compara entre sí, y solo llama cuando el apartamento le convence del todo (cada llamada lo compromete).
- **Frustraciones:** no sabe si un precio es caro o barato sin llamar primero; los barrios administrativos no coinciden con la zona real donde quiere vivir; la info de los avisos suele ser incompleta o desactualizada.
- **Metas:** encontrar un apartamento bien ubicado dentro de su presupuesto; cerrar el alquiler sin sentir que pagó de más.
- **Tareas principales:** buscar por zona cerca de su trabajo; comparar el precio del aviso con el mercado de esa zona; contactar al propietario con información de respaldo; coordinar visita.
- **No requiere cuenta** para buscar ni ver detalle de avisos — la sesión se pide recién al tocar "Contactar al propietario".

### Grupo 2 — Propietario particular
**Persona:** Carmen Suárez, 58 años, jubilada, Pocitos.
**Frase:** "Quiero poner un precio justo, ni regalarlo ni espantar a los candidatos."

- **Contexto:** heredó un apartamento de 1 dormitorio en Pocitos que le complementa la jubilación. Tuvo una mala experiencia con una inmobiliaria (honorarios altos, malos inquilinos) y ahora quiere gestionar el alquiler directamente, pero no sabe por dónde empezar.
- **Comportamiento:** usa computadora en casa, navega despacio, prefiere interfaces simples. Mira otros avisos en distintos portales para tener referencia de precio, pero no tiene forma de sistematizarlo.
- **Frustraciones:** no tiene referencia objetiva de precios para su zona/tipo de apartamento; le cuesta filtrar candidatos sin intermediario; las comisiones de inmobiliarias le parecen desproporcionadas.
- **Metas:** publicar y recibir consultas de personas serias; fijar un precio competitivo con respaldo de datos reales.
- **Tareas principales:** consultar el precio de mercado para su zona/tipo antes de publicar; publicar el aviso (wizard de 4 pasos: ubicación → características → precio → fotos); recibir y responder consultas de inquilinos.

### Grupo 3 — Corredor inmobiliario
**Persona:** Diego Acosta, 42 años, corredor matriculado independiente, Montevideo.
**Frase:** "Necesito mostrarle al propietario con datos por qué su apartamento vale lo que vale, no solo mi opinión."

- **Contexto:** gestiona una cartera de **20 propiedades** de terceros en distintos barrios. Su mayor dificultad no es conseguir clientes sino negociar con propietarios que sobrevaloran sus apartamentos. Pierde tiempo buscando referencias de precio manualmente en varios portales.
- **Comportamiento:** usa herramientas digitales con soltura, trabaja desde PC y celular indistintamente. Valora rapidez y precisión sobre estética. Presenta datos a sus clientes como argumento técnico.
- **Frustraciones:** no tiene herramientas para argumentar valuaciones con datos objetivos; pierde horas buscando referencias manualmente; los propietarios rechazan sus valuaciones por falta de respaldo verificable.
- **Metas:** tener autoridad técnica frente al propietario con datos de mercado; reducir el tiempo de negociación usando referencias objetivas.
- **Tareas principales:** analizar precios de mercado por zona/tipo de apartamento; consultar evolución histórica en zonas de su cartera; **generar informes PDF exportables y personalizables** para justificar valuaciones ante el propietario; gestionar avisos de varios propietarios (que no son necesariamente usuarios de la plataforma) desde un mismo perfil.

**Importante para el modelo de datos:** el corredor administra propiedades cuyo *dueño real* es un tercero identificado solo por nombre (ej. "C. Suárez", "J. Methol", "R. Píriz" en la pantalla "Mi cartera"). Ese dueño no es necesariamente una cuenta de usuario — es un dato de contacto asociado a la propiedad, distinto del usuario que publica (el corredor). Ver `data-model.md §Owner vs. Publisher`.

## 4. Propuesta de valor por segmento

| Segmento | Problema que resuelve Cuadra | Feature ancla |
|---|---|---|
| Inquilino | "¿Este precio es justo?" sin tener que llamar y comparar manualmente | Chip + barra "vs. mercado" en cada aviso, visible sin cuenta |
| Propietario | "¿Cuánto cobro?" sin depender de una inmobiliaria | Consulta de mercado *antes* de publicar (mínimo/mediana/máximo + tendencia 12 meses) |
| Corredor | "¿Cómo justifico mi valuación?" frente a un propietario que la cuestiona | Dashboard de análisis por zona + informe PDF exportable con datos verificables |

## 5. Competencia y posicionamiento

Portales existentes en Uruguay: **Gallito, Infocasas, MercadoLibre Inmuebles**. Todos publican avisos pero **ninguno** combina:
- búsqueda libre en el mapa (polígono dibujado a mano o radio desde un punto), ignorando límites administrativos de barrio, y
- inteligencia de precios (mediana/rango/histórico) en el segmento de alquileres residenciales.

Ese cruce (búsqueda geográfica libre + contexto de mercado) es el diferencial competitivo declarado del proyecto.

## 6. Identidad de marca

- **Nombre:** *cuadra* (minúscula en el uso tipográfico). En Uruguay, una cuadra es la unidad básica de la ciudad — la distancia entre dos esquinas, la medida con la que la gente describe dónde vive. Conecta directamente con la búsqueda por zona.
- **Símbolo:** una manzana — cuadrado redondeado exterior (la cuadra) + cuadrado interior en clay (la propiedad).
- **Personalidad:**
  - *Transparente* — muestra el precio real del mercado, incluso cuando incomoda; sin letra chica.
  - *Cercana* — conoce el barrio y la cuadra; habla como un vecino que sabe, no como una inmobiliaria.
  - *Precisa* — datos, rangos y medianas; cada cifra tiene de dónde agarrarse.
  - *Cálida* — detrás de cada búsqueda hay alguien buscando un hogar.
- **Paleta:** Clay `#bd5d3a` (acción primaria), Tinta `#211f1c` (texto), Papel `#f4f1ea` (fondo); semánticos de mercado: por debajo `#2f7d57` (verde), en rango `#3f6ea3` (azul), por encima `#c23b2e` (rojo). Estos tres estados semánticos son un concepto de negocio, no solo visual — son el output directo del motor de comparación de precios y deben modelarse como tal en la data layer (ver `market-intelligence.md`).

## 7. Estado actual del proyecto

- Proyecto académico (Facultad de Ingeniería, UX/UI) de 3 personas, entregado 24/06/2026.
- Existe: investigación de usuarios, wireframes de baja fidelidad, prototipo de alta fidelidad, **prototipo clickeable en React sin backend** (datos hardcodeados en `src/data/`, navegación simulada, sin persistencia real).
- **No existe todavía:** backend, base de datos, autenticación real, fuente de datos de mercado real, lógica de agregación de precios, geocoding real, generación real de PDFs.
- Este set de documentos (`business.md`, `roadmap.md`, `data-model.md`, `market-intelligence.md`) es el punto de partida para diseñar esa capa de datos.

## 8. Preguntas de negocio abiertas (no definidas en el material fuente)

El informe es un proyecto de diseño UX/UI; **no define modelo de monetización, ni métricas de negocio, ni estrategia de adquisición de datos de mercado reales.** Antes de construir la data layer conviene que el equipo decida:

1. **Modelo de monetización:** ¿gratis para inquilinos y propietarios, con informes PDF pagos para corredores? ¿Avisos destacados? ¿Suscripción para corredores con cartera grande? ¿Comisión por contacto/lead? (ninguna opción está confirmada — son solo las alternativas típicas del rubro, a validar).
2. **Origen de los datos de mercado:** ¿los avisos publicados en Cuadra alcanzan volumen propio para calcular medianas confiables por zona, o se necesita ingerir datos externos (scraping de Gallito/Infocasas/MercadoLibre, compra de datos, alianza con corredores) para el arranque en frío? Ver `market-intelligence.md §Estrategia de datos`.
3. **Cobertura geográfica inicial:** el prototipo usa barrios de Montevideo; ¿el MVP se limita a Montevideo o contempla interior del país desde el día uno?
4. **Verificación de identidad/propiedad:** ¿se valida que quien publica es realmente el propietario o un corredor matriculado? El informe no lo aborda, pero es relevante para confianza y para separar "corredor" de "propietario particular" como rol formal (hoy es implícito por comportamiento, no un campo de rol explícito en las pantallas de auth).
