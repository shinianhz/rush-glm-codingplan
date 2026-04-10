# main.py
from __future__ import annotations

import asyncio
import json
import sys
import logging
import time

import httpx
from rich.console import Console
from rich.panel import Panel

from utils import (
    httpx_cookies_from_playwright,
    load_config,
    mask_phone,
    seconds_until_target,
    setup_logger,
    Config,
    PURCHASE_URL,
)
from login import LoginSession, perform_login
from sniper import (
    rush_via_api,
    rush_direct,
    RushStatus,
    parse_response_json,
)

console = Console()


def print_banner(config: Config):
    phone_masked = mask_phone(config.phone)
    pid = config.resolved_product_id or "未配置"
    console.print(Panel.fit(
        f"[bold]GLM Coding Plan Rush (并发 API)[/bold]\n"
        f"档位: [cyan]{config.plan}[/cyan]\n"
        f"手机: [dim]{phone_masked}[/dim]\n"
        f"目标时间: [cyan]{config.target_time}[/cyan]\n"
        f"并发数: [cyan]{config.concurrency}[/cyan]\n"
        f"抢购窗口: [cyan]{config.rush_duration_s}s[/cyan]\n"
        f"产品ID: [cyan]{pid}[/cyan]\n"
        f"购买端点: [cyan]{PURCHASE_URL}[/cyan]",
        title="[1/4] 配置已加载",
        border_style="green",
    ))


async def wait_for_target(config: Config) -> bool:
    """Phase 2: Countdown with adaptive sleep, pre-warm, and precision busy-wait."""
    console.print("\n[3/4] 等待开售...")
    prewarmed = False

    while True:
        secs = seconds_until_target(config.target_time)

        if secs <= -60:
            console.print("[red]已过目标时间![/red]")
            return False

        if secs <= 0:
            console.print("[bold red]开火![/bold red]")
            return True

        mins, s = divmod(int(secs), 60)
        hrs, mins = divmod(mins, 60)

        console.print(
            f"\r      倒计时 {hrs:02d}:{mins:02d}:{s:02d}    ",
            end="",
        )

        # Final precision window: busy-wait
        if secs <= 1.5:
            while seconds_until_target(config.target_time) > 0:
                pass
            return True

        # Pre-warm connection once at ~3s before target
        if secs <= 3 and not prewarmed:
            prewarmed = True
            console.print("\n      预热连接...")
            try:
                async with httpx.AsyncClient(http2=True) as client:
                    await client.head("https://bigmodel.cn", timeout=2)
            except Exception:
                pass
            continue

        # Adaptive sleep: always wake up at least 3.5s before target
        sleep_time = min(5.0, max(0.1, secs - 3.5))
        await asyncio.sleep(sleep_time)


