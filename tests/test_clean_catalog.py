from pathlib import Path

import pytest

from clean_catalog import (
    clean_catalog,
    clean_row,
    parse_brand,
    parse_oem,
    parse_pack,
    parse_price,
    sku_code_pattern,
)

DATA = Path(__file__).resolve().parent.parent / "data"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1 500 руб", 1500.0),
        ("1500.00", 1500.0),
        ("1500р", 1500.0),
        ("1 500 rub", 1500.0),
        ("8 990,00", 8990.0),
        ("390,5", 390.5),
        ("от 450 руб", 450.0),
        ("3 200 р", 3200.0),
        ("890 руб.", 890.0),
        ("250", 250.0),
        ("abc", None),
        ("", None),
    ],
)
def test_parse_price(raw, expected):
    assert parse_price(raw) == expected


def test_parse_brand_finds_known_brand_case_insensitive():
    brand, rest = parse_brand("Колодки тормозные передние Mavico MV1028F Lada Vesta")
    assert brand == "MAVICO"
    assert "Mavico" not in rest


def test_parse_brand_word_boundary_no_false_positive():
    # "ZF" не должен ложно сработать внутри произвольного слова
    brand, rest = parse_brand("Крепёж ZFOOBAR подвески")
    assert brand == ""
    assert rest == "Крепёж ZFOOBAR подвески"


def test_parse_brand_unknown_brand_stays_in_text():
    brand, rest = parse_brand("Щётки Деталиус 40х40")
    assert brand == "ДЕТАЛИУС"
    assert "40х40" in rest


def test_parse_oem_extracts_code():
    code, rest = parse_oem("Renault Logan OEM 8200123456 к-т 2шт")
    assert code == "8200123456"
    assert "OEM" not in rest


def test_parse_oem_absent():
    code, rest = parse_oem("Фильтр масляный Hyundai Solaris")
    assert code == ""
    assert rest == "Фильтр масляный Hyundai Solaris"


@pytest.mark.parametrize(
    "raw, expected_type, expected_qty",
    [
        ("комплект 4 шт", "компл", "4"),
        ("4шт", "шт", "4"),
        ("2 шт", "шт", "2"),
        ("к-т 2шт", "компл", "2"),
        ("набор 3 шт", "компл", "3"),
        ("пара", "компл", "2"),
        ("1 компл.", "компл", "1"),
        ("1шт", "шт", "1"),
        ("без упоминания количества", "", ""),
    ],
)
def test_parse_pack(raw, expected_type, expected_qty):
    pack_type, qty, _rest = parse_pack(raw)
    assert pack_type == expected_type
    assert qty == expected_qty


def test_parse_pack_does_not_eat_dimension_digits():
    _pack_type, qty, rest = parse_pack("микрофибры 40х40 набор 3 шт")
    assert qty == "3"
    assert "40х40" in rest


def test_sku_code_pattern_matches_variants():
    pattern = sku_code_pattern("MF1041")
    assert pattern.search("Mavico MF-1041 Hyundai")
    assert pattern.search("Mavico MF 1041 Hyundai")
    assert pattern.search("Mavico MF1041 Hyundai")


def test_sku_code_pattern_ignores_purely_numeric_sku():
    # числовой id (не буквенно-цифровой артикул) не должен использоваться
    # для вырезания текста — иначе случайно съест число штук
    assert sku_code_pattern("4") is None
    assert sku_code_pattern("") is None


def test_clean_row_full_pipeline():
    row = clean_row(
        "Колодки тормозные передние Mavico MV1028F Lada Vesta комплект 4 шт",
        "1 500 руб",
        sku="MV1028F",
    )
    assert row["brand"] == "MAVICO"
    assert row["pack_type"] == "компл"
    assert row["pack_qty"] == "4"
    assert row["price_rub"] == 1500.0
    assert "MV1028F" not in row["clean_name"]
    assert row["clean_name"] == "Колодки тормозные передние Lada Vesta"


def test_clean_catalog_on_real_data(tmp_path):
    out_path = tmp_path / "clean.csv"
    total_in, total_out, dropped_dupe = clean_catalog(str(DATA / "catalog_raw.csv"), str(out_path))

    assert total_in == 19
    assert dropped_dupe == 6
    assert total_out == 12

    import csv

    with out_path.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 12
    # у всех строк с известным брендом Mavico колонка brand заполнена
    mavico_rows = [r for r in rows if r["sku"] != "13"]
    assert all(r["brand"] for r in mavico_rows)
    # дубли по sku схлопнуты в одну запись
    skus = [r["sku"] for r in rows]
    assert len(skus) == len(set(skus))


def test_clean_catalog_drops_fully_empty_row(tmp_path):
    in_path = tmp_path / "raw.csv"
    in_path.write_text(
        "id,name,price\n"
        '1,"Тормозные колодки BOSCH OEM 0986BB0620 1 компл.","1 500 руб"\n'
        ",,\n"  # полностью пустая строка — должна быть отброшена
        '2,"Фильтр масляный MANN OEM W712/75 1 шт","450р"\n',
        encoding="utf-8",
    )
    out_path = tmp_path / "clean.csv"
    total_in, total_out, _dropped_dupe = clean_catalog(str(in_path), str(out_path))
    assert total_in == 3
    assert total_out == 2  # одна полностью пустая строка отброшена
