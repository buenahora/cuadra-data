"""Offline tests for etl/extract/scrape_casasweb.py's pure functions
(normalize_row, collect_zona's partitioning logic). Deliberately does not
hit the network -- network access + robots.txt handling is exercised
manually (`uv run python etl/extract/scrape_casasweb.py --max-zonas N`),
same convention as test_scrape_infocasas.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "etl" / "extract"))

import scrape_casasweb  # noqa: E402
from scrape_casasweb import (  # noqa: E402
    BANOS_VALUES,
    DORM_VALUES,
    PAGE_CAP,
    collect_zona,
    normalize_row,
)


def make_item(**overrides):
    item = {
        "ref": "CW252165",
        "Zona": "Pocitos",
        "dorms": 1,
        "areaEdificada": 50,
        "precioA": 16500,
        "MonA": "$",
        "fechaMod": "2026-08-05T10:00:00",
    }
    item.update(overrides)
    return item


def test_normalize_row_uyu_listing():
    row = normalize_row(make_item(), fallback_uyu_per_usd=40.0)
    assert row == {
        "listing_id": "CW252165",
        "barrio": "Pocitos",
        "dormitorios": 1,
        "metraje_m2": 50,
        "precio": 16500,
        "estado": "activo",
        "publicado_en": "2026-08-05T10:00:00",
        "fotos": "",
        "foto_count": "",
    }


def test_normalize_row_usd_listing_uses_fallback_rate():
    row = normalize_row(make_item(MonA="U$S", precioA=650), fallback_uyu_per_usd=40.0)
    assert row["precio"] == 26000  # 650 * 40.0


def test_normalize_row_builds_fotos_from_foto_id():
    row = normalize_row(make_item(foto="1550753"), fallback_uyu_per_usd=40.0)
    assert row["fotos"] == "https://casasweb.com/fotos/1550753u.jpg"


def test_normalize_row_keeps_foto_as_is_when_already_a_url():
    # Avisos sindicados desde otras inmobiliarias/MLS -- ver docstring de
    # normalize_row -- traen `foto` como URL externa completa, no un ID.
    url = "https://static.tokkobroker.com/pictures/123456_abc.jpg"
    row = normalize_row(make_item(foto=url), fallback_uyu_per_usd=40.0)
    assert row["fotos"] == url


def test_normalize_row_skips_listing_without_zona():
    assert normalize_row(make_item(Zona=None), 40.0) is None


def test_normalize_row_skips_listing_without_dorms():
    assert normalize_row(make_item(dorms=None), 40.0) is None


def test_normalize_row_skips_listing_without_ref():
    assert normalize_row(make_item(ref=None), 40.0) is None


def test_normalize_row_skips_listing_without_price():
    assert normalize_row(make_item(precioA=None), 40.0) is None


def test_normalize_row_missing_area_becomes_empty_string():
    row = normalize_row(make_item(areaEdificada=None), 40.0)
    assert row["metraje_m2"] == ""


class _FakeSession:
    """Simula la API real: /propiedades devuelve PAGE_CAP avisos exactos
    mientras el filtro no sea lo bastante específico, y menos que eso una
    vez que la combinación de filtros identifica un subconjunto real."""

    def __init__(self, capped_until: str):
        # capped_until: "zona" | "dorm" | "banos" | "none" -- en qué nivel de
        # partición deja de pegar el tope, para simular cada escenario.
        self.capped_until = capped_until
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(dict(params or {}))
        has_dorm = "dorm" in (params or {})
        has_banos = "banos" in (params or {})

        if self.capped_until == "none":
            hit_cap = False
        elif self.capped_until == "zona":
            hit_cap = not has_dorm
        elif self.capped_until == "dorm":
            hit_cap = not has_banos
        elif self.capped_until == "banos":
            hit_cap = True
        else:
            raise ValueError(self.capped_until)

        if hit_cap:
            n = PAGE_CAP
        else:
            n = 3

        dorm = params.get("dorm", 0)
        banos = params.get("banos", 0)
        items = [
            {"ref": f"CW{dorm}-{banos}-{i}", "Zona": "Pocitos", "dorms": dorm or 1}
            for i in range(n)
        ]
        return _FakeResponse(items)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def json(self):
        return self._payload


def test_collect_zona_returns_directly_when_under_cap():
    session = _FakeSession(capped_until="none")
    items = collect_zona(session, "A", "a", 1, zona=6, delay=0)
    assert len(items) == 3
    assert len(session.calls) == 1  # un solo request, sin partición


def test_collect_zona_partitions_by_dorm_when_zona_hits_cap():
    session = _FakeSession(capped_until="zona")
    items = collect_zona(session, "A", "a", 1, zona=6, delay=0)
    # 1 request de zona (descartado, pega el tope) + 1 por cada valor de dorm
    assert len(session.calls) == 1 + len(list(DORM_VALUES))
    # cada bucket de dorm aporta 3 avisos únicos -> 6 dorms * 3 = 18
    assert len(items) == len(list(DORM_VALUES)) * 3


def test_collect_zona_partitions_by_banos_when_dorm_still_hits_cap():
    session = _FakeSession(capped_until="dorm")
    items = collect_zona(session, "A", "a", 1, zona=6, delay=0)
    # 1 (zona) + 6 (dorm) + 6*5 (banos por cada dorm) requests
    assert len(session.calls) == 1 + len(list(DORM_VALUES)) + len(list(DORM_VALUES)) * len(list(BANOS_VALUES))
    assert len(items) == len(list(DORM_VALUES)) * len(list(BANOS_VALUES)) * 3


def test_collect_zona_still_capped_after_banos_partition_does_not_crash(capsys):
    # Caso límite real: zona=6 (Pocitos) dorm=1 banos=1 siguió devolviendo
    # exactamente PAGE_CAP en la corrida de referencia. collect_zona debe
    # devolver esos avisos igual (mejor parcial que nada) y avisar por stdout,
    # no fallar ni perder silenciosamente la partición.
    session = _FakeSession(capped_until="banos")
    items = collect_zona(session, "A", "a", 1, zona=6, delay=0)
    assert len(items) > 0
    assert "posible truncamiento" in capsys.readouterr().out


def test_fetch_propiedades_returns_none_on_network_error():
    class _RaisingSession:
        def get(self, url, params=None, timeout=None):
            import requests

            raise requests.exceptions.ReadTimeout("read timed out")

    assert scrape_casasweb.fetch_propiedades(_RaisingSession(), {}) is None


class _StatusSequenceResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _StatusSequenceSession:
    """Devuelve los status codes de `sequence` en orden, uno por request."""

    def __init__(self, sequence):
        self.sequence = list(sequence)
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        status = self.sequence[self.calls]
        self.calls += 1
        payload = [{"ref": "CW1"}] if status == 200 else None
        return _StatusSequenceResponse(status, payload)


def test_fetch_propiedades_retries_429_with_backoff_then_succeeds(monkeypatch):
    sleeps = []
    monkeypatch.setattr(scrape_casasweb.time, "sleep", lambda s: sleeps.append(s))
    session = _StatusSequenceSession([429, 429, 200])

    result = scrape_casasweb.fetch_propiedades(session, {})

    assert result == [{"ref": "CW1"}]
    assert session.calls == 3
    # backoff exponencial: segundo intento espera el doble que el primero
    assert sleeps == [scrape_casasweb.INITIAL_BACKOFF_429_SECONDS, scrape_casasweb.INITIAL_BACKOFF_429_SECONDS * 2]


def test_fetch_propiedades_gives_up_after_max_retries_of_429(monkeypatch):
    monkeypatch.setattr(scrape_casasweb.time, "sleep", lambda s: None)
    session = _StatusSequenceSession([429] * (scrape_casasweb.MAX_RETRIES_429 + 1))

    result = scrape_casasweb.fetch_propiedades(session, {})

    assert result is None
    assert session.calls == scrape_casasweb.MAX_RETRIES_429 + 1
