# tests/test_capture.py
import pytest
from capture import detect_purchase_endpoint, PurchaseEndpoint


class TestDetectPurchaseEndpoint:
    def test_identifies_subscribe_endpoint(self):
        requests = [
            {"url": "https://bigmodel.cn/api/paas/v4/user/info", "method": "GET"},
            {"url": "https://bigmodel.cn/api/paas/v4/subscribe/order", "method": "POST"},
            {"url": "https://bigmodel.cn/api/paas/v4/models", "method": "GET"},
        ]
        result = detect_purchase_endpoint(requests)
        assert result is not None
        assert result.url == "https://bigmodel.cn/api/paas/v4/subscribe/order"
        assert result.method == "POST"

    def test_identifies_purchase_endpoint(self):
        requests = [
            {"url": "https://bigmodel.cn/api/paas/v4/plan/buy", "method": "POST"},
        ]
        result = detect_purchase_endpoint(requests)
        assert result is not None
        assert result.url == "https://bigmodel.cn/api/paas/v4/plan/buy"

    def test_returns_none_when_no_match(self):
        requests = [
            {"url": "https://bigmodel.cn/api/paas/v4/user/info", "method": "GET"},
            {"url": "https://bigmodel.cn/api/paas/v4/models", "method": "GET"},
        ]
        result = detect_purchase_endpoint(requests)
        assert result is None

    def test_prefers_post_over_get(self):
        requests = [
            {"url": "https://bigmodel.cn/api/paas/v4/subscribe/list", "method": "GET"},
            {"url": "https://bigmodel.cn/api/paas/v4/subscribe/order", "method": "POST"},
        ]
        result = detect_purchase_endpoint(requests)
        assert result.method == "POST"

    def test_identifies_order_endpoint(self):
        requests = [
            {"url": "https://bigmodel.cn/api/order/create", "method": "POST"},
        ]
        result = detect_purchase_endpoint(requests)
        assert result is not None
