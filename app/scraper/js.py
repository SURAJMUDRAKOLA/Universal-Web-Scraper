"""Async Playwright browser engine — feeds the same extraction pipeline as static.py.

Responsibilities:
- Lifecycle management with isolated contexts
- Navigation with proper wait strategy (networkidle → domcontentloaded fallback)
- Null-safe scrollHeight evaluation (never crashes on headless pages)
- Bounded interactions: tab clicks, load-more clicks, pagination, infinite scroll
- Safe cleanup — browser always closed in finally block
- Structured errors with recoverable flag
"""
from __future__ import annotations

import time
from urllib.parse import urlparse, urlunsplit

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from app.scraper.config import (
    BROWSER_TIMEOUT,
    BROWSER_WAIT_AFTER_LOAD,
    BROWSER_WAIT_AFTER_NAV,
    MAX_CLICKS,
    MAX_PAGES,
    MAX_SCROLLS,
    MIN_PAGES,
)
from app.scraper.urls import normalize_for_dedup

# ── Interaction selectors ─────────────────────────────────────────────────────
_TAB_SELECTORS = [
    "[role='tab']",
    "button[aria-selected]",
    ".tab",
    "[data-tab]",
]

_LOAD_MORE_SELECTORS = [
    "button:has-text('Load more')",
    "button:has-text('Show more')",
    "button:has-text('See more')",
    "a:has-text('Load more')",
    ".load-more",
    "[class*='load-more']",
    "[id*='load-more']",
]

_PAGINATION_SELECTORS = [
    "a.morelink",               # Hacker News (highly specific, try first)
    "a[rel='next']",            # Standard rel=next
    ".pagination__next",        # Common framework pattern
    ".pagination a.next",       # Common pattern
    "[aria-label='Next page']", # ARIA pattern
    "a:has-text('Next')",       # Text-based fallback
]

# Null-safe scroll height JS (guard against headless pages without body)
_JS_SCROLL_HEIGHT = (
    "document.body ? document.body.scrollHeight "
    ": document.documentElement.scrollHeight"
)
_JS_SCROLL_BOTTOM = (
    "window.scrollTo(0, document.body ? document.body.scrollHeight "
    ": document.documentElement.scrollHeight)"
)


async def js_scrape(url: str) -> tuple[str, dict, str | None]:
    """Render a page with Playwright and return (html, interactions, error|None).

    html is the final rendered page HTML after all interactions.
    interactions matches the schema: {clicks, scrolls, pages}.
    error is a human-readable message if rendering failed entirely.
    """
    interactions: dict = {
        "clicks": [],
        "scrolls": 0,
        "pages": [url],
    }
    errors: list[dict] = []
    t0 = time.perf_counter()

    browser = None
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            )
            page = await context.new_page()

            # ── Navigate ──────────────────────────────────────────────────────
            try:
                await page.goto(url, timeout=BROWSER_TIMEOUT, wait_until="domcontentloaded")
                # Try networkidle for richer content, fall back silently on timeout
                try:
                    await page.wait_for_load_state("networkidle", timeout=1_500)
                except PlaywrightTimeout:
                    pass
                await page.wait_for_timeout(BROWSER_WAIT_AFTER_LOAD)
            except PlaywrightTimeout:
                return "", interactions, "Timeout while loading the page"

            # ── Remove noise from live DOM ────────────────────────────────────
            try:
                await page.evaluate("""
                    ['cookie','modal','popup','overlay','dialog','banner',
                     'newsletter','subscribe','gdpr','toast'].forEach(k => {
                        document.querySelectorAll(
                            `[class*="${k}"],[id*="${k}"]`
                        ).forEach(el => el.remove());
                    });
                """)
            except Exception:
                pass

            # ── Tab clicks ────────────────────────────────────────────────────
            total_clicks = 0
            for selector in _TAB_SELECTORS:
                if total_clicks >= MAX_CLICKS:
                    break
                try:
                    tabs = page.locator(selector)
                    count = await tabs.count()
                    if count > 1:
                        for i in range(min(3, count)):
                            if total_clicks >= MAX_CLICKS:
                                break
                            try:
                                await tabs.nth(i).click(timeout=2_000)
                                interactions["clicks"].append(f"{selector}[{i}]")
                                total_clicks += 1
                                await page.wait_for_timeout(700)
                            except Exception:
                                pass
                        break
                except Exception:
                    continue

            # ── Load-more clicks ──────────────────────────────────────────────
            for selector in _LOAD_MORE_SELECTORS:
                if total_clicks >= MAX_CLICKS:
                    break
                try:
                    btn = page.locator(selector).first
                    if await btn.is_visible(timeout=2_000):
                        for _ in range(3):
                            if total_clicks >= MAX_CLICKS:
                                break
                            try:
                                await btn.click(timeout=3_000)
                                interactions["clicks"].append(selector)
                                total_clicks += 1
                                await page.wait_for_timeout(1_200)
                            except Exception:
                                break
                        break
                except Exception:
                    continue

            # ── Pagination — bounded, visited-set prevents cycles ─────────────
            visited: set[str] = {normalize_for_dedup(url)}

            for _depth in range(MAX_PAGES - 1):  # already counted page 1
                navigated = False
                for selector in _PAGINATION_SELECTORS:
                    try:
                        btn = page.locator(selector).first
                        if not await btn.is_visible(timeout=2_000):
                            continue
                        await btn.click(timeout=5_000)
                        await page.wait_for_load_state("domcontentloaded")
                        await page.wait_for_timeout(BROWSER_WAIT_AFTER_NAV)

                        new_url = await page.evaluate("window.location.href")
                        norm = normalize_for_dedup(new_url)
                        if norm in visited:
                            break  # cycle detected

                        visited.add(norm)
                        interactions["pages"].append(new_url)
                        interactions["clicks"].append(f"pagination[{selector}]")
                        navigated = True
                        break
                    except Exception:
                        continue

                if not navigated:
                    break

            # ── Infinite scroll — null-safe, bounded, errors preserved ────────
            previous_height = 0
            try:
                previous_height = await page.evaluate(_JS_SCROLL_HEIGHT)
            except Exception as exc:
                errors.append({
                    "message": str(exc),
                    "phase": "scroll",
                    "recoverable": True,
                })

            try:
                for _ in range(MAX_SCROLLS):
                    await page.evaluate(_JS_SCROLL_BOTTOM)
                    await page.wait_for_timeout(1_800)
                    interactions["scrolls"] += 1  # count every attempt

                    try:
                        new_height = await page.evaluate(_JS_SCROLL_HEIGHT)
                    except Exception as exc:
                        errors.append({
                            "message": str(exc),
                            "phase": "scroll",
                            "recoverable": True,
                        })
                        break

                    if new_height <= previous_height:
                        break
                    previous_height = new_height
            except Exception as exc:
                errors.append({
                    "message": str(exc),
                    "phase": "scroll",
                    "recoverable": True,
                })

            # ── Capture final HTML ────────────────────────────────────────────
            html = await page.content()
            await context.close()

            render_ms = round((time.perf_counter() - t0) * 1000, 1)
            interactions["_renderDurationMs"] = render_ms

            if errors:
                # Return html + interactions even when scroll errors occurred —
                # earlier data is preserved (recoverable)
                return html, interactions, errors[0]["message"]
            return html, interactions, None

    except PlaywrightTimeout:
        return "", interactions, "Timeout during JS rendering"
    except Exception as exc:
        return "", interactions, str(exc)
    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
