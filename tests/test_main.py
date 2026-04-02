import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import main
from utils import Config, PURCHASE_URL


class TestExecuteRush:
    def test_calls_purchase_with_correct_product_id(self, monkeypatch):
        config = Config(
            phone="13800138000",
            password="secret",
            plan="Pro",
            target_time="10:00:00",
            max_retries=1,
        )
        session = SimpleNamespace(
            cookies=[],
            auth_token="test-jwt-token",
            endpoint=None,
            site_headers={
                "bigmodel-organization": "org-test123",
                "bigmodel-project": "proj-test456",
            },
        )

        captured = {}

        async def fake_do_purchase(client, url, auth_token, site_headers, product_id, cfg):
            captured.update({
                "url": url,
                "auth_token": auth_token,
                "site_headers": site_headers,
                "product_id": product_id,
            })
            from sniper import RushStatus
            return RushStatus.SUCCESS, "购买成功!"

        monkeypatch.setattr(main, "httpx_cookies_from_playwright", lambda rows: {})
        monkeypatch.setattr(main, "_do_purchase", fake_do_purchase)

        result = asyncio.run(main.execute_rush(config, session))

        assert result is True
        assert captured["url"] == PURCHASE_URL
        assert captured["auth_token"] == "test-jwt-token"
        assert captured["site_headers"]["bigmodel-organization"] == "org-test123"
        assert captured["product_id"] == "product-1df3e1"  # Pro default

    def test_fails_without_product_id(self, monkeypatch):
        config = Config(
            phone="13800138000",
            password="secret",
            plan="UnknownPlan",
            target_time="10:00:00",
            max_retries=1,
            product_id="",
        )
        session = SimpleNamespace(
            cookies=[],
            auth_token="token",
            endpoint=None,
            site_headers={},
        )

        monkeypatch.setattr(main, "httpx_cookies_from_playwright", lambda rows: {})

        result = asyncio.run(main.execute_rush(config, session))
        assert result is False

    def test_uses_explicit_product_id(self, monkeypatch):
        config = Config(
            phone="13800138000",
            password="secret",
            plan="Pro",
            target_time="10:00:00",
            max_retries=1,
            product_id="product-custom123",
        )
        session = SimpleNamespace(
            cookies=[],
            auth_token="token",
            endpoint=None,
            site_headers={},
        )

        captured = {}

        async def fake_do_purchase(client, url, auth_token, site_headers, product_id, cfg):
            captured["product_id"] = product_id
            from sniper import RushStatus
            return RushStatus.SUCCESS, "ok"

        monkeypatch.setattr(main, "httpx_cookies_from_playwright", lambda rows: {})
        monkeypatch.setattr(main, "_do_purchase", fake_do_purchase)

        result = asyncio.run(main.execute_rush(config, session))
        assert result is True
        assert captured["product_id"] == "product-custom123"
