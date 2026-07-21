"""
Задача 3. Чистка каталога (без API).

Запуск:
    python clean_catalog.py --in data/catalog_raw.csv --out data/catalog_clean.csv

Что делает:
1. Разносит название и характеристики на отдельные колонки:
   brand (бренд), oem_number (OEM-номер), pack_type (штука/комплект),
   clean_name (название без служебных вставок).
2. Приводит цену к числу (float) в колонку price_rub, поддерживая форматы
   вида «1 500 руб», «1500.00», «1500р», «1 500 rub», с пробелами-
   разделителями тысяч (обычными и неразрывными).
3. Убирает точные дубликаты и полностью пустые строки.
4. Сохраняет результат в CSV.

Проверено на реальном data/catalog_raw.csv (каталог Mavico). Список брендов
(BRAND_HINTS) и ключевые слова упаковки (PACK_RE/COMPL_KEYWORDS_RE) собраны по
факту встречающихся в этом файле данных — при появлении новых брендов/форматов
их нужно туда добавить.
"""
from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("clean_catalog")

# Известные бренды автозапчастей — если список брендов заранее известен,
# распознавание брендов из свободного текста надёжнее регулярки.
# Дополните этот список реальными брендами из вашего каталога.
# MAVICO/DBA/Деталиус — бренды, реально встречающиеся в catalog_raw.csv;
# остальные оставлены как задел на случай расширения ассортимента.
BRAND_HINTS = [
    "MAVICO", "DBA", "ДЕТАЛИУС",
    "BOSCH", "TRW", "FEBI", "LEMFORDER", "SACHS", "LUK", "VALEO",
    "MANN", "MAHLE", "NGK", "DENSO", "CONTINENTAL", "ZF",
]

OEM_RE = re.compile(r"\bOEM[:\s#-]*([A-Za-z0-9._/-]{4,})\b", re.IGNORECASE)

# Число (опционально) + единица упаковки. В реальных данных встречаются и
# слитные упоминания вида «1 компл.» / «4 шт» (число прямо перед словом),
# и составные вида «комплект 4 шт» / «к-т 2шт» (два отдельных совпадения
# этой же регулярки подряд — обрабатываются в parse_pack).
PACK_RE = re.compile(
    r"(?<![\dxXхХ])(\d+)?\s*(компл(?:ект)?|к-?т|набор|пара|шт(?:ука|уки|)?|pcs?|set)\.?\b",
    re.IGNORECASE,
)
# Из этих ключевых слов складывается "комплект", а не поштучная единица.
COMPL_KEYWORDS_RE = re.compile(r"^(компл(?:ект)?|к-?т|набор|пара)$", re.IGNORECASE)

PRICE_RE = re.compile(
    r"(\d[\d\s\u00a0]*(?:[.,]\d{1,2})?)\s*(руб|rub|р)\.?", re.IGNORECASE
)
# на случай, если в цене нет суффикса валюты — просто число с пробелами/точкой
PLAIN_NUMBER_RE = re.compile(r"^\s*(\d[\d\s\u00a0]*(?:[.,]\d{1,2})?)\s*$")

FIELDNAMES = [
    "sku", "clean_name", "brand", "oem_number", "pack_type", "pack_qty",
    "price_rub", "stock", "original_name", "original_price",
]


def setup_logging() -> None:
    # На Windows консоль часто в cp1251 — кириллица в логах превращается
    # в кракозябры. Переключаем stdout/stderr на utf-8 явно.
    for stream in (sys.stdout, sys.stderr):
        if stream.encoding and stream.encoding.lower() != "utf-8":
            stream.reconfigure(encoding="utf-8", errors="replace")
    Path("logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("logs/clean_catalog.log", encoding="utf-8"),
        ],
    )


