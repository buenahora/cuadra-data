"""Offline tests for etl/extract/scrape_infocasas.py's pure functions
(category_url, normalize_row). Deliberately does not hit the network --
network access + robots.txt handling is exercised manually
(`uv run python etl/extract/scrape_infocasas.py --max-pages N`), not in the
test suite, per docs/scraping-strategy.md."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "etl" / "extract"))

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
    row = normalize_row(make_item(), fallback_rate)
    assert row == {
        "listing_id": "IC194077005",
        "barrio": "Cordón",
        "dormitorios": 1,
        "metraje_m2": 50,
        "precio": 26000,
        "estado": "activo",
        "publicado_en": "2026-08-05",
        "fotos": "",
        "foto_count": "",
    }
    # UYU listings update the running fallback rate from their own currency.rate
    assert fallback_rate == [40.0]


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
