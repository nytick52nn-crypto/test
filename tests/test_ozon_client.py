import os

import pytest

os.environ.setdefault("OZON_CLIENT_ID", "test-client")
os.environ.setdefault("OZON_API_KEY", "test-key")

from ozon_client import OzonApiError, OzonClient


class FakeResponse:
    def __init__(self, status_code, json_data=None, headers=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.headers = headers or {}
        self.text = text
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._json


@pytest.fixture
def client():
    return OzonClient(client_id="c", api_key="k", max_retries=3, backoff_base=1.0)


def test_post_success_no_retry(client, monkeypatch):
    monkeypatch.setattr(client.session, "post", lambda *a, **k: FakeResponse(200, {"result": {"items": []}}))
    data = client._post("/v3/product/list", {})
    assert data == {"result": {"items": []}}


def test_post_retries_on_429_then_succeeds(client, monkeypatch):
    calls = {"n": 0}

    def fake_post(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeResponse(429, headers={"Retry-After": "0"})
        return FakeResponse(200, {"result": {"items": [1]}})

    monkeypatch.setattr(client.session, "post", fake_post)
    monkeypatch.setattr("ozon_client.time.sleep", lambda _s: None)

    data = client._post("/v3/product/list", {})
    assert calls["n"] == 2
    assert data == {"result": {"items": [1]}}


def test_post_gives_up_after_max_retries(client, monkeypatch):
    monkeypatch.setattr(client.session, "post", lambda *a, **k: FakeResponse(429))
    monkeypatch.setattr("ozon_client.time.sleep", lambda _s: None)

    with pytest.raises(OzonApiError):
        client._post("/v3/product/list", {})


def test_post_retries_on_5xx(client, monkeypatch):
    calls = {"n": 0}

    def fake_post(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeResponse(503)
        return FakeResponse(200, {"result": {}})

    monkeypatch.setattr(client.session, "post", fake_post)
    monkeypatch.setattr("ozon_client.time.sleep", lambda _s: None)

    data = client._post("/v3/product/list", {})
    assert calls["n"] == 2
    assert data == {"result": {}}


def test_post_raises_on_other_http_error(client, monkeypatch):
    monkeypatch.setattr(client.session, "post", lambda *a, **k: FakeResponse(404, text="not found"))
    with pytest.raises(OzonApiError):
        client._post("/v3/product/list", {})


def test_iter_product_ids_paginates_with_cursor(client, monkeypatch):
    pages = [
        {"result": {"items": [{"product_id": 1}, {"product_id": 2}], "last_id": "cursor1"}},
        {"result": {"items": [{"product_id": 3}], "last_id": ""}},
    ]

    def fake_post(path, payload):
        assert path == "/v3/product/list"
        return pages.pop(0)

    monkeypatch.setattr(client, "_post", fake_post)
    items = list(client.iter_product_ids())
    assert [i["product_id"] for i in items] == [1, 2, 3]