def parse_price(raw: str) -> float | None:
    if not raw:
        return None
    text = raw.replace("\u00a0", " ").strip()
    m = PRICE_RE.search(text)
    if not m:
        m = PLAIN_NUMBER_RE.match(text)
    if not m:
        return None
    number = m.group(1).replace(" ", "").replace("\u00a0", "")
    number = number.replace(",", ".")
    try:
        return round(float(number), 2)
    except ValueError:
        logger.warning("Не удалось распознать цену: %r", raw)
        return None


def parse_brand(text: str) -> tuple[str, str]:
    """Возвращает (brand, остаток_текста_без_бренда).

    Сравнение по границам слов (\\b), а не подстрокой — иначе короткий бренд
    (например, "ZF") может случайно совпасть внутри другого слова.
    """
    for brand in BRAND_HINTS:
        m = re.search(rf"\b{re.escape(brand)}\b", text, re.IGNORECASE)
        if m:
            cleaned = (text[: m.start()] + text[m.end() :]).strip()
            return brand, cleaned
    return "", text


def parse_oem(text: str) -> tuple[str, str]:
    m = OEM_RE.search(text)
    if m:
        code = m.group(1)
        cleaned = (text[: m.start()] + text[m.end() :]).strip()
        return code, cleaned
    return "", text


def sku_code_pattern(sku: str) -> re.Pattern | None:
    """Регулярка для поиска собственного артикула товара внутри названия.

    В сырых данных один и тот же код может быть написан по-разному
    (MF1041 / MF-1041 / MF 1041), поэтому код разбивается на буквенные и
    цифровые куски, между которыми допускаются пробел/дефис.

    Чисто цифровой sku (порядковый id) намеренно игнорируется: голая цифра
    слишком часто случайно совпадает с числом штук в названии ("4 шт"),
    а не является частью артикула.
    """
    if not sku or not re.search(r"[A-Za-zА-Яа-яЁё]", sku):
        return None
    parts = re.findall(r"[A-Za-zА-Яа-яЁё]+|\d+", sku)
    if not parts:
        return None
    pattern = r"\b" + r"[\s-]*".join(re.escape(p) for p in parts) + r"\b"
    return re.compile(pattern, re.IGNORECASE)


def parse_pack(text: str) -> tuple[str, str, str]:
    """Возвращает (pack_type: 'шт'|'компл'|'', pack_qty, остаток).

    В названии может быть несколько совпадений PACK_RE — например,
    "комплект 4 шт" даёт два матча ("комплект" и "4 шт"): тип упаковки
    берётся из компл-ключевого слова, если оно есть, а число штук — из
    того совпадения, где оно явно указано.
    """
    matches = list(PACK_RE.finditer(text))
    if not matches:
        return "", "", text

    pack_type, qty = "", ""
    for m in matches:
        digit, unit = m.group(1), m.group(2)
        if COMPL_KEYWORDS_RE.match(unit):
            pack_type = "компл"
            if digit:
                qty = digit
            elif not qty:
                qty = "2" if unit.lower().startswith("пара") else "1"
        else:
            if not pack_type:
                pack_type = "шт"
            if digit:
                qty = digit

    for m in sorted(matches, key=lambda mm: -mm.start()):
        text = text[: m.start()] + text[m.end() :]
    return pack_type, qty, text


def normalize_whitespace(text: str) -> str:
    text = re.sub(r"\(\s*\)", "", text)  # пустые скобки, оставшиеся после вырезания
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,;.-/")


def clean_row(raw_name: str, raw_price: str, sku: str = "") -> dict:
    name = (raw_name or "").strip()
    brand, rest = parse_brand(name)
    code_pattern = sku_code_pattern(sku)
    if code_pattern:
        rest = code_pattern.sub("", rest).strip()
    oem, rest = parse_oem(rest)
    pack_type, pack_qty, rest = parse_pack(rest)
    clean_name = normalize_whitespace(rest)
    price = parse_price(raw_price)
    return {
        "clean_name": clean_name,
        "brand": brand,
        "oem_number": oem,
        "pack_type": pack_type,
        "pack_qty": pack_qty,
        "price_rub": price if price is not None else "",
        "original_name": name,
        "original_price": raw_price,
    }


