"""
Тонкий клиент для Ozon Seller API.

Почему так:
- Ozon переводит методы товаров на курсорную пагинацию (last_id / cursor),
  а не offset. Это подтверждено официальными объявлениями об устаревании
  /v2/product/list, /v2/product/info, /v2/product/info/list и переходе на
  /v3/product/list, /v3/product/info/list, /v5/product/info/prices,
  /v4/product/info/stocks (см. README, раздел "Как проверяли методы").
- HTTP 429 и 5xx обрабатываются экспоненциальным бэкоффом с уважением
  заголовка Retry-After, если он есть.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Iterable, Iterator

import requests

logger = logging.getLogger("ozon_client")


class OzonApiError(Exception):
    """Ошибка ответа Ozon Seller API (не связанная с сетевым ретраем)."""


class OzonClient:
    def __init__(
        self,
        client_id: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 5,
        backoff_base: float = 1.5,
    ) -> None:
        self.client_id = client_id or os.environ["OZON_CLIENT_ID"]
        self.api_key = api_key or os.environ["OZON_API_KEY"]
        # Позволяет переопределить хост для тестового кабинета через .env,
        # если Ozon выдаст отдельный адрес тестовой среды.
        self.base_url = (base_url or os.environ.get("OZON_BASE_URL", "https://api-seller.ozon.ru")).rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Client-Id": self.client_id,
                "Api-Key": self.api_key,
                "Content-Type": "application/json",
            }
        )

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = self.session.post(url, json=payload, timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt > self.max_retries:
                    logger.error("Сетевая ошибка после %s попыток: %s", attempt, exc)
                    raise
                sleep_for = self.backoff_base ** attempt
                logger.warning("Сетевая ошибка (%s), повтор через %.1fс (попытка %s/%s)", exc, sleep_for, attempt, self.max_retries)
                time.sleep(sleep_for)
                continue

            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt > self.max_retries:
                    raise OzonApiError(f"{path}: превышен лимит попыток, последний статус {resp.status_code}: {resp.text[:500]}")
                retry_after = resp.headers.get("Retry-After")
                sleep_for = float(retry_after) if retry_after else self.backoff_base ** attempt
                logger.warning(
                    "%s ответил %s, повтор через %.1fс (попытка %s/%s)",
                    path, resp.status_code, sleep_for, attempt, self.max_retries,
                )
                time.sleep(sleep_for)
                continue

            if not resp.ok:
                raise OzonApiError(f"{path}: HTTP {resp.status_code}: {resp.text[:1000]}")

            try:
                return resp.json()
            except ValueError as exc:
                raise OzonApiError(f"{path}: некорректный JSON в ответе: {exc}")

    # ------------------------------------------------------------------
    # Товары
    # ------------------------------------------------------------------
    def iter_product_ids(self, visibility: str = "ALL", limit: int = 1000) -> Iterator[dict[str, Any]]:
        """Курсорная пагинация по /v3/product/list.

        Возвращает элементы {"product_id":..., "offer_id":...} по одному,
        сам разруливая курсор last_id, пока Ozon не перестанет его отдавать.
        """
        last_id = ""
        while True:
            payload = {
                "filter": {"visibility": visibility},
                "limit": limit,
                "last_id": last_id,
            }
            data = self._post("/v3/product/list", payload)
            result = data.get("result", data)  # на случай другой обёртки ответа
            items = result.get("items", [])
            logger.info("Получено %s товаров (last_id=%r)", len(items), last_id)
            for item in items:
                yield item
            last_id = result.get("last_id", "")
            if not items or not last_id:
                break

    def get_prices(self, product_ids: Iterable[int], batch_size: int = 100) -> dict[int, dict[str, Any]]:
        """POST /v5/product/info/prices батчами по product_id.

        Возвращает {product_id: price_info}.
        """
        out: dict[int, dict[str, Any]] = {}
        for batch in _chunked(list(product_ids), batch_size):
            cursor = ""
            while True:
                payload = {
                    "cursor": cursor,
                    "filter": {"product_id": [str(pid) for pid in batch], "visibility": "ALL"},
                    "limit": batch_size,
                }
                data = self._post("/v5/product/info/prices", payload)
                result = data.get("result", data)
                items = result.get("items", [])
                for item in items:
                    out[int(item["product_id"])] = item
                cursor = result.get("cursor", "")
                if not items or not cursor:
                    break
        return out

    def get_product_info(self, product_ids: Iterable[int], batch_size: int = 1000) -> dict[int, dict[str, Any]]:
        """POST /v3/product/info/list батчами по product_id.

        Не курсорный метод — просто передаём список id (до 1000 за раз) и
        получаем карточки товаров, отсюда берём name.
        Возвращает {product_id: item}.
        """
        out: dict[int, dict[str, Any]] = {}
        for batch in _chunked(list(product_ids), batch_size):
            payload = {"product_id": [str(pid) for pid in batch], "offer_id": [], "sku": []}
            data = self._post("/v3/product/info/list", payload)
            result = data.get("result", data)
            items = result.get("items", result) if isinstance(result, dict) else result
            for item in items:
                pid = item.get("id") or item.get("product_id")
                if pid is not None:
                    out[int(pid)] = item
        return out

    def get_stocks(self, product_ids: Iterable[int], batch_size: int = 100) -> dict[int, dict[str, Any]]:
        """POST /v4/product/info/stocks батчами по product_id.

        Возвращает {product_id: stocks_info}.
        """
        out: dict[int, dict[str, Any]] = {}
        for batch in _chunked(list(product_ids), batch_size):
            cursor = ""
            while True:
                payload = {
                    "cursor": cursor,
                    "filter": {"product_id": [str(pid) for pid in batch], "visibility": "ALL"},
                    "limit": batch_size,
                }
                data = self._post("/v4/product/info/stocks", payload)
                result = data.get("result", data)
                items = result.get("items", [])
                for item in items:
                    out[int(item["product_id"])] = item
                cursor = result.get("cursor", "")
                if not items or not cursor:
                    break
        return out


def _chunked(seq: list[Any], size: int) -> Iterator[list[Any]]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]
