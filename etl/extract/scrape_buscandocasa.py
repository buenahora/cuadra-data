"""
Extract stage — extractor real de BuscandoCasa.com (tercera fuente, además
de InfoCasas y Casasweb; ver docs/scraping-strategy.md §7 para el research
completo y la decisión explícita de quedarse en nivel tarjeta por ahora,
sin ir a la ficha de detalle por propiedad).

Escribe data/raw/listings_buscandocasa.csv. Mismo contrato de columnas que
InfoCasas/Casasweb (listing_id, barrio, dormitorios, metraje_m2, precio,
estado, publicado_en) -- listing_id prefijado "BC" + ref propio del sitio,
mismo espíritu que "IC"/"CW". No pisa listings.csv de InfoCasas ni los
archivos de Casasweb.

Cómo funciona (research del mismo día en docs/scraping-strategy.md §7): a
diferencia de la vista "0_promocion.asp" usada en la exploración inicial
(solo "últimos ingresos", ~40 avisos por página, sin filtros reales), el
buscador real del sitio expone un endpoint AJAX por combinación
tipo+operación:

    POST https://www.buscandocasa.com/0/0_in{op}_v30.asp?fav=0

(op = 'a'/'v'/'t' -- alquiler/venta/temporada), con el form gigante de
filtros ("f1", ~250 campos ocultos) serializado en el body. Reverseado
leyendo 0/0_v092.js (función listado_propiedades -> xmlhttpPost2) y
0/js/ajaxsbmt_v05.js (getquerystring2, que serializa TODOS los campos de
f1, incluyendo checkboxes/radios marcados). fetch_form_defaults() lee la
home una vez por corrida para capturar el snapshot default de esos ~250
campos (sin ningún filtro de zona/precio/dormitorios aplicado) y lo usa
como base del POST, pisando solo `op`/`inmueble`/`inmueble_fav`/
`nresultados` por combinación.

Sin filtros de zona/precio, el propio parámetro `nresultados` del form
controla cuántos avisos devuelve el endpoint en una sola llamada --
probado hasta 100.000 y el sitio devuelve el total real completo sin
truncar (confirmado: total reportado por la respuesta == cantidad real de
tarjetas parseadas, en las 21 combinaciones tipo×operación). Esto significa
que, a diferencia de InfoCasas (paginado obligatorio, orden que se
reordena solo en vivo) y Casasweb (tope duro de 50 avisos por combinación,
requiere particionar por zona/dormitorios/baños), BuscandoCasa se cubre
completo con **un solo request por combinación tipo×operación** -- no hace
falta paginar ni particionar. fetch_combo() igual loguea una advertencia
si algún día el total reportado y la cantidad de tarjetas parseadas
dejaran de coincidir (mismo espíritu que el chequeo de cobertura de
scrape_infocasas.py), en vez de asumir en silencio que sigue completo.

21 combinaciones válidas de tipo×operación (ver <select name="inop"> en la
home, VALID_COMBOS abajo): 9 tipos en venta, 8 en alquiler, 4 en temporada
-- no todos los tipos existen en las 3 operaciones. Volumen real medido el
2026-08-13: ~1.270 avisos activos en total en las 21 combinaciones --
sensiblemente menor a los "+20.000" publicitados en el sitio (con ese
volumen, probablemente un acumulado histórico y no el inventario vivo
actual). A esta escala no hace falta ninguna estrategia de rotación de
UA/proxies -- las 21 combinaciones corren en segundos.

Moneda: los precios vienen mezclados en pesos uruguayos ($) o dólares
(US$) dentro de la misma combinación tipo+operación (a diferencia de
InfoCasas, que trae el campo de moneda tipado). Se usa el tipo de cambio
propio del sitio (variable JS `cambiodolar` embebida en la home, ej.
40.35) para normalizar todo a pesos uruguayos, mismo criterio que
scrape_infocasas.py.

Estado: igual que Casasweb, el endpoint de búsqueda no expone un flag de
activo/pausado explícito -- como son resultados vivos del propio buscador
del sitio, se marca "activo" para todas las filas.

Dormitorios/m²: la tabla de "detalle" de cada tarjeta tiene un layout
DISTINTO según el tipo de propiedad (apartamentos/casas: dormitorios +
baños + m²; terrenos: m² + frente + precio/m²; otros tipos con variantes
propias -- no se relevó cada uno en detalle). parse_cards() solo completa
`dormitorios` cuando el bloque de detalle menciona "dorm." explícitamente
-- para tipos sin dormitorios (terrenos, locales, oficinas, garajes,
comercios) la columna queda vacía en vez de descartar la fila entera, a
diferencia de InfoCasas que sí descarta avisos sin dormitorios (tiene
sentido ahí porque su scope es solo apartamentos; acá el scope pedido es
"todos los listings que se pueda" across todos los tipos).

`ref` no es único entre combinaciones -- gotcha real encontrado y corregido
el mismo día: el `ref` interno (primer argumento de `cargar_ficha(...)` en
el HTML de resultados) identifica la UNIDAD, no la oferta comercial -- la
misma unidad puede estar publicada simultáneamente en más de una
combinación tipo+operación (ej. un apartamento en alquiler anual Y en
temporada a la vez, con precios distintos, mismo `ref`). La primera
versión de este extractor deduplicaba por `ref` a nivel global entre las
21 combinaciones y perdía ~90% de los avisos de temporada por esto (se
detectó comparando refs de "apa" contra "apt": refs compartidos con
precios distintos en cada combinación). Por eso `listing_id` combina el
op letter (a/v/t) + `ref` -- son ofertas distintas, no duplicados -- y el
dedup real (por si dentro de una misma combinación el sitio repite algún
`ref`, no confirmado que pase pero es una salvaguarda barata) se hace
scopeado a cada combinación por separado, nunca global.

Precio "CONSULTAR"/"RESERVADO": los avisos de temporada casi nunca traen
un monto fijo (medido: ~90% de "apt"/"cat" muestran "CONSULTAR", el resto
"RESERVADO" o un monto en USD puntual) -- tiene sentido de negocio, el
precio de temporada depende de la semana/temporada. `normalize_price()`
devuelve `None` en esos casos y la fila se conserva igual con `precio`
vacío, no se descarta -- mismo criterio que dormitorios/m² vacíos arriba.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
import time
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path

import requests

DATA_SOURCE = "buscandocasa"
BASE = "https://www.buscandocasa.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
DEFAULT_DELAY_SECONDS = 1.0
# La ficha de detalle es un request por aviso (~1.270 avisos activos hoy,
# ver docs/scraping-strategy.md §7.4) en vez de uno por combinación -- delay
# más corto que DEFAULT_DELAY_SECONDS para que una corrida completa siga
# siendo razonable (~10 min), sin dejar de ser respetuoso.
DEFAULT_DETALLE_DELAY_SECONDS = 0.5
DEFAULT_FALLBACK_UYU_PER_USD = 40.0  # solo si no se puede leer 'cambiodolar' de la home

PROPERTY_TYPES = {
    "ap": "apartamento",
    "ca": "casa",
    "te": "terreno",
    "lo": "local",
    "of": "oficina",
    "ch": "chacra",
    "co": "comercio",
    "ga": "garage",
    "es": "especial",
}
OPERATIONS = {"v": "venta", "a": "alquiler", "t": "temporada"}

# Confirmado contra <select name="inop"> de la home (2026-08-13). No todos
# los tipos existen en las 3 operaciones (ej. comercios/especiales solo
# tienen venta).
VALID_COMBOS = [
    "apv", "cav", "tev", "lov", "ofv", "chv", "cov", "gav", "esv",
    "apa", "caa", "tea", "loa", "ofa", "cha", "gaa", "esa",
    "apt", "cat", "cht", "lot",
]

CARD_SPLIT_RE = re.compile(r'(?=<div id="div_[^"]*" class="div_resultados)')
REF_RE = re.compile(r"cargar_ficha\('([^']+)'")
# cargar_ficha(ref, tienefotos, idsuc, favo) -- los 3 primeros son justo los
# parámetros que pide 0/0_ficha_v04.asp (ver fetch_ficha_detalle), reverseado
# el mismo día leyendo 0/0_v092.js (xmlHttp.open del propio cargar_ficha).
CARGAR_FICHA_RE = re.compile(r"cargar_ficha\('([^']+)','([^']*)',(\d+)")
PRICE_RE = re.compile(r'class="precio"[^>]*>(.*?)</div>', re.S)
LUGAR_RE = re.compile(r"<b><b>(.*?)</b>\s*</b>", re.S)
DETALLE_RE = re.compile(r'<table width="100%"[^>]*>.*?</table>', re.S)
FECHA_RE = re.compile(r"(\d{2}/\d{2}/\d{4})")
M2_RE = re.compile(r"([\d.]+)\s*m(?:&sup2;|²)", re.I)
PRICE_AMOUNT_RE = re.compile(r"([\d.]+)")
# La celda de foto de la tarjeta trae portada + cantidad total (galería
# completa vive en la ficha, no implementada -- ver docstring del módulo).
FOTO_SRC_RE = re.compile(r'src="(/fotos/[^"]+)"')
FOTO_COUNT_RE = re.compile(r'title="(\d+)\s*fotos?"', re.I)


def _attr(tag: str, name: str) -> str | None:
    m = re.search(name + r'\s*=\s*"([^"]*)"', tag, flags=re.I)
    if m:
        return html.unescape(m.group(1))
    m = re.search(name + r"\s*=\s*([^\s>]+)", tag, flags=re.I)
    if m:
        return html.unescape(m.group(1))
    return None


def _strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", s)).replace("\xa0", " ").strip()


def fetch_form_defaults(session: requests.Session) -> tuple[dict, float | None]:
    """Lee la home y extrae el snapshot default de todos los campos del
    form de búsqueda "f1" (checkboxes/radios en su estado inicial, selects
    en su opción seleccionada, resto de hidden fields con su value), más
    el tipo de cambio USD/UYU embebido (`cambiodolar`)."""
    r = session.get(BASE + "/", timeout=30)
    r.raise_for_status()
    r.encoding = "iso-8859-1"
    data = r.text

    idx1 = data.find('<form name="f1"')
    idx2 = data.find("</form>", idx1)
    form = data[idx1:idx2]

    defaults: dict[str, str] = {}
    for tag in re.findall(r"<input\b[^>]*>", form, flags=re.I):
        name = _attr(tag, "name")
        if not name:
            continue
        typ = (_attr(tag, "type") or "text").lower()
        if typ in ("submit", "button", "reset", "image", "file"):
            continue
        if typ in ("checkbox", "radio"):
            checked = re.search(r"\bchecked\b", tag, flags=re.I) is not None
            if checked:
                defaults[name] = _attr(tag, "value") or "on"
            else:
                defaults.setdefault(name, "")
        else:
            defaults[name] = _attr(tag, "value") or ""

    for sel in re.finditer(
        r'<select\b[^>]*name="([^"]+)"[^>]*>(.*?)</select>', form, flags=re.I | re.S
    ):
        name, body = sel.group(1), sel.group(2)
        m = re.search(r'<option\b[^>]*value="([^"]*)"[^>]*selected', body, flags=re.I)
        if not m:
            m = re.search(r'<option\b[^>]*selected[^>]*value="([^"]*)"', body, flags=re.I)
        if not m:
            m = re.search(r'<option\b[^>]*value="([^"]*)"', body, flags=re.I)
        defaults[name] = html.unescape(m.group(1)) if m else ""

    m_rate = re.search(r"cambiodolar\s*=\s*([\d.]+)", data)
    rate = float(m_rate.group(1)) if m_rate else None

    return defaults, rate


def parse_cards(html_fragment: str) -> list[dict]:
    parts = CARD_SPLIT_RE.split(html_fragment)
    cards = [c for c in parts if c.strip().startswith('<div id="div_')]
    out = []
    for c in cards:
        m_ref = REF_RE.search(c)
        if not m_ref:
            continue
        ref = m_ref.group(1)

        m_cf = CARGAR_FICHA_RE.search(c)
        ficha_ft = m_cf.group(2) if m_cf else ""
        ficha_idsuc = m_cf.group(3) if m_cf else ""

        m_price = PRICE_RE.search(c)
        precio_txt = _strip_tags(m_price.group(1)) if m_price else ""

        m_lugar = LUGAR_RE.search(c)
        ciudad_barrio = _strip_tags(m_lugar.group(1)) if m_lugar else ""
        barrio = (
            ciudad_barrio.split(" / ")[-1].strip()
            if " / " in ciudad_barrio
            else ciudad_barrio.strip()
        )

        m_det = DETALLE_RE.search(c)
        detalle_txt = _strip_tags(m_det.group(0)) if m_det else ""

        m_fecha = FECHA_RE.search(c)
        fecha = m_fecha.group(1) if m_fecha else ""

        m_m2 = M2_RE.search(detalle_txt)
        m2 = m_m2.group(1).replace(".", "") if m_m2 else ""

        dormitorios = ""
        if "dorm" in detalle_txt.lower():
            m_dorm = re.match(r"\s*(\d+)", detalle_txt)
            if m_dorm:
                dormitorios = m_dorm.group(1)

        m_foto = FOTO_SRC_RE.search(c)
        foto_url = BASE + m_foto.group(1) if m_foto else ""
        m_foto_count = FOTO_COUNT_RE.search(c)
        foto_count = m_foto_count.group(1) if m_foto_count else ""

        out.append(
            {
                "ref": ref,
                "ficha_ft": ficha_ft,
                "ficha_idsuc": ficha_idsuc,
                "precio_txt": precio_txt,
                "barrio": barrio,
                "dormitorios": dormitorios,
                "m2": m2,
                "fecha": fecha,
                "foto_url": foto_url,
                "foto_count": foto_count,
            }
        )
    return out


def normalize_price(precio_txt: str, uyu_per_usd: float) -> int | None:
    is_usd = "US$" in precio_txt
    cleaned = precio_txt.replace("US$", "").replace("$", "")
    m = PRICE_AMOUNT_RE.search(cleaned)
    if not m:
        return None
    amount = float(m.group(1).replace(".", ""))
    if is_usd:
        return round(amount * uyu_per_usd)
    return round(amount)


def fecha_iso(fecha_ddmmyyyy: str) -> str:
    if not fecha_ddmmyyyy:
        return ""
    d, m, y = fecha_ddmmyyyy.split("/")
    return f"{y}-{m}-{d}"


def fetch_combo(
    session: requests.Session,
    combo: str,
    defaults: dict,
    nresultados: int = 100_000,
    delay: float = DEFAULT_DELAY_SECONDS,
) -> list[dict]:
    tipo, op = combo[:2], combo[2]
    body = dict(defaults)
    body["op"] = op
    body["inmueble"] = tipo
    body["inmueble_fav"] = tipo.upper()
    body["nresultados"] = str(nresultados)

    url = f"{BASE}/0/0_in{op}_v30.asp?fav=0"
    r = session.post(url, data=body, timeout=60)
    r.raise_for_status()
    r.encoding = "iso-8859-1"

    parts = r.text.split("||")
    total_reportado = int(parts[0]) if parts and parts[0].strip().isdigit() else None
    html_fragment = parts[1] if len(parts) > 1 else ""
    cards = parse_cards(html_fragment)

    if total_reportado is not None and total_reportado != len(cards):
        print(
            f"  ADVERTENCIA {combo}: total reportado por el sitio={total_reportado} "
            f"vs tarjetas parseadas={len(cards)} -- revisar parse_cards()",
            file=sys.stderr,
        )

    time.sleep(delay)
    return cards


SECTION_TITLE_RE = re.compile(r'<div class="ficha_titulo">(.*?)</div>', re.S)
FREE_TEXT_RE = re.compile(r'<div class="ficha_item_0"[^>]*>(.*?)</div>', re.S)
ITEM_PAIR_RE = re.compile(
    r'<div class="(ficha_item|ficha_item_n)">'
    r'<div class="ficha_item_1">(.*?)</div>'
    r'<div class="ficha_item_2">(.*?)</div>'
    r"</div>",
    re.S,
)


def parse_ficha(html_text: str) -> dict:
    """Parsea la ficha de detalle completa a un dict {sección: contenido} --
    ver docs/scraping-strategy.md §7.4 para el research del layout (~70-80
    campos en secciones fijas: UBICACIÓN, ALQUILER/VENTA, DETALLES,
    DORMITORIOS, BAÑOS, EQUIPAMIENTO, COMENTARIOS, etc.).

    Cada sección es una de dos formas (confirmado hoy contra fichas reales):
      - Texto libre (UBICACIÓN, COMENTARIOS): un solo <div class="ficha_item_0">.
      - Lista de campos (el resto): pares <div class="ficha_item_1">label</div>
        <div class="ficha_item_2">valor</div>, envueltos en <div
        class="ficha_item"> (marcado/con valor) o <div class="ficha_item_n">
        (no marcado -- para amenities booleanos, "valor" es un ok.svg/no.svg
        en vez de texto, por eso "marcado" es su propio campo en vez de
        inferirlo del ícono). Gotcha documentado: el label se renderiza
        siempre esté marcado o no -- no se puede usar el texto del label
        para saber si aplica, hay que mirar la clase del contenedor.
    """
    titles = list(SECTION_TITLE_RE.finditer(html_text))
    result: dict = {}
    for i, m in enumerate(titles):
        title = _strip_tags(m.group(1))
        start = m.end()
        end = titles[i + 1].start() if i + 1 < len(titles) else len(html_text)
        block = html_text[start:end]

        pairs = ITEM_PAIR_RE.findall(block)
        if pairs:
            items = []
            for cls, label, value_html in pairs:
                marcado = cls == "ficha_item"
                has_icon = "sino" in value_html or ".svg" in value_html
                items.append(
                    {
                        "label": _strip_tags(label),
                        "value": None if has_icon else _strip_tags(value_html),
                        "marcado": marcado,
                    }
                )
            result[title] = items
            continue

        m_free = FREE_TEXT_RE.search(block)
        if m_free:
            result[title] = _strip_tags(m_free.group(1))

    return result


def descripcion_de_ficha(ficha: dict) -> str:
    return ficha.get("COMENTARIOS") or ""


def banos_de_ficha(ficha: dict) -> str:
    for item in ficha.get("BAÑOS") or []:
        if item["label"].lower().startswith("baños completos") and item["value"]:
            return item["value"]
    return ""


def fetch_ficha_detalle(
    session: requests.Session, ref: str, op: str, ft: str, idsuc: str, delay: float
) -> dict | None:
    """GET a 0/0_ficha_v04.asp -- mismo endpoint AJAX que carga la ficha al
    hacer click en un resultado (ver cargar_ficha() en 0/0_v092.js). ref/ft/
    idsuc salen de la propia tarjeta (parse_cards) -- no hay que reconstruir
    la URL pública .ver.uy, cuyo slug no es el mismo string que el ref
    interno (ver docstring del módulo)."""
    params = {
        "esmovil": 0,
        "favo": 0,
        "idi": "e",
        "contar": 1,
        "pantalla": 1,
        "pantalla2": 1,
        "pos": 0,
        "op": op,
        "id": "000",
        "idinmobiliaria": "",
        "ref": ref,
        "ft": ft,
        "idsuc": idsuc,
    }
    try:
        resp = session.get(f"{BASE}/0/0_ficha_v04.asp", params=params, timeout=20)
    except requests.exceptions.RequestException as e:
        print(f"  !! error de red en ficha {ref}: {e}")
        return None
    if resp.status_code != 200:
        print(f"  !! HTTP {resp.status_code} en ficha {ref}")
        return None
    resp.encoding = "iso-8859-1"
    time.sleep(delay)
    return parse_ficha(resp.text)


def check_robots(session: requests.Session) -> bool:
    """robots.txt de buscandocasa.com devuelve 404 (confirmado 2026-08-13)
    -- RobotFileParser trata esto como "sin reglas", can_fetch() da True.
    Se repite en runtime por si el sitio agrega reglas más adelante."""
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(BASE + "/robots.txt")
    try:
        rp.read()
    except Exception:
        return True
    return rp.can_fetch(USER_AGENT, BASE + "/0/0_ina_v30.asp")


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "listing_id",
        "barrio",
        "dormitorios",
        "banos",
        "metraje_m2",
        "precio",
        "estado",
        "publicado_en",
        "descripcion",
        "fotos",
        "foto_count",
        "fuente_datos",
        "operacion",
        "tipo_propiedad",
        "ingestado_en",
        "datos_crudos",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows -> {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="data/raw/listings_buscandocasa.csv")
    parser.add_argument("--nresultados", type=int, default=100_000)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS)
    parser.add_argument(
        "--combos",
        nargs="*",
        default=VALID_COMBOS,
        help="subset de combinaciones tipo+operación a scrapear (default: las 21, ej. --combos apv apa)",
    )
    parser.add_argument(
        "--skip-detalle",
        action="store_true",
        help="no pedir la ficha de detalle por aviso (descripcion/banos/datos_crudos quedan vacíos) -- "
        "corrida más rápida, solo nivel tarjeta.",
    )
    parser.add_argument(
        "--detalle-delay",
        type=float,
        default=DEFAULT_DETALLE_DELAY_SECONDS,
        help="segundos entre requests de ficha de detalle (un request por aviso)",
    )
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": BASE + "/",
            "Content-Type": "application/x-www-form-urlencoded",
        }
    )

    if not check_robots(session):
        print("robots.txt bloquea la ruta de búsqueda -- abortando.", file=sys.stderr)
        sys.exit(1)

    defaults, rate = fetch_form_defaults(session)
    if not rate:
        rate = DEFAULT_FALLBACK_UYU_PER_USD
        print(f"no se pudo leer 'cambiodolar' de la home, usando fallback {rate}", file=sys.stderr)
    else:
        print(f"tipo de cambio del sitio (cambiodolar): {rate} UYU/USD")

    ingestado_en = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []
    for combo in args.combos:
        cards = fetch_combo(session, combo, defaults, nresultados=args.nresultados, delay=args.delay)
        tipo_label = PROPERTY_TYPES[combo[:2]]
        op_label = OPERATIONS[combo[2]]

        # Dedup solo DENTRO de la combinación (salvaguarda -- no se observaron
        # refs repetidos dentro de una misma combinación en el research, pero
        # tampoco cuesta nada chequear). OJO: el mismo `ref` puede repetirse
        # LEGÍTIMAMENTE entre combinaciones distintas -- confirmado empíricamente
        # (ej. una misma unidad publicada en alquiler anual y también en
        # temporada, con precios distintos, mismo ref). Por eso listing_id lleva
        # el op letter (a/v/t) además del ref: son ofertas comerciales distintas,
        # no duplicados del mismo aviso, y deduplicarlas a nivel global perdía
        # ~90% de los avisos de temporada en la primera corrida.
        seen_refs_combo: set[str] = set()
        n_new = 0
        for c in cards:
            if c["ref"] in seen_refs_combo:
                continue
            seen_refs_combo.add(c["ref"])

            # Precio: los avisos de temporada muy seguido publican "CONSULTAR"
            # o "RESERVADO" en vez de un monto fijo (confirmado: ~90% de los
            # avisos de temporada). No es un error de parseo -- se deja
            # `precio` vacío en vez de descartar la fila, mismo criterio que
            # dormitorios/metraje_m2 para tipos donde no aplican.
            precio = normalize_price(c["precio_txt"], rate)

            descripcion, banos, datos_crudos = "", "", ""
            if not args.skip_detalle:
                ficha = fetch_ficha_detalle(
                    session, c["ref"], combo[2], c["ficha_ft"], c["ficha_idsuc"], args.detalle_delay
                )
                if ficha is not None:
                    descripcion = descripcion_de_ficha(ficha)
                    banos = banos_de_ficha(ficha)
                    datos_crudos = json.dumps(ficha, ensure_ascii=False)

            rows.append(
                {
                    "listing_id": f"BC{combo[2]}{c['ref']}",
                    "barrio": c["barrio"],
                    "dormitorios": c["dormitorios"],
                    "banos": banos,
                    "metraje_m2": c["m2"],
                    "precio": precio if precio is not None else "",
                    "estado": "activo",
                    "publicado_en": fecha_iso(c["fecha"]),
                    "descripcion": descripcion,
                    # Un solo elemento por ahora, mismo criterio que Casasweb
                    # -- ver docstring homólogo ahí.
                    "fotos": c["foto_url"],
                    "foto_count": c["foto_count"],
                    "fuente_datos": DATA_SOURCE,
                    "operacion": op_label,
                    "tipo_propiedad": tipo_label,
                    "ingestado_en": ingestado_en,
                    "datos_crudos": datos_crudos,
                }
            )
            n_new += 1

        print(f"{combo:5s} {tipo_label}/{op_label:10s}: {len(cards):4d} avisos ({n_new} nuevos)")

    write_csv(Path(args.out), rows)
    print(f"total filas: {len(rows)} (de {len(args.combos)} combinaciones)")


if __name__ == "__main__":
    main()
