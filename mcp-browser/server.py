#!/usr/bin/env python3
"""Browser testing MCP - screenshot, click, scroll, evaluate JS on localhost pages."""

import base64
import os
from mcp.server.fastmcp import FastMCP
from playwright.sync_api import sync_playwright

mcp = FastMCP("browser-test")

_pw = None
_browser = None
_page = None
SCREENSHOT_DIR = os.path.expanduser("~/amapiano/mcp-browser/screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def get_page():
    global _pw, _browser, _page
    if _page and not _page.is_closed():
        return _page
    _pw = sync_playwright().start()
    _browser = _pw.chromium.launch(headless=True)
    _page = _browser.new_page(viewport={"width": 1400, "height": 900})
    return _page


@mcp.tool()
def screenshot(url: str = "http://localhost:8766", filename: str = "screen.png") -> str:
    """Take a screenshot of a URL. Returns the file path."""
    page = get_page()
    page.goto(url, wait_until="networkidle", timeout=15000)
    path = os.path.join(SCREENSHOT_DIR, filename)
    page.screenshot(path=path, full_page=False)
    return f"Screenshot saved to {path}"


@mcp.tool()
def screenshot_element(selector: str, url: str = "http://localhost:8766", filename: str = "element.png") -> str:
    """Screenshot a specific CSS selector element."""
    page = get_page()
    if page.url != url:
        page.goto(url, wait_until="networkidle", timeout=15000)
    el = page.query_selector(selector)
    if not el:
        return f"Element '{selector}' not found"
    path = os.path.join(SCREENSHOT_DIR, filename)
    el.screenshot(path=path)
    return f"Element screenshot saved to {path}"


@mcp.tool()
def click(selector: str, url: str = "http://localhost:8766") -> str:
    """Click an element by CSS selector."""
    page = get_page()
    if page.url != url:
        page.goto(url, wait_until="networkidle", timeout=15000)
    page.click(selector, timeout=5000)
    page.wait_for_timeout(500)
    return f"Clicked '{selector}'"


@mcp.tool()
def click_text(text: str, url: str = "http://localhost:8766") -> str:
    """Click an element by its visible text content."""
    page = get_page()
    if page.url != url:
        page.goto(url, wait_until="networkidle", timeout=15000)
    page.get_by_text(text, exact=False).first.click(timeout=5000)
    page.wait_for_timeout(500)
    return f"Clicked text '{text}'"


@mcp.tool()
def evaluate(js: str, url: str = "http://localhost:8766") -> str:
    """Run JavaScript in the page and return the result."""
    page = get_page()
    if page.url != url:
        page.goto(url, wait_until="networkidle", timeout=15000)
    result = page.evaluate(js)
    return str(result)


@mcp.tool()
def scroll_to(selector: str, url: str = "http://localhost:8766") -> str:
    """Scroll an element into view."""
    page = get_page()
    if page.url != url:
        page.goto(url, wait_until="networkidle", timeout=15000)
    page.evaluate(f"document.querySelector('{selector}')?.scrollIntoView({{behavior:'smooth',inline:'center'}})")
    page.wait_for_timeout(500)
    return f"Scrolled to '{selector}'"


@mcp.tool()
def get_page_info(url: str = "http://localhost:8766") -> str:
    """Get page title, URL, and count of key elements."""
    page = get_page()
    if page.url != url:
        page.goto(url, wait_until="networkidle", timeout=15000)
    info = page.evaluate("""() => {
        return {
            title: document.title,
            url: location.href,
            cfCards: document.querySelectorAll('.cf-card').length,
            cfActive: document.querySelector('.cf-card.active')?.dataset?.idx || 'none',
            gridCards: document.querySelectorAll('.grid-card').length,
            listRows: document.querySelectorAll('.list-row').length,
            heroCards: document.querySelectorAll('.hero-card').length,
            currentView: typeof currentView !== 'undefined' ? currentView : 'unknown',
            cfIndex: typeof cfIndex !== 'undefined' ? cfIndex : -1,
            filteredCount: typeof filteredTracks !== 'undefined' ? filteredTracks.length : 0,
            playerVisible: document.getElementById('player')?.classList?.contains('active') || false,
        }
    }""")
    return str(info)


@mcp.tool()
def switch_view(view: str, url: str = "http://localhost:8766") -> str:
    """Switch the music library view. Options: list, thumb, grid, coverflow"""
    page = get_page()
    if page.url != url:
        page.goto(url, wait_until="networkidle", timeout=15000)
    page.evaluate(f"setView('{view}')")
    page.wait_for_timeout(1000)
    path = os.path.join(SCREENSHOT_DIR, f"view-{view}.png")
    page.screenshot(path=path, full_page=False)
    return f"Switched to {view} view. Screenshot: {path}"


@mcp.tool()
def test_coverflow_scroll(direction: str = "right", steps: int = 3, url: str = "http://localhost:8766") -> str:
    """Test coverflow scrolling. Direction: left or right. Takes screenshot after."""
    page = get_page()
    if page.url != url:
        page.goto(url, wait_until="networkidle", timeout=15000)
    page.evaluate("setView('coverflow')")
    page.wait_for_timeout(1000)

    key = "ArrowRight" if direction == "right" else "ArrowLeft"
    for i in range(steps):
        page.keyboard.press(key)
        page.wait_for_timeout(400)

    info = page.evaluate("""() => ({
        cfIndex: cfIndex,
        totalCfTracks: cfTracksCached?.length || 0,
        activeCard: document.querySelector('.cf-card.active')?.dataset?.idx,
        renderedCards: document.querySelectorAll('.cf-card').length,
        sliderVal: document.getElementById('cfSlider')?.value,
        titleText: document.getElementById('cfTitle')?.textContent,
    })""")

    path = os.path.join(SCREENSHOT_DIR, f"cf-scroll-{direction}.png")
    page.screenshot(path=path, full_page=False)
    return f"Scrolled {direction} {steps}x. State: {info}. Screenshot: {path}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
