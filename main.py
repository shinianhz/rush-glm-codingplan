# main.py
from __future__ import annotations

import asyncio
import sys
import logging

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
    detect_api_result,
    RushStatus,
    parse_response_json,
)

console = Console()


def print_banner(config: Config):
    phone_masked = mask_phone(config.phone)
    pid = config.resolved_product_id or "未配置"
    console.print(Panel.fit(
        f"[bold]GLM Coding Plan Rush (纯 API)[/bold]\n"
        f"档位: [cyan]{config.plan}[/cyan]\n"
        f"手机: [dim]{phone_masked}[/dim]\n"
        f"目标时间: [cyan]{config.target_time}[/cyan]\n"
        f"最大重试: [cyan]{config.max_retries}[/cyan]\n"
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
                async with httpx.AsyncClient() as client:
                    await client.head("https://bigmodel.cn", timeout=2)
            except Exception:
                pass
            continue

        # Adaptive sleep: always wake up at least 3.5s before target
        sleep_time = min(5.0, max(0.1, secs - 3.5))
        await asyncio.sleep(sleep_time)


async def _do_purchase(
    client: httpx.AsyncClient,
    url: str,
    auth_token: str,
    site_headers: dict[str, str],
    product_id: str,
    config: Config,
) -> tuple[RushStatus, str]:
    """Core purchase logic: POST to pay/preview with productId."""
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://bigmodel.cn",
        "Referer": "https://bigmodel.cn/glm-coding",
        "set-language": "zh",
    }
    # Raw JWT — no "Bearer " prefix!
    if auth_token:
        headers["Authorization"] = auth_token
    # Merge captured org/project headers
    headers.update(site_headers)

    body = {"productId": product_id}

    for attempt in range(1, config.max_retries + 1):
        logger = logging.getLogger("glm_rush")
        logger.info(f"[API] 尝试 {attempt}/{config.max_retries} product={product_id}")
        try:
            resp = await client.request(
                "POST",
                url,
                json=body,
                headers=headers,
                timeout=10.0,
            )
            result = detect_api_result(resp.status_code, parse_response_json(resp))
            result.attempt = attempt
            logger.info(f"[API] → {result.status.value}: {result.message}")

            if result.status in (RushStatus.SUCCESS, RushStatus.ALREADY_DONE, RushStatus.FAILED):
                return result.status, result.message
        except httpx.TimeoutException:
            logger.warning(f"[API] 尝试 {attempt} 超时")
        except Exception as e:
            logger.warning(f"[API] 尝试 {attempt} 异常: {e}")

        await asyncio.sleep(config.retry_interval_ms / 1000)

    return RushStatus.FAILED, "API 通道全部失败"


async def execute_rush(config: Config, session: LoginSession) -> bool:
    """Phase 3: Pure API rush purchase via pay/preview."""
    console.print(f"\n[4/4] 开始抢购 [cyan]{config.plan}[/cyan]!\n")
    logger = logging.getLogger("glm_rush")

    product_id = config.resolved_product_id
    if not product_id:
        console.print("[red]未配置 product_id 且无法从 plan 映射，请在 config.json 中设置 product_id[/red]")
        return False

    auth_token = session.auth_token or ""
    site_headers = session.site_headers or {}
    jar = httpx_cookies_from_playwright(session.cookies)

    console.print(f"      产品ID: [cyan]{product_id}[/cyan]")
    console.print(f"      Auth Token: {'有' if auth_token else '无'}")
    console.print(f"      Site Headers: {list(site_headers.keys()) if site_headers else '无'}")

    async with httpx.AsyncClient(cookies=jar) as client:
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
    return False


async def main():
    logger = setup_logger()
    logger.info("=== GLM Rush (纯 API) 启动 ===")

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

    # Phase 3: Rush (pure API)
    success = await execute_rush(config, session)
    logger.info(f"最终结果: {'成功' if success else '失败'}")

    if success:
        console.print("\n[green]抢购完成! 无需等待浏览器关闭。[/green]")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
