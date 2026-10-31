"""Offline tests for etl/extract/scrape_buscandocasa.py's pure functions
(parse_cards, normalize_price, fecha_iso). Deliberately does not hit the
network -- network access + the real 21-combination run is exercised
manually (`uv run python etl/extract/scrape_buscandocasa.py`), same
convention as test_scrape_infocasas.py / test_scrape_casasweb.py.

Los fragmentos HTML de los fixtures de abajo son un recorte fiel de la
estructura real devuelta por 0/0_in{op}_v30.asp (ver docstring del módulo
y docs/scraping-strategy.md §7.3 para el research completo), no HTML
inventado -- capturados de una corrida real contra el sitio el 2026-08-13.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "etl" / "extract"))

from scrape_buscandocasa import (  # noqa: E402
    fecha_iso,
    normalize_price,
    parse_cards,
)

APARTMENT_CARD = """
<div id="div_207AP20270" class="div_resultados_ap_0_0" style="">
	<table border=0 cellpadding=3 cellspacing=0 class="resultados_0_0" xbgcolor="white">
<tr class="dedo" valign="top" id="tr4207AP20270" onclick="cargar_ficha('207AP2027','s',1189,'0');">
	<td colspan=2 id="td5207AP20270" class="resultadost_foto" valign="top" align="center" style="background-image:url('/fotos/207/207AP2027_320.jpg');background-size:cover;background-position:center">
			<img class="rounded" src="/fotos/207/207AP2027_320.jpg" title="49 fotos">
	</td>
</tr>
<tr class="dedo" valign="top" id="tr2207AP20270" onclick="cargar_ficha('207AP2027','s',1189,'0');">
	<td id="td3207AP20270" colspan=2 class="resultadost3">
	<div class="precio" id="pre207AP20270" >
		US$ 2.000.000
	</div>
</td></tr>
<tr class="dedo" valign="top" id="tr4207AP20270" onclick="cargar_ficha('207AP2027','s',1189,'0');">
	<td id="td4207AP20270" colspan=2 class="resultadost3">
	<div class="resultados_lugar_0_0"><b><b>MONTEVIDEO / PUNTA&nbsp;CTAS&nbsp;GOLF</b> </b><br><span style="font-size:10pt">BR. ARTIGAS Y GOLF</span></div>
	</td>
</tr>
	<tr class="dedo" valign="top" id="tr7207AP20270" onclick="cargar_ficha('207AP2027','s',1189,'0');">
		<td colspan="1" id="td7207AP20270" class="resultadost3">
		<table width="100%" border=0 cellspacing=0 cellpadding=0><tbody><tr valign="bottom"><td class="haundi" rowspan="2" align="right" width="7%">3</td><td class="txiki" width="33%">3 suits / 4 placares</td><td class="haundi" rowspan="2" align="right" width="10%">3</td><td class="txiki" width="20%">+1 toilette</td><td class="haundi" rowspan="2" align="right" width="20%">300<span class="txiki">m&sup2;</span></td></tr><tr valign="top"><td class="txiki"><b>dorm.</b></td><td class="txiki">ba&ntilde;os+serv.</td></tr></tbody></table>
		</td>
	</tr>
	<tr class="dedo" id="tr6207AP20270" onclick="cargar_ficha('207AP2027','s',1189,'0');">
		<td colspan="1" id="td6207AP20270" class="resultadost5">
			<table width="96%" align="center">
				<tr valign="top"><td>Frente<br>5&ordm; piso<br>Impecable</td><td align="right">Central Losa<br>Garage 2 autos</td></tr>
				<tr valign="top"><td></td><td align="right"><span style="font-size:9pt;">02/05/2025</span></td></tr>
			</table>
		</td>
	</tr>
	</table>
</div>
"""

TERRENO_CARD = """
<div id="div_950TE2626000" class="div_resultados_te_0_0" style="">
	<table border=0 cellpadding=3 cellspacing=0 class="resultados_0_0" xbgcolor="white">
<tr class="dedo" valign="top" id="tr2950TE2626000" onclick="cargar_ficha('950TE262600','s',2176,'0');">
	<td id="td3950TE2626000" colspan=2 class="resultadost3">
	<div class="precio" id="pre950TE2626000" >
		US$ 6.900.000
	</div>
</td></tr>
<tr class="dedo" valign="top" id="tr4950TE2626000" onclick="cargar_ficha('950TE262600','s',2176,'0');">
	<td id="td4950TE2626000" colspan=2 class="resultadost3">
	<div class="resultados_lugar_0_0"><b><b>MONTEVIDEO / POCITOS</b> </b><br><span style="font-size:10pt">LUIS A. HERRERA 2451 Y P. ELLAURI</span></div>
	</td>
