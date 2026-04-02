#!/usr/bin/env python3
"""一次性导出 Playwright 登录状态，供 config.json 的 storage_state_path 使用。

用法:
  python3 export_playwright_state.py

在弹出的 Chromium 里完成智谱登录，回到终端按 Enter，会生成 bigmodel_storage.json。
"""
from __future__ import annotations

import asyncio

from playwright.async_api import async_playwright

OUT = "bigmodel_storage.json"
START = "https://bigmodel.cn/glm-coding"


async def main() -> None:
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=False)
    context = await browser.new_context()
    page = await context.new_page()
    await page.goto(START, wait_until="domcontentloaded", timeout=60000)
    print(f"已在浏览器打开页面，请完成登录。完成后回到此处按 Enter 保存 → {OUT}")
    await asyncio.to_thread(input)
    await context.storage_state(path=OUT)
    await browser.close()
    await pw.stop()
    print(f"已保存。请在 config.json 中设置: \"storage_state_path\": \"{OUT}\"")


if __name__ == "__main__":
    asyncio.run(main())
