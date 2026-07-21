"""
Задача 1. Выгрузка товаров продавца (Ozon Seller API) в CSV.

Запуск:
    python export_products.py [--out data/products_export.csv]

Логика:
1. Получаем список товаров (product_id, offer_id) с пагинацией через
   /v3/product/list (курсор last_id).
2. Батчами получаем цены (/v5/product/info/prices) и остатки
   (/v4/product/info/stocks) по product_id.
3. Склеиваем всё в один CSV с колонкой даты выгрузки.

Ключи и настройки берутся из .env (см. README), в коде не хардкодятся.
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from ozon_client import OzonClient

logger = logging.getLogger("export_products")

FIELDNAMES = ["offer_id", "product_id", "name", "price", "old_price", "stock", "export_date"]


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
            logging.FileHandler("logs/export_products.log", encoding="utf-8"),
        ],
    )


def _extract_price(price_info: dict) -> tuple[str, str]:
    """v5/product/info/prices оборачивает цену в price_info['price'] с 12.2024."""
    price_obj = price_info.get("price", price_info)
    return price_obj.get("price", ""), price_obj.get("old_price", "")


def _extract_stock(stock_info: dict) -> str:
    """v4/product/info/stocks отдаёт массив stocks (по складам/схемам fbo/fbs).

    Суммируем present по всем записям — это доступный остаток к продаже.
    """
    stocks = stock_info.get("stocks", [])
    if not stocks:
        return "0"
    total_present = sum(int(s.get("present", 0)) for s in stocks)
    return str(total_present)


def export_products(out_path: str) -> int:
    client = OzonClient()

    products = list(client.iter_product_ids())
    logger.info("Всего товаров в списке: %s", len(products))
    if not products:
        logger.warning("Список товаров пуст — проверьте Client-Id/Api-Key и права доступа.")

    product_ids = [p["product_id"] for p in products]
    infos = client.get_product_info(product_ids)
    prices = client.get_prices(product_ids)
    stocks = client.get_stocks(product_ids)

    export_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with out_file.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for p in products:
            pid = p["product_id"]
            info = infos.get(pid, {})
            price_info = prices.get(pid, {})
            stock_info = stocks.get(pid, {})
            price, old_price = _extract_price(price_info)
            row = {
                "offer_id": p.get("offer_id", ""),
                "product_id": pid,
                "name": info.get("name", ""),
                "price": price,
                "old_price": old_price,
                "stock": _extract_stock(stock_info),
                "export_date": export_date,
            }
            writer.writerow(row)

    logger.info("Готово: %s товаров сохранено в %s", len(products), out_file)
    return len(products)


def main() -> None:
    load_dotenv()
    setup_logging()
    parser = argparse.ArgumentParser(description="Выгрузка товаров Ozon в CSV")
    parser.add_argument("--out", default="data/products_export.csv", help="Путь к выходному CSV")
    args = parser.parse_args()
    try:
        export_products(args.out)
    except Exception:
        logger.exception("Выгрузка завершилась с ошибкой")
        raise


if __name__ == "__main__":
    main()