</tr>
	<tr class="dedo" valign="top" id="tr7950TE2626000" onclick="cargar_ficha('950TE262600','s',2176,'0');">
		<td colspan="1" id="td7950TE2626000" class="resultadost3">
		<table width="100%" border=0 cellspacing=0 cellpadding=0><tbody><tr valign="bottom"><td class="haundi" align="right" width="15%">1.500</td><td class="txiki" width="15%">m&sup2;</td><td class="haundi" align="right" width="20%">40</td><td class="txiki" width="20%">m fte.</td><td class="haundi" align="right" width="20%">4.600</td><td class="txiki" width="10%">US$/m&sup2;</td></tr></tbody></table>
	 </td>
	<tr class="dedo" id="tr6950TE2626000" onclick="cargar_ficha('950TE262600','s',2176,'0');">
		<td colspan="1" id="td6950TE2626000" class="resultadost5">
			<table width="96%" align="center">
				<tr valign="top"><td>A 500mts del mar<br>Orientaci&oacute;n N</td><td align="right">Nivel normal<br></td></tr>
				<tr valign="top"><td></td><td align="right"><span style="font-size:9pt;">18/07/2026</span></td></tr>
			</table>
		</td>
	</tr>
	</table>
</div>
"""

# Aviso real encontrado el 2026-08-13 con un espacio literal dentro del ref
# (data quality de la inmobiliaria "URUGUARGEN", no del extractor) -- el
# regex de ref tiene que sobrevivir a esto, ver docs/scraping-strategy.md §7.3.
MALFORMED_REF_CARD = """
<div id="div_029AP334 Valdi0" class="div_resultados_ap_0_0" style="">
<tr id="tr1029AP334 Valdi0">
	<td id="td1029AP334 Valdi0" colspan=2 class="resultadost1" valign="middle" style='background-color:firebrick'>
		<a href="#" onclick="cargar_ficha('029AP334 Valdi','s',675,'0');"><b><span class="list_numero">36</span> URUGUARGEN</b></a>
	</td>
</tr>
	<td class="precio"><div class="precio">$ 13.000</div></td>
	<div class="resultados_lugar_0_0"><b><b>MONTEVIDEO / BARRA&nbsp;CARRASCO</b> </b><br><span>CALLE X</span></div>
</div>
"""


def test_parse_cards_extracts_apartment_fields():
    cards = parse_cards(APARTMENT_CARD)
    assert len(cards) == 1
    c = cards[0]
    assert c["ref"] == "207AP2027"
    assert c["precio_txt"] == "US$ 2.000.000"
    assert c["barrio"] == "PUNTA CTAS GOLF"
    assert c["dormitorios"] == "3"
    assert c["m2"] == "300"
    assert c["fecha"] == "02/05/2025"


def test_parse_cards_terreno_has_no_dormitorios_but_has_m2():
    cards = parse_cards(TERRENO_CARD)
    assert len(cards) == 1
    c = cards[0]
    assert c["ref"] == "950TE262600"
    assert c["dormitorios"] == ""  # sin "dorm." en el bloque de detalle -> vacío, no descartado
    assert c["m2"] == "1500"  # separador de miles removido
    assert c["barrio"] == "POCITOS"


def test_parse_cards_survives_ref_with_embedded_space():
    cards = parse_cards(MALFORMED_REF_CARD)
    assert len(cards) == 1
    assert cards[0]["ref"] == "029AP334 Valdi"


def test_parse_cards_two_cards_in_one_fragment():
    cards = parse_cards(APARTMENT_CARD + TERRENO_CARD)
    assert [c["ref"] for c in cards] == ["207AP2027", "950TE262600"]


def test_normalize_price_uyu():
    assert normalize_price("$ 28.500", uyu_per_usd=40.35) == 28500


def test_normalize_price_usd_converts_with_rate():
    assert normalize_price("US$ 1.000", uyu_per_usd=40.0) == 40000


def test_normalize_price_strips_highlight_marks():
    assert normalize_price("¡US$ 560.000!", uyu_per_usd=40.0) == 22400000


def test_normalize_price_returns_none_for_consultar():
    assert normalize_price("CONSULTAR", uyu_per_usd=40.0) is None


def test_normalize_price_returns_none_for_reservado():
    assert normalize_price("RESERVADO", uyu_per_usd=40.0) is None


def test_fecha_iso_converts_ddmmyyyy():
    assert fecha_iso("02/05/2025") == "2025-05-02"


def test_fecha_iso_empty_input_stays_empty():
    assert fecha_iso("") == ""
