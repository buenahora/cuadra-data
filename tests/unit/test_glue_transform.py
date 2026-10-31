"""Offline tests de glue/transform.py -- el job que lleva lo que dejan los
extractores en el bucket raw al contrato de curated. Lo que se testea es el
contrato de columnas y el dedupe; S3/Postgres quedan afuera (el job los toca
con boto3, que acá no se importa)."""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "glue"))

import transform  # noqa: E402


def fila_cruda(**overrides):
    """Una fila tal cual sale de etl/extract/scrape_infocasas.py (todo
    string, como lo lee pd.read_csv con dtype=str)."""
    fila = {
        "listing_id": "IC194077005",
        "fuente_datos": "infocasas",
        "barrio": "Cordón",
        "direccion": "Soriano 1234",
        "latitud": "-34.9033",
        "longitud": "-56.1833",
        "dormitorios": "1",
        "banos": "2",
        "metraje_m2": "50",
        "precio": "26000",
        "estado": "activo",
        "publicado_en": "2026-08-05",
        "descripcion": "Luminoso",
        "amenities": "Garaje|Parrillero",
        "fotos": "https://x/1.jpg|https://x/2.jpg",
        "foto_count": "2",
        "operacion": "alquiler",
        "tipo_propiedad": "apartamentos",
        "ingestado_en": "2026-08-05T10:00:00+00:00",
        "datos_crudos": '{"id": 194077005}',
    }
    fila.update(overrides)
    return fila


def test_la_salida_es_exactamente_el_contrato_de_curated():
    salida = transform.normalizar(pd.DataFrame([fila_cruda()]))
    assert list(salida.columns) == transform.COLUMNAS
    # datos_crudos no va a curated: es el JSON entero del aviso, se queda en raw.
    assert "datos_crudos" not in salida.columns


def test_tipa_numeros_y_deja_el_resto_como_texto():
    salida = transform.normalizar(pd.DataFrame([fila_cruda()])).iloc[0]
    assert salida["dormitorios"] == 1
    assert salida["banos"] == 2
    assert salida["metraje_m2"] == 50
    assert salida["precio"] == 26000.0
    assert salida["latitud"] == -34.9033
    assert salida["barrio"] == "Cordón"
    assert salida["publicado_en"] == "2026-08-05"


def test_vacios_y_no_numericos_quedan_nulos():
    salida = transform.normalizar(
        pd.DataFrame([fila_cruda(banos="", direccion="", metraje_m2="s/d")])
    ).iloc[0]
    assert pd.isna(salida["banos"])
    assert pd.isna(salida["direccion"])
    assert pd.isna(salida["metraje_m2"])


def test_una_fuente_sin_todas_las_columnas_igual_entra(capsys):
    # Casasweb, por ejemplo, no tiene dirección ni coordenadas en la fuente.
    # El job no se cae: las deja en NULL y lo avisa en el log.
    cruda = fila_cruda()
    for columna in ("direccion", "latitud", "longitud", "descripcion"):
        del cruda[columna]
    salida = transform.normalizar(pd.DataFrame([cruda]))
    assert list(salida.columns) == transform.COLUMNAS
    assert salida.iloc[0]["direccion"] is None
    assert pd.isna(salida.iloc[0]["latitud"])
    assert "columnas del contrato que no vinieron" in capsys.readouterr().out


def test_dedupea_el_mismo_aviso_que_vino_en_dos_tandas():
    # El extractor de InfoCasas pagina dos órdenes distintos y cada uno corre
    # en varias tandas: el mismo aviso aparece en más de un CSV.
    salida = transform.normalizar(pd.DataFrame([fila_cruda(), fila_cruda(), fila_cruda(listing_id="IC2")]))
    assert sorted(salida["listing_id"]) == ["IC194077005", "IC2"]


def test_el_mismo_listing_id_de_dos_fuentes_no_se_pisa():
    salida = transform.normalizar(
        pd.DataFrame([fila_cruda(), fila_cruda(fuente_datos="otra")])
    )
    assert len(salida) == 2


def test_filas_sin_listing_id_se_descartan():
    salida = transform.normalizar(pd.DataFrame([fila_cruda(), fila_cruda(listing_id="")]))
    assert len(salida) == 1


def test_el_hash_cambia_con_el_precio_y_no_con_la_descripcion():
    base = transform.normalizar(pd.DataFrame([fila_cruda()])).iloc[0]["hash_contenido"]
    otro_precio = transform.normalizar(pd.DataFrame([fila_cruda(precio="27000")])).iloc[0]["hash_contenido"]
    otra_desc = transform.normalizar(pd.DataFrame([fila_cruda(descripcion="Otra cosa")])).iloc[0]["hash_contenido"]
    assert base != otro_precio
    assert base == otra_desc


def test_los_decimales_son_float_aunque_vengan_todos_enteros():
    # Regresión: con todos los precios enteros, to_numeric devolvía int64 y el
    # parquet salía con INT64 en una columna que el catálogo de Glue declara
    # `double` -- Athena no puede leer esa partición.
    salida = transform.normalizar(
        pd.DataFrame([fila_cruda(precio="26000"), fila_cruda(listing_id="IC2", precio="30000")])
    )
    for columna in transform.DECIMALES:
        assert salida[columna].dtype == "float64", columna
    for columna in transform.ENTEROS:
        assert salida[columna].dtype == "Int64", columna