def detect_columns(fieldnames: list[str]) -> tuple[str, str, str | None, str | None]:
    """Пытаемся угадать, какие колонки в сыром CSV — название/цена/sku/остаток.

    Если реальные заголовки отличаются от ожидаемых — здесь нужно поправить
    список кандидатов под фактические названия колонок.
    """
    lower = {f.lower(): f for f in fieldnames}
    name_candidates = ["name", "title", "название", "наименование", "товар"]
    price_candidates = ["price", "цена", "стоимость"]
    sku_candidates = ["sku", "id", "артикул", "offer_id"]
    stock_candidates = ["stock", "остаток", "quantity", "qty"]

    def pick(cands: list[str]) -> str | None:
        for c in cands:
            if c in lower:
                return lower[c]
        return None

    name_col = pick(name_candidates)
    price_col = pick(price_candidates)
    sku_col = pick(sku_candidates)
    stock_col = pick(stock_candidates)
    if not name_col or not price_col:
        raise ValueError(
            f"Не удалось определить колонки название/цена среди {fieldnames}. "
            "Поправьте detect_columns() под реальные заголовки catalog_raw.csv."
        )
    return name_col, price_col, sku_col, stock_col


def clean_catalog(in_path: str, out_path: str) -> tuple[int, int, int]:
    """Возвращает (строк_на_входе, строк_на_выходе, удалено_дублей)."""
    with open(in_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        name_col, price_col, sku_col, stock_col = detect_columns(fieldnames)
        rows = list(reader)

    total_in = len(rows)
    seen: set[tuple] = set()
    seen_prices: dict[tuple, Any] = {}
    out_rows: list[dict] = []
    dropped_empty = 0
    dropped_dupe = 0

    for i, row in enumerate(rows):
        raw_name = (row.get(name_col) or "").strip()
        raw_price = (row.get(price_col) or "").strip()
        if not raw_name and not raw_price:
            dropped_empty += 1
            continue

        raw_sku = (row.get(sku_col) or "").strip() if sku_col else ""
        cleaned = clean_row(raw_name, raw_price, raw_sku)
        stock = (row.get(stock_col) or "").strip() if stock_col else ""

        # Дедуп-ключ: по sku, когда он есть и непуст — в реальных данных это
        # надёжный идентификатор товара (одна и та же позиция выгружается
        # по нескольку раз с разным написанием названия). Если sku нет —
        # откатываемся на дедуп по названию+цене.
        dedup_key = ("sku", raw_sku.lower()) if raw_sku else (
            "name_price", cleaned["clean_name"].lower(), cleaned["price_rub"]
        )
        if dedup_key in seen:
            prev_price = seen_prices.get(dedup_key)
            if prev_price is not None and cleaned["price_rub"] not in ("", prev_price):
                logger.warning(
                    "Дубль %r с отличающейся ценой: было %s, встретилось %s — оставлена первая запись",
                    raw_sku or cleaned["clean_name"], prev_price, cleaned["price_rub"],
                )
            dropped_dupe += 1
            continue
        seen.add(dedup_key)
        seen_prices[dedup_key] = cleaned["price_rub"]

        sku = raw_sku or str(i)
        out_rows.append({"sku": sku, **cleaned, "stock": stock})

    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with out_file.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(out_rows)

    logger.info(
        "Вход: %s строк. Пустых удалено: %s. Дублей удалено: %s. На выходе: %s строк -> %s",
        total_in, dropped_empty, dropped_dupe, len(out_rows), out_file,
    )
    return total_in, len(out_rows), dropped_dupe


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="Чистка каталога Ozon")
    parser.add_argument("--in", dest="in_path", default="data/catalog_raw.csv")
    parser.add_argument("--out", dest="out_path", default="data/catalog_clean.csv")
    args = parser.parse_args()
    clean_catalog(args.in_path, args.out_path)


if __name__ == "__main__":
    main()