async def _single_request(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    body: dict,
    worker_id: int,
    attempt: int,
) -> tuple[RushStatus, str]:
    """Fire a single preview request. Preview = final payment for balance.

    POST /pay/preview with payType=BALANCE directly deducts balance and
    completes the purchase. No submit/create-sign step needed.
    """
    logger = logging.getLogger("glm_rush")
    try:
        resp = await client.request("POST", url, json=body, headers=headers, timeout=10.0)
        resp_body = parse_response_json(resp)
        logger.info(f"[W{worker_id}] #{attempt} PREVIEW → {resp.status_code}: {json.dumps(resp_body, ensure_ascii=False)[:2000]}")

        # Auth errors
        if resp.status_code in (401, 403):
            return RushStatus.FAILED, f"鉴权失败 ({resp.status_code}): {resp_body}"

        # Server busy / rate limit
        if resp.status_code in (429, 502, 503, 504):
            return RushStatus.RETRY, f"服务繁忙 ({resp.status_code})"
        if resp.status_code == 500:
            return RushStatus.RETRY, f"服务器错误: {resp_body}"

        if not (200 <= resp.status_code < 300):
            return RushStatus.RETRY, f"状态码 {resp.status_code}: {resp_body}"

        # HTTP 200 — classify response
        data = resp_body.get("data") or {}
        msg = str(resp_body.get("msg", ""))
        code = resp_body.get("code")

        # System busy (555 code)
        if code == 555 or "系统繁忙" in msg:
            return RushStatus.RETRY, f"系统繁忙: {msg}"

        # Sold out
        if isinstance(data, dict) and data.get("soldOut") is True:
            return RushStatus.RETRY, "商品已售罄"

        # Already subscribed
        if "已订阅" in msg or "already" in msg.lower():
            return RushStatus.ALREADY_DONE, f"已订阅: {msg}"

        # Insufficient balance
        if "余额不足" in msg or "insufficient" in msg.lower():
            return RushStatus.FAILED, f"余额不足: {msg}"

        # Purchase success: code 200 + success true + has bizId
        if code == 200 and resp_body.get("success") is True and isinstance(data, dict):
            biz_id = data.get("bizId", "")
            if biz_id:
                console.print(f"      [bold green]W{worker_id} #{attempt}: 购买成功! bizId={biz_id[:12]}...[/bold green]")
                return RushStatus.SUCCESS, f"购买成功! bizId={biz_id}, 金额={data.get('payAmount', '?')}"

        # Retry on ambiguous response
        return RushStatus.RETRY, f"未确认响应: code={code}, msg={msg}"

    except httpx.TimeoutException:
        logger.debug(f"[W{worker_id}] #{attempt} 超时")
        return RushStatus.RETRY, "超时"
    except httpx.ConnectError as e:
        logger.debug(f"[W{worker_id}] #{attempt} 连接失败: {e}")
        return RushStatus.RETRY, f"连接失败: {e}"
    except Exception as e:
        logger.debug(f"[W{worker_id}] #{attempt} 异常: {type(e).__name__}: {e}")
        return RushStatus.RETRY, f"{type(e).__name__}: {e}"


async def _do_purchase(
    client: httpx.AsyncClient,
    url: str,
    auth_token: str,
    site_headers: dict[str, str],
    product_id: str,
    config: Config,
) -> tuple[RushStatus, str]:
    """Concurrent rush: N workers fire preview requests in parallel for rush_duration_s seconds."""
    logger = logging.getLogger("glm_rush")

    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://bigmodel.cn",
        "Referer": "https://bigmodel.cn/glm-coding",
        "set-language": "zh",
    }
    if auth_token:
        headers["Authorization"] = auth_token
    headers.update(site_headers)

    body = {
        "productId": product_id,
        "payType": "BALANCE",
        "quantity": 1,
        "cycle": "month",
        "autoRenew": False,
        "isUpgrade": False,
    }

    safe_headers = {k: (v[:20] + "..." if k.lower() == "authorization" and len(v) > 20 else v)
                    for k, v in headers.items()}
    logger.info(f"[RUSH] 并发 {config.concurrency} 工作线程, 持续 {config.rush_duration_s}s")
    logger.info(f"[API] 请求头: {json.dumps(safe_headers, ensure_ascii=False)}")
    logger.info(f"[API] 请求体: {json.dumps(body, ensure_ascii=False)}")

    deadline = time.monotonic() + config.rush_duration_s
    total_attempts = 0
    stop_event = asyncio.Event()
    results: list[tuple[RushStatus, str]] = []

    async def worker(worker_id: int):
        nonlocal total_attempts
        while not stop_event.is_set() and time.monotonic() < deadline:
            total_attempts += 1
            status, message = await _single_request(
                client, url, headers, body, worker_id, total_attempts,
            )

            if status == RushStatus.SUCCESS:
                stop_event.set()
                results.append((status, message))
                return
            if status == RushStatus.ALREADY_DONE:
                stop_event.set()
                results.append((status, message))
                return
            if status == RushStatus.FAILED:
                stop_event.set()
                results.append((status, message))
                return

            # RETRY — fire again immediately
            console.print(f"      [dim]W{worker_id} #{total_attempts}: {message}[/dim]")

    # Launch concurrent workers
    workers = [asyncio.create_task(worker(i)) for i in range(config.concurrency)]

    # Wait for all workers to finish (either deadline or early stop)
    await asyncio.gather(*workers, return_exceptions=True)

    if results:
        return results[0]

    logger.info(f"[RUSH] {total_attempts} 次请求，全部未成功")
    return RushStatus.FAILED, f"{total_attempts} 次请求全部未成功 ({config.rush_duration_s}s)"


