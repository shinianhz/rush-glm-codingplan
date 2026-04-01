# main.py
from __future__ import annotations

import asyncio
import sys
import logging

import httpx
from rich.console import Console
from rich.panel import Panel

from utils import load_config, seconds_until_target, setup_logger, Config
from login import perform_login
from sniper import rush_via_api, rush_via_browser, RushStatus

console = Console()


def print_banner(config: Config):
    phone_masked = config.phone[:3] + "****" + config.phone[-4:]
    console.print(Panel.fit(
        f"[bold]GLM Coding Plan Rush[/bold]\n"
        f"档位: [cyan]{config.plan}[/cyan]\n"
        f"手机: [dim]{phone_masked}[/dim]\n"
        f"目标时间: [cyan]{config.target_time}[/cyan]\n"
        f"最大重试: [cyan]{config.max_retries}[/cyan]",
        title="[1/4] 配置已加载",
        border_style="green",
    ))


async def wait_for_target(config: Config, session):
    """Phase 2: Countdown with heartbeat."""
    console.print("\n[3/4] 等待开售...")

    while True:
        secs = seconds_until_target(config.target_time)

        if secs <= -1:
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

        # Connection pre-warm at 2s before target
        if secs <= 2.5:
            console.print("\n      预热连接...")
            try:
                async with httpx.AsyncClient() as client:
                    await client.head("https://bigmodel.cn", timeout=3)
            except Exception:
                pass

        if secs <= 1.5:
            # Busy-wait the last ~1.5 seconds for precision
            while seconds_until_target(config.target_time) > 0:
                pass
            return True

        # Heartbeat every 5 seconds
        try:
            resp = await session.page.evaluate("fetch('/api/paas/v4/user/info').then(r=>r.ok)")
            if not resp:
                console.print("\n[yellow]会话可能过期，尝试刷新...[/yellow]")
                await session.page.reload(wait_until="domcontentloaded", timeout=10000)
        except Exception:
            console.print("\n[yellow]心跳检测失败，尝试刷新页面...[/yellow]")
            try:
                await session.page.reload(wait_until="domcontentloaded", timeout=10000)
            except Exception:
                pass

        await asyncio.sleep(5)


async def execute_rush(config: Config, session):
    """Phase 3: Execute the rush purchase."""
    console.print(f"\n[4/4] 开始抢购 [cyan]{config.plan}[/cyan]!\n")
    logger = logging.getLogger("glm_rush")

    # Channel 1: API direct
    if session.endpoint and session.auth_token:
        console.print("[bold]通道 1: API 直连[/bold]")
        logger.info("启动 API 通道")

        headers = session.endpoint.headers or {}
        if session.auth_token:
            headers["Authorization"] = f"Bearer {session.auth_token}"

        async with httpx.AsyncClient(cookies={c["name"]: c["value"] for c in session.cookies}) as client:
            result = await rush_via_api(client, session.endpoint, config)

        if result.status == RushStatus.SUCCESS:
            console.print(f"[bold green]API 通道成功! {result.message}[/bold green]")
            return True
        if result.status == RushStatus.ALREADY_DONE:
            console.print(f"[yellow]{result.message}[/yellow]")
            return True

        console.print(f"[dim]API 通道失败: {result.message}，切换浏览器通道[/dim]")

    # Channel 2: Browser fallback
    console.print("[bold]通道 2: 浏览器自动化[/bold]")
    result = await rush_via_browser(session.page, config)

    if result.status == RushStatus.SUCCESS:
        console.print(f"[bold green]浏览器通道成功! {result.message}[/bold green]")
        return True
    if result.status == RushStatus.ALREADY_DONE:
        console.print(f"[yellow]{result.message}[/yellow]")
        return True

    console.print(f"[bold red]抢购失败: {result.message}[/bold red]")
    return False


async def main():
    logger = setup_logger()
    logger.info("=== GLM Rush 启动 ===")

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

    # Phase 2: Wait
    ready = await wait_for_target(config, session)
    if not ready:
        console.print("[red]未能到达目标时间[/red]")
        sys.exit(1)

    # Phase 3: Rush
    success = await execute_rush(config, session)
    logger.info(f"最终结果: {'成功' if success else '失败'}")

    # Keep browser open for 30s to verify
    if success:
        console.print("\n[dim]浏览器将在 30 秒后关闭，你可以手动检查...[/dim]")
        await asyncio.sleep(30)

    await session.context.browser.close()


if __name__ == "__main__":
    asyncio.run(main())
