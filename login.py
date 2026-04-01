# login.py
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from playwright.async_api import async_playwright, BrowserContext, Page

from capture import detect_purchase_endpoint, save_captured_endpoint, PurchaseEndpoint
from utils import Config, ensure_debug_dir

logger = logging.getLogger("glm_rush")

LOGIN_URL = "https://bigmodel.cn/glm-coding?utm_source=bigModel&utm_medium=Special&utm_content=glm-code&utm_campaign=Platform_Ops&_channel_track_key=8BAeCdUS"
CODING_PAGE = "https://bigmodel.cn/glm-coding"


class LoginSession:
    """Holds the authenticated browser context and captured API info."""

    def __init__(
        self,
        context: BrowserContext,
        page: Page,
        cookies: list[dict],
        auth_token: str | None,
        endpoint: PurchaseEndpoint | None,
    ):
        self.context = context
        self.page = page
        self.cookies = cookies
        self.auth_token = auth_token
        self.endpoint = endpoint


async def perform_login(config: Config) -> LoginSession:
    """Launch browser, log in, navigate to coding page, capture API endpoints."""
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=False)
    context = await browser.new_context()
    page = await context.new_page()

    intercepted_requests: list[dict[str, Any]] = []

    # Intercept network requests to capture purchase API
    async def handle_request(route, request):
        url = request.url.lower()
        if any(kw in url for kw in ("subscribe", "order", "purchase", "buy")):
            body = None
            try:
                body = json.loads(request.post_data) if request.post_data else None
            except (json.JSONDecodeError, TypeError):
                pass
            intercepted_requests.append({
                "url": request.url,
                "method": request.method,
                "headers": dict(request.headers) if request.headers else None,
                "body": body,
            })
        await route.continue_()

    await page.route("**/*", handle_request)

    # Step 1: Login
    logger.info("正在打开登录页面...")
    print("\n[2/4] 登录中...")
    await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(2)

    if "/login" not in page.url:
        logger.info("已经登录，跳过登录步骤")
        print("      已经登录 ✓")
    else:
        # Click password login tab if available
        try:
            pwd_tab = page.locator("text=密码登录").or_(page.locator("text=账号密码"))
            if await pwd_tab.count() > 0:
                await pwd_tab.first.click()
                await asyncio.sleep(0.5)
        except Exception:
            pass

        # Fill phone
        phone_input = page.locator('input[type="tel"]').or_(
            page.locator('input[placeholder*="手机"]')
        ).or_(
            page.locator('input[placeholder*="phone"]')
        ).first
        await phone_input.fill(config.phone)
        logger.info(f"已填入手机号: {config.phone[:3]}****{config.phone[-4:]}")

        # Fill password
        pwd_input = page.locator('input[type="password"]').first
        await pwd_input.fill(config.password)
        logger.info("已填入密码")

        # Click login
        login_btn = page.get_by_role("button", name="登录").or_(
            page.locator("button:has-text('登')")
        ).first
        await login_btn.click()
        logger.info("点击登录按钮")

        # Wait for login to complete: URL change or SPA async
        try:
            await page.wait_for_url(lambda url: "/login" not in url, timeout=8000)
        except Exception:
            pass  # SPA may not change URL, that's OK

    # Step 2: Navigate to coding plan page (also verifies login)
    logger.info("正在导航到 Coding Plan 页面...")
    print("      导航到 Coding Plan 页面...")
    await page.goto(CODING_PAGE, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(3)

    if "/login" in page.url:
        debug_dir = ensure_debug_dir()
        await page.screenshot(path=str(debug_dir / "login_failed.png"))
        raise RuntimeError("登录失败，请检查账号密码。截图已保存到 debug/login_failed.png")

    print("      登录成功 ✓")
    logger.info("登录成功")

    # Step 3: Capture cookies and auth token
    cookies = await context.cookies()

    auth_token = None
    for cookie in cookies:
        if "token" in cookie.get("name", "").lower():
            auth_token = cookie["value"]
            break

    # Also try localStorage
    try:
        auth_token = await page.evaluate("""
            () => {
                const keys = Object.keys(localStorage);
                for (const key of keys) {
                    if (key.toLowerCase().includes('token')) {
                        const val = localStorage.getItem(key);
                        try { return JSON.parse(val).access_token || JSON.parse(val).token || val; }
                        catch { return val; }
                    }
                }
                return null;
            }
        """)
    except Exception:
        pass

    logger.info(f"捕获 auth_token: {'有' if auth_token else '无'}")

    # Step 4: Detect purchase API
    endpoint = detect_purchase_endpoint(intercepted_requests)
    if endpoint:
        save_captured_endpoint(endpoint)
        print(f"      API 抓包: 发现购买端点 {endpoint.method} {endpoint.url} ✓")
        logger.info(f"发现购买端点: {endpoint.method} {endpoint.url}")
    else:
        print("      API 抓包: 未发现购买端点，将使用浏览器模式")
        logger.info("未发现购买端点，回退到浏览器模式")

    print("      抓取 cookies + auth_token ✓")

    return LoginSession(
        context=context,
        page=page,
        cookies=cookies,
        auth_token=auth_token,
        endpoint=endpoint,
    )
