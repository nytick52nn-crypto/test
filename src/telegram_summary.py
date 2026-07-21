"""
Задача 2. Утренняя сводка в Telegram на основе выгрузки из задачи 1.

Запуск:
    python export_products.py                 # сначала обновить выгрузку
    python telegram_summary.py                 # затем отправить сводку

Или одной командой:
    python telegram_summary.py --run-export

Логика:
1. Читаем CSV выгрузки (по умолчанию data/products_export.csv).
2. Для каждого товара формируем строку: название, цена, остаток.
3. Если остаток < LOW_STOCK_THRESHOLD (из .env) — помечаем «заканчивается».
4. Собираем сообщение (с учётом лимита Telegram ~4096 символов на сообщение,
   при необходимости бьём на несколько сообщений) и отправляем через
   Bot API методом sendMessage.

Токен бота и chat_id — из .env, в коде не хардкодятся.
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

logger = logging.getLogger("telegram_summary")

TELEGRAM_MESSAGE_LIMIT = 4096


def setup_logging() -> None:
    # На Windows консоль часто в cp1251: кириллица в логах превращается в
    # кракозябры, а эмодзи в сводке (📦/⚠️) вообще роняют print() с
    # UnicodeEncodeError. Переключаем stdout/stderr на utf-8 явно.
    for stream in (sys.stdout, sys.stderr):
        if stream.encoding and stream.encoding.lower() != "utf-8":
            stream.reconfigure(encoding="utf-8", errors="replace")
    Path("logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("logs/telegram_summary.log", encoding="utf-8"),
        ],
    )


def read_products(csv_path: str) -> list[dict[str, str]]:
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def build_message_lines(rows: list[dict[str, str]], threshold: int) -> tuple[list[str], list[str]]:
    """Возвращает (обычные_строки, строки_с_алертом)."""
    normal, low = [], []
    for row in rows:
        name = row.get("name") or row.get("offer_id") or "без названия"
        price = row.get("price") or "—"
        try:
            stock = int(float(row.get("stock") or 0))
        except ValueError:
            stock = 0
        line = f"• {name} — {price} ₽, остаток: {stock}"
        if stock < threshold:
            low.append(f"{line} ⚠️ заканчивается")
        else:
            normal.append(line)
    return normal, low


def compose_report(rows: list[dict[str, str]], threshold: int) -> str:
    normal, low = build_message_lines(rows, threshold)
    export_date = rows[0].get("export_date", "") if rows else ""
    parts = [f"📦 Сводка по товарам ({export_date})", f"Всего товаров: {len(rows)}"]
    if low:
        parts.append(f"\n⚠️ Заканчиваются ({len(low)}):")
        parts.extend(low)
    if normal:
        parts.append(f"\nОстальные товары ({len(normal)}):")
        parts.extend(normal)
    return "\n".join(parts)


def split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Режем по границам строк, чтобы уложиться в лимит Telegram на сообщение."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.split("\n"):
        # +1 за перевод строки
        if current_len + len(line) + 1 > limit and current:
            chunks.append("\n".join(current))
            current, current_len = [], 0
        current.append(line)
        current_len += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def send_telegram_message(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=15)
    if not resp.ok:
        raise RuntimeError(f"Telegram API вернул ошибку {resp.status_code}: {resp.text[:500]}")


def main() -> None:
    load_dotenv()
    setup_logging()

    parser = argparse.ArgumentParser(description="Утренняя сводка по товарам в Telegram")
    parser.add_argument("--csv", default="data/products_export.csv", help="Путь к CSV из задачи 1")
    parser.add_argument("--run-export", action="store_true", help="Сначала запустить export_products.py")
    parser.add_argument("--dry-run", action="store_true", help="Только вывести сообщение в консоль, не отправлять")
    args = parser.parse_args()

    if args.run_export:
        import export_products

        export_products.export_products(args.csv)

    threshold = int(os.environ.get("LOW_STOCK_THRESHOLD", "5"))

    rows = read_products(args.csv)
    if not rows:
        logger.warning("CSV %s пуст — нечего отправлять", args.csv)
        return

    report = compose_report(rows, threshold)

    if args.dry_run:
        print(report)
        return

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    for chunk in split_message(report):
        send_telegram_message(token, chat_id, chunk)
    logger.info("Сводка отправлена (%s товаров, порог низкого остатка: %s)", len(rows), threshold)


if __name__ == "__main__":
    main()
