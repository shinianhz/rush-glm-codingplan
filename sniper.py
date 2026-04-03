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


def _api_error_message(body: dict) -> str:
    m = body.get("message")
    if isinstance(m, str) and m.strip():
        return m
    err = body.get("error")
    if isinstance(err, dict):
        em = err.get("message")
        if isinstance(em, str) and em.strip():
            return em
    return str(body)


def detect_api_result(status_code: int, body: dict) -> RushResult:
    """Classify an API response into a RushStatus."""
    msg_lower = str(body.get("message", "")).lower()

    if status_code in (401, 403):
        return RushResult(
            RushStatus.FAILED,
            f"鉴权失败 ({status_code}): {_api_error_message(body)}",
        )

    if 200 <= status_code < 300:
        # Check inner data for soldOut / actual purchase confirmation
        data = body.get("data")
        print(" api response data:", data)

        if isinstance(data, dict):
            if data.get("soldOut") is True:
                
                return RushResult(RushStatus.FAILED, "商品已售罄")
            # Real purchase success: has payAmount or cashAmount (non-null)
            has_payment = data.get("payAmount") is not None or data.get("cashAmount") is not None
            if has_payment and body.get("success") is True:
                return RushResult(RushStatus.SUCCESS, f"购买成功! {data}")
        elif body.get("order_id"):
            return RushResult(RushStatus.SUCCESS, f"购买成功! 订单号: {body['order_id']}")

        # Top-level code/msg checks for error messages even on HTTP 200
        code = body.get("code")
        api_msg = str(body.get("msg", ""))
        if code == 500 and "不支持购买" in api_msg:
            return RushResult(RushStatus.RETRY, f"套餐暂不可购买: {api_msg}")
        if code == 555 or "系统繁忙" in api_msg:
            return RushResult(RushStatus.RETRY, f"系统繁忙: {api_msg}")

        if "already" in msg_lower or "已订阅" in msg_lower or "already_subscribed" in msg_lower:
            return RushResult(RushStatus.ALREADY_DONE, "已订阅该套餐")
        if "insufficient" in msg_lower or "余额不足" in msg_lower:
            return RushResult(RushStatus.FAILED, "余额不足，购买失败")

        # success:true but no data — ambiguous, retry
        if body.get("success") is True:
            return RushResult(RushStatus.RETRY, f"响应成功但无购买确认: {body}")

        return RushResult(RushStatus.RETRY, f"未知成功响应: {body}")

    if status_code in (429, 502, 503, 504):
        return RushResult(RushStatus.RETRY, f"服务繁忙 ({status_code})")

    if status_code == 500:
        return RushResult(RushStatus.RETRY, f"服务器错误 ({status_code})")

    return RushResult(RushStatus.RETRY, f"未处理状态码 {status_code}: {body}")


# Headers safe to forward from captured requests
_SAFE_CAPTURED_HEADERS = {"authorization", "content-type", "accept", "x-requested-with"}


def _clean_captured_headers(raw: dict[str, str] | None) -> dict[str, str]:
    """Whitelist captured headers to avoid stale content-length, host, etc."""
    if not raw:
        return {}
    return {k: v for k, v in raw.items() if k.lower() in _SAFE_CAPTURED_HEADERS}


def _drop_authorization(headers: dict[str, str]) -> None:
    """Remove any Authorization key (any casing) so extra_headers can set one canonical Bearer."""
    for k in list(headers.keys()):
        if k.lower() == "authorization":
            del headers[k]


def _has_authorization(headers: dict[str, str] | None) -> bool:
    if not headers:
        return False
    return any(k.lower() == "authorization" for k in headers)


async def rush_via_api(
    client: httpx.AsyncClient,
    endpoint: PurchaseEndpoint,
    config: Config,
    extra_headers: dict[str, str] | None = None,
) -> RushResult:
    """Pure API purchase with retries."""
    plan_map = {"Lite": "lite", "Pro": "pro", "Max": "max"}
    plan_key = plan_map.get(config.plan, "pro")

    body = {**(endpoint.body or {})}
    body["plan"] = body.get("plan", plan_key)

    headers = _clean_captured_headers(endpoint.headers)
    headers["Content-Type"] = "application/json"
    if extra_headers:
        if _has_authorization(extra_headers):
            _drop_authorization(headers)
        headers.update(extra_headers)

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
            result = detect_api_result(resp.status_code, parse_response_json(resp))
            result.attempt = attempt
            logger.info(f"[API] 尝试 {attempt} → {result.status.value}: {result.message}")

            if result.status in (RushStatus.SUCCESS, RushStatus.ALREADY_DONE, RushStatus.FAILED):
                return result
        except httpx.TimeoutException:
            logger.warning(f"[API] 尝试 {attempt} 超时")
        except Exception as e:
            logger.warning(f"[API] 尝试 {attempt} 异常: {e}")

        await asyncio.sleep(config.retry_interval_ms / 1000)

    return RushResult(RushStatus.FAILED, "API 通道全部失败")


def parse_response_json(resp: httpx.Response) -> dict:
    try:
        return resp.json()
    except Exception:
        text = (resp.text or "")[:500]
        return {"message": f"非 JSON 响应: {text!r}"}


async def rush_direct(
    client: httpx.AsyncClient,
    url: str,
    method: str,
    body: dict | None,
    config: Config,
    extra_headers: dict[str, str] | None = None,
) -> RushResult:
    """Direct API purchase using config-provided URL (no captured endpoint needed)."""
    plan_map = {"Lite": "lite", "Pro": "pro", "Max": "max"}
    plan_key = plan_map.get(config.plan, "pro")

    normalized_method = method.upper()
    request_body = {**(body or {})}
    request_body["plan"] = request_body.get("plan", plan_key)

    headers = {"Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)

    for attempt in range(1, config.max_retries + 1):
        logger.info(f"[API] 尝试 {attempt}/{config.max_retries}")
        try:
            request_kwargs = {
                "headers": headers,
                "timeout": 10.0,
            }
            if normalized_method == "GET":
                request_kwargs["params"] = request_body
            else:
                request_kwargs["json"] = request_body

            resp = await client.request(normalized_method, url, **request_kwargs)
            result = detect_api_result(resp.status_code, parse_response_json(resp))
            result.attempt = attempt
            logger.info(f"[API] 尝试 {attempt} → {result.status.value}: {result.message}")

            if result.status in (RushStatus.SUCCESS, RushStatus.ALREADY_DONE, RushStatus.FAILED):
                return result
        except httpx.TimeoutException:
            logger.warning(f"[API] 尝试 {attempt} 超时")
        except Exception as e:
            logger.warning(f"[API] 尝试 {attempt} 异常: {e}")

        await asyncio.sleep(config.retry_interval_ms / 1000)

    return RushResult(RushStatus.FAILED, "API 通道全部失败")
