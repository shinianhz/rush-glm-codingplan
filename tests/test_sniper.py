# tests/test_sniper.py
import pytest
from sniper import detect_api_result, detect_browser_result, RushResult, RushStatus


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


class TestDetectBrowserResult:
    def test_success_text_found(self):
        result = detect_browser_result("购买成功！感谢您的支持")
        assert result.status == RushStatus.SUCCESS

    def test_already_subscribed_text(self):
        result = detect_browser_result("您已订阅该套餐")
        assert result.status == RushStatus.ALREADY_DONE

    def test_sold_out_text(self):
        result = detect_browser_result("已售罄")
        assert result.status == RushStatus.FAILED

    def test_unknown_page(self):
        result = detect_browser_result("GLM Coding Plan")
        assert result.status == RushStatus.UNKNOWN