async def execute_rush(config: Config, session: LoginSession) -> bool:
    """Phase 3: Concurrent API rush purchase."""
    console.print(f"\n[4/4] 开始抢购 [cyan]{config.plan}[/cyan]! (并发 {config.concurrency}, 持续 {config.rush_duration_s}s)\n")
    logger = logging.getLogger("glm_rush")

    product_id = config.resolved_product_id
    if not product_id:
        console.print("[red]未配置 product_id 且无法从 plan 映射，请在 config.json 中设置 product_id[/red]")
        return False

    auth_token = session.auth_token or ""
    site_headers = session.site_headers or {}
    jar = httpx_cookies_from_playwright(session.cookies)

    console.print(f"      产品ID: [cyan]{product_id}[/cyan]")
    console.print(f"      购买URL: [cyan]{PURCHASE_URL}[/cyan]")
    console.print(f"      Auth Token: {'有 (' + auth_token[:20] + '...)' if auth_token else '[red]无[/red]'}")
    console.print(f"      Site Headers: {site_headers if site_headers else '[yellow]无[/yellow]'}")
    console.print(f"      Cookies: {len(session.cookies)} 个")

    logger.info(f"[RUSH] 开始抢购: plan={config.plan}, product_id={product_id}")
    logger.info(f"[RUSH] auth_token={'有 (' + auth_token[:30] + '...)' if auth_token else '无'}")
    logger.info(f"[RUSH] site_headers={json.dumps(site_headers, ensure_ascii=False)}")
    logger.info(f"[RUSH] cookies数量={len(session.cookies)}")

    if not auth_token:
        console.print("      [bold red]警告: 无 auth token，请求可能返回 401![/bold red]")
        logger.warning("[RUSH] 无 auth token，抢购请求可能失败")
    if not site_headers:
        console.print("      [bold yellow]警告: 无 bigmodel-organization/project headers[/bold yellow]")
        logger.warning("[RUSH] 无 site_headers，缺少 org/project 信息")

    async with httpx.AsyncClient(cookies=jar, http2=True) as client:
        status, message = await _do_purchase(
            client, PURCHASE_URL, auth_token, site_headers, product_id, config,
        )

        if status == RushStatus.SUCCESS:
            console.print(f"\n[bold green]抢购成功! {message}[/bold green]")
            return True
        if status == RushStatus.ALREADY_DONE:
            console.print(f"\n[yellow]{message}[/yellow]")
            return True

    console.print(f"\n[bold red]抢购失败: {message}[/bold red]")
    console.print("[dim]详细信息请查看 rush.log[/dim]")
    return False


async def main():
    logger = setup_logger()
    logger.info("=== GLM Rush (并发 API) 启动 ===")

    # Phase 1: Load config & login
    try:
        config = load_config()
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]配置错误: {e}[/red]")
        console.print("请复制 config.example.json 为 config.json 并填写信息")
        sys.exit(1)

    print_banner(config)

    try:
        session = await perform_login(config)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]登录异常: {e}[/red]")
        logger.exception("登录异常")
        sys.exit(1)

    # Phase 2: Wait (no browser needed)
    ready = await wait_for_target(config)
    if not ready:
        console.print("[red]未能到达目标时间[/red]")
        sys.exit(1)

    # Phase 3: Rush (concurrent API)
    success = await execute_rush(config, session)
    logger.info(f"最终结果: {'成功' if success else '失败'}")

    if success:
        console.print("\n[green]抢购完成! 无需等待浏览器关闭。[/green]")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
