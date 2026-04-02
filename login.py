# login.py
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from capture import PURCHASE_KEYWORDS, detect_purchase_endpoint, save_captured_endpoint, PurchaseEndpoint
from utils import Config, ensure_debug_dir, mask_phone, strip_bearer_prefix

logger = logging.getLogger("glm_rush")

JWT_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")


def _collect_json_tokens(obj: Any, out: list[str], depth: int = 0) -> None:
    if depth > 14:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            lk = str(k).lower()
            if isinstance(v, str) and len(v) > 20:
                if JWT_RE.match(v.strip()) or "token" in lk or lk in (
                    "authorization",
                    "access_token",
                    "accesstoken",
                    "id_token",
                ):
                    out.append(v.strip())
            elif isinstance(v, (dict, list)):
                _collect_json_tokens(v, out, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            _collect_json_tokens(item, out, depth + 1)


def _pick_best_token(candidates: list[str]) -> str | None:
    if not candidates:
        return None
    uniq = list(dict.fromkeys(candidates))
    jwt_hits = [t for t in uniq if JWT_RE.match(t)]
    if jwt_hits:
        return max(jwt_hits, key=len)
    return max(uniq, key=len)


async def _capture_tokens_from_response(response, bucket: list[str]) -> None:
    try:
        url = response.url.lower()
        if "bigmodel" not in url:
            return
        if response.status != 200:
            return
        ct = (response.headers.get("content-type") or "").lower()
        if "json" not in ct:
            return
        if not any(
            k in url
            for k in (
                "/api/",
                "login",
                "auth",
                "token",
                "user",
                "session",
                "passport",
                "oauth",
                "refresh",
            )
        ):
            return
        data = await response.json()
        _collect_json_tokens(data, bucket)
    except Exception:
        pass

LOGIN_URL = "https://bigmodel.cn/login"
CODING_PAGE = "https://bigmodel.cn/glm-coding?utm_source=bigModel&utm_medium=Special&utm_content=glm-code&utm_campaign=Platform_Ops&_channel_track_key=8BAeCdUS"
LOGIN_URL_MARKERS = ("/login", "/user/login", "passport", "signin")
LOGIN_TAB_LABELS = ("密码登录", "账号密码", "账号登录")
ACCOUNT_PLACEHOLDERS = (
    "请输入用户名/邮箱/手机号",
    "请输入手机号",
    "手机号",
)


def _url_indicates_login(url: str) -> bool:
    lower = (url or "").lower()
    return any(marker in lower for marker in LOGIN_URL_MARKERS)


async def _locator_exists(locator) -> bool:
    try:
        return await locator.count() > 0
    except Exception:
        return False


async def _locator_is_visible(locator) -> bool:
    try:
        return await locator.count() > 0 and await locator.is_visible()
    except Exception:
        return False


async def _get_account_input_locator(page):
    for placeholder in ACCOUNT_PLACEHOLDERS:
        locator = page.get_by_placeholder(placeholder)
        if await _locator_exists(locator):
            return locator

    for selector in (
        'input[placeholder*="用户名"]',
        'input[placeholder*="邮箱"]',
        'input[placeholder*="手机"]',
        'input[type="text"]',
    ):
        locator = page.locator(selector).first
        if await _locator_exists(locator):
            return locator

    return page.locator('input[placeholder*="手机"]').first


async def _is_login_ui_visible(page) -> bool:
    for label in ("手机号登录", "账号登录", "微信扫码登录"):
        if await _locator_is_visible(page.get_by_text(label, exact=True).first):
            return True

    if await _locator_is_visible(page.get_by_role("button", name="登录").first):
        return True

    if await _locator_is_visible(page.locator('input[type="password"]').first):
        return True

    account_input = await _get_account_input_locator(page)
    return await _locator_is_visible(account_input)


async def _wait_for_login_indicator(page, checks: int = 16, interval_s: float = 0.25) -> bool:
    for _ in range(checks):
        if _url_indicates_login(page.url) or await _is_login_ui_visible(page):
            return True
        await asyncio.sleep(interval_s)
    return False


async def _fill_password_login_form(page, config: Config) -> None:
    """智谱登录页常为「短信登录」默认页：密码区的 input 在 DOM 里但不可见，必须先切到密码登录。"""
    await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
    await asyncio.sleep(1.2)

    for label in LOGIN_TAB_LABELS:
        tab = page.get_by_text(label, exact=True).first
        try:
            if await tab.count() > 0 and await tab.is_visible():
                await tab.click(timeout=5000)
                await asyncio.sleep(0.6)
                break
        except Exception:
            continue
    else:
        loose = page.locator("text=密码登录").first
        try:
            if await loose.count() > 0:
                await loose.click(timeout=5000)
                await asyncio.sleep(0.6)
        except Exception:
            pass

    pwd = page.locator('input[type="password"]').first
    await pwd.wait_for(state="visible", timeout=25000)

    phone = await _get_account_input_locator(page)

    for attempt in range(3):
        try:
            await phone.wait_for(state="visible", timeout=12000)
            await phone.scroll_into_view_if_needed()
            await phone.click(timeout=5000)
            await phone.fill(config.phone, timeout=15000)
            break
        except Exception as e:
            logger.warning("填入手机号重试 %s/3: %s", attempt + 1, e)
            await asyncio.sleep(0.8)
            phone = await _get_account_input_locator(page)
    else:
        raise RuntimeError("无法在登录页填入手机号（请手动在弹出窗口完成密码登录，或配置 storage_state_path）")

    await pwd.fill(config.password, timeout=15000)
    await page.get_by_role("button", name="登录").first.click(timeout=15000)
    logger.info("点击登录按钮")


class LoginSession:
    """Holds captured auth data (no browser reference)."""

    def __init__(
        self,
        cookies: list[dict],
        auth_token: str | None,
        endpoint: PurchaseEndpoint | None,
        site_headers: dict[str, str] | None = None,
    ):
        self.cookies = cookies
        self.auth_token = auth_token
        self.endpoint = endpoint
        self.site_headers = site_headers or {}


async def perform_login(config: Config) -> LoginSession:
    """Launch browser, log in, capture auth token + cookies, then close browser."""
    pw = await async_playwright().start()
    browser = None
    try:
        browser = await pw.chromium.launch(headless=False)

        ctx_kwargs: dict[str, Any] = {}
        ssp = (config.storage_state_path or "").strip()
        if ssp:
            p = Path(ssp).expanduser()
            if p.is_file():
                ctx_kwargs["storage_state"] = str(p)
                logger.info("使用 storage_state: %s", p)
            else:
                logger.warning("storage_state_path 不是有效文件，已忽略: %s", p)

        context = await browser.new_context(**ctx_kwargs)
        page = await context.new_page()

        intercepted_requests: list[dict[str, Any]] = []
        response_tokens: list[str] = []

        def _on_response(response) -> None:
            try:
                asyncio.get_running_loop().create_task(
                    _capture_tokens_from_response(response, response_tokens)
                )
            except RuntimeError:
                pass

        page.on("response", _on_response)

        # Intercept network requests to capture purchase API
        async def handle_request(route, request):
            url = request.url.lower()
            if any(kw in url for kw in PURCHASE_KEYWORDS):
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

        # Step 1: Go to coding page, detect login state
        logger.info("正在打开 Coding Plan 页面...")
        print("\n[2/4] 登录中...")
        if not ctx_kwargs.get("storage_state"):
            print("      提示: 使用独立 Chromium，与系统 Chrome 已登录状态无关；可配置 storage_state_path 复用会话。")

        await page.goto(CODING_PAGE, wait_until="domcontentloaded", timeout=45000)
        needs_login = await _wait_for_login_indicator(page)

        if needs_login:
            logger.info("需要登录，进入密码登录流程...")
            await _fill_password_login_form(page, config)
            logger.info(f"已填入手机号: {mask_phone(config.phone)}")

            try:
                await page.wait_for_url(
                    lambda url: all(x not in url.lower() for x in ("/login", "/user/login")),
                    timeout=20000,
                )
            except Exception:
                pass

            logger.info("正在导航到 Coding Plan 页面...")
            print("      导航到 Coding Plan 页面...")
            await page.goto(CODING_PAGE, wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(3)
        else:
            logger.info("当前页面无需密码登录（已登录或 storage_state 有效）")
            print("      已登录 ✓（当前会话无需再填密码）")

        u = page.url.lower()
        if "/login" in u or "/user/login" in u:
            debug_dir = ensure_debug_dir()
            await page.screenshot(path=str(debug_dir / "login_failed.png"))
            raise RuntimeError("登录失败，请检查账号密码。截图已保存到 debug/login_failed.png")

        print("      登录成功 ✓")
        logger.info("登录成功")

        # Step 3: Capture cookies and auth token
        cookies = await context.cookies()

        token_from_cookie = None
        for cookie in cookies:
            n = cookie.get("name", "").lower()
            if "token" in n or n in ("jwt", "accesstoken"):
                token_from_cookie = cookie["value"]
                break

        storage_token = None
        try:
            storage_token = await page.evaluate("""
            () => {
                function scan(store) {
                    for (let i = 0; i < store.length; i++) {
                        const key = store.key(i);
                        const lower = key.toLowerCase();
                        if (!lower.includes('token') && !lower.includes('auth')
                            && !lower.includes('jwt') && !lower.includes('access')) continue;
                        const val = store.getItem(key);
                        if (!val) continue;
                        try {
                            const o = JSON.parse(val);
                            const t = o.access_token || o.token || o.accessToken || o.jwt;
                            if (typeof t === 'string' && t.length > 20) return t;
                        } catch (e) {
                            if (val.length > 20) return val;
                        }
                    }
                    return null;
                }
                return scan(localStorage) || scan(sessionStorage);
            }
        """)
        except Exception:
            pass

        resp_token = _pick_best_token(response_tokens)

        auth_token = resp_token or storage_token or token_from_cookie
        if not auth_token and config.access_token:
            auth_token = strip_bearer_prefix(config.access_token)
        elif auth_token:
            auth_token = strip_bearer_prefix(auth_token)

        logger.info(f"捕获 auth_token: {'有' if auth_token else '无'}")
        if not auth_token:
            print("      警告: 未捕获到 JWT；智谱 API 需要 Authorization 头。")
            print("            请在 config.json 填写 access_token（DevTools → Network → 请求头 Authorization，可省略 Bearer 前缀）")
            logger.warning("未捕获 auth_token，抢购请求可能 401（需在 config 配置 access_token）")

        # Step 4: Detect purchase API
        endpoint = detect_purchase_endpoint(intercepted_requests)
        if endpoint:
            save_captured_endpoint(endpoint)
            print(f"      API 抓包: 发现购买端点 {endpoint.method} {endpoint.url} ✓")
            logger.info(f"发现购买端点: {endpoint.method} {endpoint.url}")
        else:
            print("      API 抓包: 未发现购买端点，将使用内置 pay/preview API")
            logger.info("未发现购买端点，将使用内置 pay/preview API")

        # Step 5: Extract bigmodel-organization and bigmodel-project from captured requests
        site_headers = {}
        for req in intercepted_requests:
            h = req.get("headers") or {}
            for key in ("bigmodel-organization", "bigmodel-project"):
                if key in h and not site_headers.get(key):
                    site_headers[key] = h[key]

        # Also scan all intercepted request headers (including non-purchase ones)
        # Re-scan response interception for these headers too
        if not site_headers.get("bigmodel-organization"):
            # Try extracting from cookies or page context
            try:
                org_project = await page.evaluate("""
                    () => {
                        const result = {};
                        for (let i = 0; i < localStorage.length; i++) {
                            const key = localStorage.key(i);
                            if (key.includes('org') || key.includes('project') || key.includes('organization')) {
                                try {
                                    const val = JSON.parse(localStorage.getItem(key));
                                    if (val.organizationId) result['bigmodel-organization'] = val.organizationId;
                                    if (val.projectId) result['bigmodel-project'] = val.projectId;
                                } catch(e) {}
                            }
                        }
                        return result;
                    }
                """)
                for k, v in org_project.items():
                    if not site_headers.get(k):
                        site_headers[k] = v
            except Exception:
                pass

        if site_headers:
            print(f"      捕获站点 headers: {list(site_headers.keys())} ✓")
            logger.info(f"捕获站点 headers: {site_headers}")
        else:
            print("      警告: 未捕获 bigmodel-organization/project headers")
            logger.warning("未捕获 bigmodel-organization/project headers，API 可能返回 401")

        print("      抓取 cookies + auth_token ✓")
        print("      浏览器已关闭，切换纯 API 模式 ✓")
        logger.info("浏览器已关闭，切换纯 API 模式")

        return LoginSession(
            cookies=cookies,
            auth_token=auth_token,
            endpoint=endpoint,
            site_headers=site_headers,
        )
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        await pw.stop()
