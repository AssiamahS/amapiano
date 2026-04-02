#!/usr/bin/env python3
"""Browser testing MCP - screenshot, click, scroll, evaluate JS on localhost pages."""

import os
from mcp.server.fastmcp import FastMCP
from playwright.async_api import async_playwright

mcp = FastMCP("browser-test")

_pw = None
_browser = None
_page = None
SCREENSHOT_DIR = os.path.expanduser("~/amapiano/mcp-browser/screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


async def get_page():
    global _pw, _browser, _page
    if _page and not _page.is_closed():
        return _page
    _pw = await async_playwright().start()
    _browser = await _pw.chromium.launch(headless=True)
    _page = await _browser.new_page(viewport={"width": 1400, "height": 900})
    return _page


@mcp.tool()
async def screenshot(url: str = "http://localhost:8766", filename: str = "screen.png") -> str:
    """Take a screenshot of a URL. Returns the file path."""
    page = await get_page()
    await page.goto(url, wait_until="networkidle", timeout=15000)
    path = os.path.join(SCREENSHOT_DIR, filename)
    await page.screenshot(path=path, full_page=False)
    return f"Screenshot saved to {path}"


@mcp.tool()
async def screenshot_element(selector: str, url: str = "http://localhost:8766", filename: str = "element.png") -> str:
    """Screenshot a specific CSS selector element."""
    page = await get_page()
    if page.url != url:
        await page.goto(url, wait_until="networkidle", timeout=15000)
    el = await page.query_selector(selector)
    if not el:
        return f"Element '{selector}' not found"
    path = os.path.join(SCREENSHOT_DIR, filename)
    await el.screenshot(path=path)
    return f"Element screenshot saved to {path}"


@mcp.tool()
async def click(selector: str, url: str = "http://localhost:8766") -> str:
    """Click an element by CSS selector."""
    page = await get_page()
    if page.url != url:
        await page.goto(url, wait_until="networkidle", timeout=15000)
    await page.click(selector, timeout=5000)
    await page.wait_for_timeout(500)
    return f"Clicked '{selector}'"


@mcp.tool()
async def click_text(text: str, url: str = "http://localhost:8766") -> str:
    """Click an element by its visible text content."""
    page = await get_page()
    if page.url != url:
        await page.goto(url, wait_until="networkidle", timeout=15000)
    await page.get_by_text(text, exact=False).first.click(timeout=5000)
    await page.wait_for_timeout(500)
    return f"Clicked text '{text}'"


@mcp.tool()
async def evaluate(js: str, url: str = "http://localhost:8766") -> str:
    """Run JavaScript in the page and return the result."""
    page = await get_page()
    if page.url != url:
        await page.goto(url, wait_until="networkidle", timeout=15000)
    result = await page.evaluate(js)
    return str(result)


@mcp.tool()
async def scroll_to(selector: str, url: str = "http://localhost:8766") -> str:
    """Scroll an element into view."""
    page = await get_page()
    if page.url != url:
        await page.goto(url, wait_until="networkidle", timeout=15000)
    await page.evaluate(f"document.querySelector('{selector}')?.scrollIntoView({{behavior:'smooth',inline:'center'}})")
    await page.wait_for_timeout(500)
    return f"Scrolled to '{selector}'"


@mcp.tool()
async def get_page_info(url: str = "http://localhost:8766") -> str:
    """Get page title, URL, and count of key elements."""
    page = await get_page()
    if page.url != url:
        await page.goto(url, wait_until="networkidle", timeout=15000)
    info = await page.evaluate("""() => {
        return {
            title: document.title,
            url: location.href,
            downloads: document.querySelectorAll('.dl-item').length,
            inputs: document.querySelectorAll('input').length,
            buttons: document.querySelectorAll('button, .btn').length,
            bodyText: document.body.innerText.substring(0, 500),
        }
    }""")
    return str(info)


@mcp.tool()
async def fill(selector: str, text: str, url: str = "http://localhost:8766") -> str:
    """Fill a text input by CSS selector."""
    page = await get_page()
    if page.url != url:
        await page.goto(url, wait_until="networkidle", timeout=15000)
    await page.fill(selector, text, timeout=5000)
    return f"Filled '{selector}' with '{text}'"


@mcp.tool()
async def goto(url: str) -> str:
    """Navigate to a URL."""
    page = await get_page()
    await page.goto(url, wait_until="networkidle", timeout=15000)
    return f"Navigated to {page.url} - Title: {await page.title()}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
