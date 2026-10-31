"""Offline tests for etl/extract/scrape_infocasas.py's pure functions
(category_url, normalize_row). Deliberately does not hit the network --
network access + robots.txt handling is exercised manually
(`uv run python etl/extract/scrape_infocasas.py --max-pages N`), not in the
test suite, per docs/scraping-strategy.md."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "etl" / "extract"))

import pytest
import requests

import scrape_infocasas  # noqa: E402
from scrape_infocasas import (  # noqa: E402
    ORDER_M2_ASC,
    ORDER_PRICE_ASC,
    STABLE_ORDERS,
    category_url,
    fetch_page,
    normalize_row,
    scrape,
)


def make_item(**overrides):
    item = {
        "id": 194077005,
        "locations": {"neighbourhood": [{"name": "Cordón"}]},
        "bedrooms": 1,
        "m2": 50,
        "price": {"amount": 26000, "currency": {"id": 2, "rate": 40.0}},
        "price_amount_usd": 650,
        "active": True,
        "created_at": "2026-08-05",
    }
    item.update(overrides)
    return item


def test_category_url_page_one_has_no_pagina_suffix():
    assert (
        category_url("alquiler", "apartamentos", "montevideo", 1, ORDER_PRICE_ASC)
        == "https://www.infocasas.com.uy/alquiler/apartamentos/montevideo?order=1"
    )


def test_category_url_later_page_has_pagina_suffix():
    assert (
        category_url("alquiler", "apartamentos", "montevideo", 3, ORDER_PRICE_ASC)
        == "https://www.infocasas.com.uy/alquiler/apartamentos/montevideo/pagina3?order=1"
    )


def test_category_url_passes_through_requested_order():
    assert "order=4" in category_url("alquiler", "apartamentos", "montevideo", 1, ORDER_M2_ASC)


def test_stable_orders_are_price_asc_and_m2_asc():
    # Un solo orden deja ~10% de los avisos afuera por empates (varios avisos
    # al mismo precio, o al mismo m²) -- combinar dos criterios que casi
    # nunca empatan juntos es lo que cierra esa brecha. Ver docstring del
    # módulo y docs/scraping-strategy.md para los números reales.
    assert STABLE_ORDERS == (ORDER_PRICE_ASC, ORDER_M2_ASC)


def test_normalize_row_uyu_listing():
    fallback_rate = [40.0]
    item = make_item()
    row = normalize_row(item, fallback_rate)
    # Contrato completo de columnas de bronze (el mismo de write_csv y de
    # generate_synthetic_listings.py): un aviso mínimo como make_item() deja
    # vacías las que dependen de campos que el item no trae, pero las
    # devuelve igual -- si falta una, raw.listings cambia de forma y los
    # modelos de staging no bindean.
    assert row == {
        "listing_id": "IC194077005",
        "barrio": "Cordón",
        "direccion": "",
        "latitud": "",
        "longitud": "",
        "dormitorios": 1,
        "banos": "",
        "metraje_m2": 50,
        "precio": 26000,
        "estado": "activo",
        "publicado_en": "2026-08-05",
        "descripcion": "",
        "amenities": "",
        "fotos": "",
        "foto_count": "",
        "datos_crudos": json.dumps(item, ensure_ascii=False),
    }
    # UYU listings update the running fallback rate from their own currency.rate
    assert fallback_rate == [40.0]


def test_normalize_row_maps_bathrooms_to_banos():
    row = normalize_row(make_item(bathrooms=2), [40.0])
    assert row["banos"] == 2


def test_normalize_row_takes_direccion_and_coords_from_the_same_json():
    # Ver normalize_row(): location_point es un WKT "POINT (lon lat)" --
    # ojo el orden lon/lat, no lat/lon.
    row = normalize_row(
        make_item(
            address="Soriano 1234",
            locations={
                "neighbourhood": [{"name": "Cordón"}],
                "location_point": "POINT (-56.1833 -34.9033)",
            },
        ),
        [40.0],
    )
    assert row["direccion"] == "Soriano 1234"
    assert row["longitud"] == "-56.1833"
    assert row["latitud"] == "-34.9033"


def test_normalize_row_flattens_facilities_into_pipe_separated_amenities():
    row = normalize_row(
        make_item(
            description="Luminoso, al frente.",
            facilities=[{"name": "Garaje"}, {"name": "Parrillero"}, {"sin_nombre": True}],
        ),
        [40.0],
    )
    assert row["descripcion"] == "Luminoso, al frente."
    assert row["amenities"] == "Garaje|Parrillero"


def test_normalize_row_datos_crudos_keeps_fields_without_column_of_their_own():
    # El punto de datos_crudos: campos que el extractor no normaliza (acá
    # commonExpenses) viajan igual, sin agregar una columna nueva.
    row = normalize_row(make_item(commonExpenses=4500), [40.0])
    assert json.loads(row["datos_crudos"])["commonExpenses"] == 4500


def test_normalize_row_updates_fallback_rate_from_uyu_listing():
    fallback_rate = [40.0]
    normalize_row(
        make_item(price={"amount": 26000, "currency": {"id": 2, "rate": 41.5}}),
        fallback_rate,
    )
    assert fallback_rate == [41.5]


def test_normalize_row_usd_listing_uses_price_amount_usd_with_fallback_rate():
    fallback_rate = [40.0]
    row = normalize_row(
        make_item(
            price={"amount": 650, "currency": {"id": 1}},
            price_amount_usd=650,
        ),
        fallback_rate,
    )
    assert row["precio"] == 26000  # 650 * 40.0


def test_normalize_row_usd_listing_without_price_amount_usd_falls_back_to_amount():
    fallback_rate = [40.0]
    row = normalize_row(
        make_item(price={"amount": 700, "currency": {"id": 1}}, price_amount_usd=None),
        fallback_rate,
    )
    assert row["precio"] == 28000  # 700 * 40.0


def test_normalize_row_skips_listing_without_neighbourhood():
    row = normalize_row(make_item(locations={"neighbourhood": []}), [40.0])
    assert row is None


def test_normalize_row_skips_listing_without_bedrooms():
    row = normalize_row(make_item(bedrooms=None), [40.0])
    assert row is None


def test_normalize_row_skips_listing_without_price_amount():
    row = normalize_row(make_item(price={"amount": None, "currency": {"id": 2}}), [40.0])
    assert row is None


def test_normalize_row_paused_listing_maps_to_pausado():
    row = normalize_row(make_item(active=False), [40.0])
    assert row["estado"] == "pausado"


def test_normalize_row_falls_back_to_m2_built_when_m2_missing():
    row = normalize_row(make_item(m2=None, m2Built=64), [40.0])
    assert row["metraje_m2"] == 64


class _RaisingSession:
    def get(self, url, timeout=None):
        raise requests.exceptions.ReadTimeout("read timed out")


def test_fetch_page_returns_none_on_network_error_instead_of_raising():
    # Regression test: a bare requests.exceptions.RequestException (timeout,
    # connection reset, etc.) used to propagate uncaught out of fetch_page,
    # crashing the whole scrape() loop -- and losing every row accumulated so
    # far, since write_csv only runs once at the very end. scrape()'s
    # MAX_CONSECUTIVE_FAILURES retry logic only works if fetch_page reports
    # failures as None instead of raising.
    assert fetch_page(_RaisingSession(), "https://www.infocasas.com.uy/x") is None


def test_scrape_merges_two_orders_to_recover_items_a_single_order_would_miss(monkeypatch):
    # Regression test for the tie-drift problem (docs/scraping-strategy.md):
    # a single sort order loses ~10% of listings to same-value ties. Here
    # item 2 only ever shows up under order=1 (price) and item 4 only ever
    # shows up under order=4 (m2) -- simulating exactly that failure mode.
    # scrape()'s job is to combine both passes so the union still has all 4.
    pages_by_order = {
        ORDER_PRICE_ASC: [
            {"data": [1, 2], "paginatorInfo": {"hasMorePages": True, "total": 4}},
            {"data": [2, 3], "paginatorInfo": {"hasMorePages": False, "total": 4}},
        ],
        ORDER_M2_ASC: [
            {"data": [1, 4], "paginatorInfo": {"hasMorePages": True, "total": 4}},
            {"data": [3, 4], "paginatorInfo": {"hasMorePages": False, "total": 4}},
        ],
    }

    def fake_fetch_page(session, url):
        order = ORDER_PRICE_ASC if "order=1" in url else ORDER_M2_ASC
        page_index = 0 if "pagina" not in url else int(url.split("pagina")[1].split("?")[0]) - 1
        raw = pages_by_order[order][page_index]
        return {
            "data": [make_item(id=item_id, price={"amount": item_id * 1000, "currency": {"id": 2, "rate": 40.0}})
                     for item_id in raw["data"]],
            "paginatorInfo": raw["paginatorInfo"],
        }

    monkeypatch.setattr(scrape_infocasas, "robots_allows", lambda session, url: True)
    monkeypatch.setattr(scrape_infocasas, "fetch_page", fake_fetch_page)

    rows = scrape("alquiler", "apartamentos", "montevideo", max_pages=None, delay=0)

    assert {row["listing_id"] for row in rows} == {"IC1", "IC2", "IC3", "IC4"}


def _paginator(**overrides):
    paginator = {"hasMorePages": True, "total": 7500}
    paginator.update(overrides)
    return paginator


def test_plan_pages_prefiere_last_page_si_el_sitio_lo_expone(monkeypatch):
    monkeypatch.setattr(scrape_infocasas, "robots_allows", lambda session, url: True)
    monkeypatch.setattr(
        scrape_infocasas,
        "fetch_page",
        lambda session, url: {"data": [make_item()] * 21, "paginatorInfo": _paginator(lastPage=359)},
    )
    assert scrape_infocasas.plan_pages(None, "alquiler", "apartamentos", "montevideo", ORDER_PRICE_ASC) == 359


def test_plan_pages_deduce_las_paginas_del_total_y_el_tamano_de_pagina(monkeypatch):
    # Sin lastPage: 100 avisos a 21 por página son 5 páginas (la última corta).
    monkeypatch.setattr(scrape_infocasas, "robots_allows", lambda session, url: True)
    monkeypatch.setattr(
        scrape_infocasas,
        "fetch_page",
        lambda session, url: {"data": [make_item()] * 21, "paginatorInfo": _paginator(total=100)},
    )
    assert scrape_infocasas.plan_pages(None, "alquiler", "apartamentos", "montevideo", ORDER_PRICE_ASC) == 5


def test_plan_pages_falla_si_el_paginator_no_alcanza(monkeypatch):
    # Mejor cortar acá que planificar una corrida de una sola tanda y creer
    # que se bajó todo.
    monkeypatch.setattr(scrape_infocasas, "robots_allows", lambda session, url: True)
    monkeypatch.setattr(
        scrape_infocasas,
        "fetch_page",
        lambda session, url: {"data": [], "paginatorInfo": {"hasMorePages": False}},
    )
    with pytest.raises(RuntimeError):
        scrape_infocasas.plan_pages(None, "alquiler", "apartamentos", "montevideo", ORDER_PRICE_ASC)


def test_scrape_de_una_tanda_pide_solo_sus_paginas(monkeypatch):
    # Lo que corre cada invocación de la Lambda: una tanda de páginas de un
    # solo orden (ver lambdas/scraper/app.py). Si start_page no se respetara,
    # cada tanda volvería a bajar la corrida entera desde la página 1.
    pedidas = []

    def fake_fetch_page(session, url):
        pedidas.append(url)
        return {"data": [make_item()], "paginatorInfo": _paginator()}

    monkeypatch.setattr(scrape_infocasas, "robots_allows", lambda session, url: True)
    monkeypatch.setattr(scrape_infocasas, "fetch_page", fake_fetch_page)

    scrape(
        "alquiler",
        "apartamentos",
        "montevideo",
        max_pages=3,
        delay=0,
        orders=(ORDER_PRICE_ASC,),
        start_page=5,
    )

    assert pedidas == [
        category_url("alquiler", "apartamentos", "montevideo", page, ORDER_PRICE_ASC)
        for page in (5, 6, 7)
    ]


def test_scrape_de_una_tanda_corta_si_el_sitio_se_queda_sin_paginas(monkeypatch):
    # El plan se calcula al principio de la corrida; si el sitio achicó el
    # total en el medio, la tanda tiene que cortar por hasMorePages y no
    # seguir pidiendo páginas que ya no existen.
    pedidas = []

    def fake_fetch_page(session, url):
        pedidas.append(url)
        return {"data": [make_item()], "paginatorInfo": _paginator(hasMorePages=False)}

    monkeypatch.setattr(scrape_infocasas, "robots_allows", lambda session, url: True)
    monkeypatch.setattr(scrape_infocasas, "fetch_page", fake_fetch_page)

    scrape(
        "alquiler",
        "apartamentos",
        "montevideo",
        max_pages=50,
        delay=0,
        orders=(ORDER_PRICE_ASC,),
        start_page=10,
    )

    assert pedidas == [category_url("alquiler", "apartamentos", "montevideo", 10, ORDER_PRICE_ASC)]
