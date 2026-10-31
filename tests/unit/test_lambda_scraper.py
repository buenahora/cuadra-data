"""Offline tests de lambdas/scraper/app.py -- el handler de la Lambda de
extracción de InfoCasas. Mismo criterio que test_scrape_infocasas.py: nada de
red. Lo que se testea acá es el reparto en tandas y el contrato de claves de
S3; el scraping en sí ya está cubierto por los tests del extractor."""

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "etl" / "extract"))
sys.path.insert(0, str(RAIZ / "lambdas" / "scraper"))

import pytest  # noqa: E402

import app  # noqa: E402
import scrape_infocasas  # noqa: E402


def test_tandas_cubren_todas_las_paginas_sin_solaparse():
    tandas = app.tandas_de(359, 150)
    assert tandas == [
        {"start_page": 1, "pages": 150},
        {"start_page": 151, "pages": 150},
        {"start_page": 301, "pages": 59},
    ]
    # Sin huecos ni repetidos: la unión es exactamente 1..359.
    paginas = [p for t in tandas for p in range(t["start_page"], t["start_page"] + t["pages"])]
    assert paginas == list(range(1, 360))


def test_una_sola_tanda_cuando_entra_entera():
    assert app.tandas_de(100, 150) == [{"start_page": 1, "pages": 100}]


def test_tandas_exactas_no_dejan_una_vacia_al_final():
    assert app.tandas_de(300, 150) == [
        {"start_page": 1, "pages": 150},
        {"start_page": 151, "pages": 150},
    ]


def test_sin_paginas_no_hay_tandas():
    assert app.tandas_de(0, 150) == []


def test_tamano_de_tanda_invalido_falla_temprano():
    with pytest.raises(ValueError):
        app.tandas_de(10, 0)


def test_clave_de_tanda_es_deterministica_y_particionada():
    # Particionada por fuente y fecha: es el contrato que lee el job de Glue
    # (glue/transform.py) y el que tiene que cumplir cualquier fuente nueva.
    clave = app.clave_de_tanda("infocasas", "2026-10-30", 1, 151, 150)
    assert clave == "fuente=infocasas/fecha=2026-10-30/listings-order1-p151-300.csv"
    assert app.clave_de_tanda("infocasas", "2026-10-30", 1, 151, 150) == clave


def test_plan_arma_un_payload_autocontenido_por_tanda(monkeypatch):
    # Cada tanda tiene que viajar con fecha y modo: el Map de la state machine
    # la pasa tal cual como payload de la Lambda, sin reinyectar nada.
    monkeypatch.setattr(app.infocasas, "plan_pages", lambda *a, **k: 200)
    monkeypatch.setattr(app, "PAGINAS_POR_TANDA", 150)

    plan = app.handler({"modo": "plan", "fecha": "2026-10-30"}, None)

    assert plan["fuente"] == "infocasas"
    assert plan["total_paginas"] == {"1": 200, "4": 200}
    assert len(plan["tandas"]) == 2 * len(scrape_infocasas.STABLE_ORDERS)
    for tanda in plan["tandas"]:
        assert tanda["modo"] == "extract"
        assert tanda["fecha"] == "2026-10-30"
        assert tanda["order"] in scrape_infocasas.STABLE_ORDERS
        assert set(tanda) == {"modo", "fecha", "order", "start_page", "pages"}


def test_modo_desconocido_falla():
    with pytest.raises(ValueError):
        app.handler({"modo": "lo-que-sea", "fecha": "2026-10-30"}, None)
