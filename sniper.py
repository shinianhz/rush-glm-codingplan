# sniper.py
from __future__ import annotations

import asyncio
import logging
from enum import Enum
from dataclasses import dataclass

import httpx

from capture import PurchaseEndpoint
from utils import Config

logger = logging.getLogger("glm_rush")


class RushStatus(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    RETRY = "retry"
    ALREADY_DONE = "already_done"
    UNKNOWN = "unknown"


@dataclass
class RushResult:
    status: RushStatus
    message: str
    attempt: int = 0


def detect_api_result(status_code: int, body: dict) -> RushResult:
    """Classify an API response into a RushStatus."""
    msg_lower = str(body.get("message", "")).lower()

    if status_code == 200:
        if body.get("order_id") or body.get("success") is True:
            order_info = body.get("order_id", "")
            return RushResult(
                RushStatus.SUCCESS,
                f"购买成功! 订单号: {order_info}" if order_info else "购买成功!",
            )
        if "already" in msg_lower or "已订阅" in msg_lower or "already_subscribed" in msg_lower:
            return RushResult(RushStatus.ALREADY_DONE, "已订阅该套餐")
        if "insufficient" in msg_lower or "余额不足" in msg_lower:
            return RushResult(RushStatus.FAILED, "余额不足，购买失败")
        return RushResult(RushStatus.RETRY, f"未知成功响应: {body}")

    if status_code in (429, 502, 503, 504):
        return RushResult(RushStatus.RETRY, f"服务繁忙 ({status_code})")

    if status_code == 500:
        return RushResult(RushStatus.RETRY, f"服务器错误 ({status_code})")

    return RushResult(RushStatus.RETRY, f"未处理状态码 {status_code}: {body}")


def detect_browser_result(page_text: str) -> RushResult:
    """Classify the current browser page content."""
    text = page_text.lower()

    if "购买成功" in page_text or "payment successful" in text:
        return RushResult(RushStatus.SUCCESS, "购买成功!")
    if "已订阅" in page_text or "already subscribed" in text:
        return RushResult(RushStatus.ALREADY_DONE, "已订阅该套餐")
    if "售罄" in page_text or "sold out" in text:
        return RushResult(RushStatus.FAILED, "已售罄")
    if "余额不足" in page_text or "insufficient" in text:
        return RushResult(RushStatus.FAILED, "余额不足")

    return RushResult(RushStatus.UNKNOWN, "无法确定页面状态")


async def rush_via_api(
    client: httpx.AsyncClient,
    endpoint: PurchaseEndpoint,
    config: Config,
) -> RushResult:
    """Channel 1: Direct API purchase with retries."""
    plan_map = {"Lite": "lite", "Pro": "pro", "Max": "max"}
    plan_key = plan_map.get(config.plan, "pro")

    body = endpoint.body or {}
    body["plan"] = body.get("plan", plan_key)

    headers = endpoint.headers or {}
    headers["Content-Type"] = headers.get("Content-Type", "application/json")

    for attempt in range(1, config.max_retries + 1):
        logger.info(f"[API] 尝试 {attempt}/{config.max_retries}")
        try:
            resp = await client.request(
                endpoint.method,
                endpoint.url,
                json=body,
                headers=headers,
                timeout=10.0,
            )
            result = detect_api_result(resp.status_code, resp.json())
            result.attempt = attempt
            logger.info(f"[API] 尝试 {attempt} → {result.status.value}: {result.message}")

            if result.status in (RushStatus.SUCCESS, RushStatus.ALREADY_DONE, RushStatus.FAILED):
                return result
        except Exception as e:
            logger.warning(f"[API] 尝试 {attempt} 异常: {e}")

        await asyncio.sleep(config.retry_interval_ms / 1000)

    return RushResult(RushStatus.FAILED, "API 通道全部失败")


async def rush_via_browser(page, config: Config) -> RushResult:
    """Channel 2: Browser automation fallback."""
    import asyncio

    plan_selectors = {
        "Lite": "text=Lite",
        "Pro": "text=Pro",
        "Max": "text=Max",
    }

    for attempt in range(1, config.max_retries + 1):
        logger.info(f"[浏览器] 尝试 {attempt}/{config.max_retries}")
        try:
            await page.reload(wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(0.5)

            plan_btn = page.locator(plan_selectors.get(config.plan, "text=Pro")).first
            await plan_btn.click(timeout=5000)
            await asyncio.sleep(0.3)

            purchase_btn = page.get_by_role("button", name="立即订阅").or_(
                page.get_by_role("button", name="购买")
            ).or_(
                page.get_by_role("button", name="立即购买")
            ).first
            await purchase_btn.click(timeout=5000)
            await asyncio.sleep(1)

            page_text = await page.inner_text("body")
            result = detect_browser_result(page_text)
            result.attempt = attempt
            logger.info(f"[浏览器] 尝试 {attempt} → {result.status.value}: {result.message}")

            if result.status in (RushStatus.SUCCESS, RushStatus.ALREADY_DONE, RushStatus.FAILED):
                return result

        except Exception as e:
            logger.warning(f"[浏览器] 尝试 {attempt} 异常: {e}")

        await asyncio.sleep(config.retry_interval_ms / 1000)

    return RushResult(RushStatus.FAILED, "浏览器通道全部失败")
