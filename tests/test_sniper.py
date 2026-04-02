# tests/test_sniper.py
import asyncio

import pytest

from capture import PurchaseEndpoint
from sniper import detect_api_result, rush_direct, rush_via_api, RushStatus
from utils import Config


class TestDetectApiResult:
    def test_success_with_order_id(self):
        result = detect_api_result(200, {"order_id": "ORD-123", "message": "ok"})
        assert result.status == RushStatus.SUCCESS
        assert "ORD-123" in result.message

    def test_success_with_success_flag(self):
        result = detect_api_result(200, {"success": True})
        assert result.status == RushStatus.SUCCESS

    def test_already_subscribed(self):
        result = detect_api_result(200, {"message": "already subscribed"})
        assert result.status == RushStatus.ALREADY_DONE

    def test_insufficient_balance(self):
        result = detect_api_result(200, {"message": "insufficient balance"})
        assert result.status == RushStatus.FAILED

    def test_server_busy(self):
        result = detect_api_result(503, {})
        assert result.status == RushStatus.RETRY

    def test_rate_limited(self):
        result = detect_api_result(429, {})
        assert result.status == RushStatus.RETRY

    def test_unknown_error(self):
        result = detect_api_result(500, {"error": "internal"})
        assert result.status == RushStatus.RETRY

    def test_auth_failed_401(self):
        body = {"error": {"code": "1001", "message": "Header中未收到Authorization参数"}}
        result = detect_api_result(401, body)
        assert result.status == RushStatus.FAILED
        assert "401" in result.message
        assert "Authorization" in result.message

    def test_created_response_is_treated_as_success(self):
        result = detect_api_result(201, {"success": True})
        assert result.status == RushStatus.SUCCESS


class RecordingClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.response


class ResponseStub:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload


class TestRequestBehavior:
    def test_rush_via_api_preserves_captured_authorization_header(self):
        endpoint = PurchaseEndpoint(
            url="https://bigmodel.cn/api/paas/v4/subscribe",
            method="POST",
            headers={"Authorization": "Bearer captured-token"},
            body={"plan": "pro"},
        )
        config = Config(
            phone="13800138000",
            password="secret",
            plan="Pro",
            target_time="10:00:00",
            max_retries=1,
        )
        client = RecordingClient(ResponseStub(200, {"success": True}))

        result = asyncio.run(
            rush_via_api(
                client,
                endpoint,
                config,
                extra_headers={"Origin": "https://bigmodel.cn"},
            )
        )

        assert result.status == RushStatus.SUCCESS
        assert client.calls[0]["headers"]["Authorization"] == "Bearer captured-token"

    def test_rush_direct_get_uses_query_params_not_json_body(self):
        config = Config(
            phone="13800138000",
            password="secret",
            plan="Pro",
            target_time="10:00:00",
            max_retries=1,
        )
        client = RecordingClient(ResponseStub(200, {"success": True}))

        result = asyncio.run(
            rush_direct(
                client,
                "https://bigmodel.cn/api/paas/v4/subscribe",
                "GET",
                {"foo": "bar"},
                config,
            )
        )

        assert result.status == RushStatus.SUCCESS
        assert client.calls[0]["method"] == "GET"
        assert client.calls[0]["params"]["foo"] == "bar"
        assert "json" not in client.calls[0]
