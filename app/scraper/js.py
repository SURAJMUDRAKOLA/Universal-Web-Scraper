"""Async Playwright browser engine — feeds the same extraction pipeline as static.py.

Responsibilities:
- Lifecycle management with isolated contexts
- Navigation with proper wait strategy (networkidle → domcontentloaded fallback)
- Null-safe scrollHeight evaluation (never crashes on headless pages)
- Bounded interactions: tab clicks, load-more clicks, pagination, infinite scroll
- Challenge/block detection — reports honestly, does not bypass
- Safe cleanup — browser always closed in finally block
- Structured BrowserResult — not a fragile tuple
"""
from __future__ import annotations

import asyncio
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import urljoin

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
from app.scraper.urls import URLPolicyError, normalize_for_dedup, normalize_url


# ── Structured result (F1: replaces fragile tuple) ────────────────────────────

@dataclass
class BrowserResult:
    """Structured return value from js_scrape — replaces (html, interactions, error) tuple."""
    # Fix 1: html/final_url always reflect the INITIAL (requested) page, not last navigated.
    html: str = ""
    final_url: str = ""
    # Initial capture (same as html/final_url after Fix 1)
    initial_html: str = ""
    initial_url: str = ""
    pages: list[str] = field(default_factory=list)
    clicks: list[str] = field(default_factory=list)
    scrolls: int = 0
    scrolls_with_change: int = 0
    render_duration_ms: float = 0.0
    # F2: Challenge/block detection
    blocked: bool = False
    challenge_detected: bool = False
    block_reason: str = ""
    # Error/warning tracking
    browser_error: str | None = None
    errors: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Diagnostics
    navigation_status: int | None = None
    final_html_length: int = 0
    browser_attempted: bool = False
    browser_succeeded: bool = False

    def to_interactions(self) -> dict:
        """Return the interactions dict the frontend expects."""
        return {
            "clicks": self.clicks,
            "scrolls": self.scrolls,
            "pages": self.pages,
        }


# ── Challenge/block detection signals (F2) ────────────────────────────────────

_CHALLENGE_TITLES = (
    "just a moment",
    "attention required",
    "security check",
    "access denied",
    "403 forbidden",
    "are you human",
    "ddos-guard",
    "please wait",
    "bot check",
    "verifying you are human",
    "checking your browser",
)

_CHALLENGE_TEXT_SIGNALS = (
    "enable cookies to continue",
    "this site is protected by",
    "verify you are human",
    "complete the captcha",
    "your browser is sending automated",
    "please complete a security check",
    "ray id",                   # Cloudflare ray ID in footer
    "cloudflare",
)


def _detect_challenge(html: str, title: str, page_url: str) -> tuple[bool, str]:
    """Return (is_challenge, reason). Never raises."""
    try:
        html_lower = html.lower()
        title_lower = title.lower().strip()

        # Very small page is suspicious (challenge pages are tiny)
        tiny_page = len(html) < 5_000 and len(html) > 0

        for sig in _CHALLENGE_TITLES:
            if sig in title_lower:
                return True, f"Challenge page title: '{title}'"

        for sig in _CHALLENGE_TEXT_SIGNALS:
            if sig in html_lower:
                return True, f"Challenge signal in body: '{sig}'"

        if tiny_page:
            # Only flag as challenge if there's some access-denial language
            for kw in ("denied", "blocked", "forbidden", "captcha", "robot", "human"):
                if kw in html_lower:
                    return True, f"Tiny page ({len(html)}B) with access-denial signal: '{kw}'"

    except Exception:
        pass
    return False, ""


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

# Fix 2+3: Pagination selectors split by trust level.
# SAFE: highly specific — always tried regardless of page type.
# CONDITIONAL: only tried when _is_genuine_listing() confirms a real paginated view.
# REMOVED: a:has-text('Next') — too broad, false-fires on carousel arrows (Amazon).
_PAGINATION_SAFE = [
    "a.morelink",    # Hacker News — page-type-specific class, never on homepages
    "a[rel='next']", # Standard HTML rel attribute — explicit, unambiguous
]
_PAGINATION_CONDITIONAL = [
    ".pagination__next",
    ".pagination a.next",
    "[aria-label='Next page']",
    "[aria-label='Go to next page']",
    "[class*='pagination'] a[href]",
]


from urllib.parse import urlparse as _urlparse, parse_qs as _parse_qs


def _is_genuine_listing(url: str, html_lower: str) -> bool:
    """Return True only when the page is a genuine paginated listing.

    Fix 2: Pagination is opt-in — only fire conditional selectors when
    the page gives explicit evidence of being a numbered listing.
    A homepage or article is NEVER a genuine listing.
    """
    parsed = _urlparse(url)
    qs = _parse_qs(parsed.query)

    # URL already carries a page/offset cursor → definitely a listing
    if any(k in qs for k in ("page", "p", "offset", "start", "pg", "pagenum", "from")):
        return True

    # URL path signals a listing or search context
    listing_path_hints = (
        "/search", "/results", "/listing", "/listings", "/category",
        "/products", "/browse", "/shop", "/catalog", "/items",
        "/collection", "/archive", "/tag", "/topics",
    )
    if any(parsed.path.startswith(h) for h in listing_path_hints):
        return True

    # HTML contains explicit pagination markup
    pagination_signals = (
        'rel="next"', "rel='next'",
        'class="pagination"', 'class="pager"',
        'aria-label="pagination"', 'aria-label="page navigation"',
        'class="page-numbers"',
    )
    if any(sig in html_lower for sig in pagination_signals):
        return True

    return False


def _next_url_valid(original_url: str, new_url: str) -> bool:
    """Fix 3: Validate that the paginated URL is a credible next-page candidate.

    Rejects candidates where:
    - Domain changed (navigated off-site)
    - Path prefix changed significantly (carousel false-click, e.g. / → /s?)
    """
    try:
        orig = _urlparse(original_url)
        nxt  = _urlparse(new_url)
        if orig.netloc != nxt.netloc:
            return False  # crossed domain boundary
        # Paths must share the same leading segment (e.g. /news → /news?p=2 OK)
        # But /  → /s?hidden-keywords=...  is a different path → reject
        orig_base = orig.path.rstrip("/").split("/")[0:2]  # first two segments
        nxt_base  = nxt.path.rstrip("/").split("/")[0:2]
        if orig_base != nxt_base and orig.path.strip("/") != "":
            return False  # path diverged from a non-root origin
        return True
    except Exception:
        return False

# Null-safe scroll height JS (guard against headless pages without body)
_JS_SCROLL_HEIGHT = (
    "document.body ? document.body.scrollHeight "
    ": document.documentElement.scrollHeight"
)
_JS_SCROLL_BOTTOM = (
    "window.scrollTo(0, document.body ? document.body.scrollHeight "
    ": document.documentElement.scrollHeight)"
)
# ── Thread pool for Windows SelectorEventLoop fallback ───────────────────────
# With uvicorn --reload, the main event loop is SelectorEventLoop which can't
# spawn subprocesses (needed by Playwright). We run Playwright in a thread
# with its own ProactorEventLoop instead.
_PLAYWRIGHT_THREAD_POOL = ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="playwright-proactor"
)

# ── Stealth init script ───────────────────────────────────────────────────────
# Hides automation markers before any page script runs.
_STEALTH_SCRIPT = """
// Hide navigator.webdriver
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined,
    configurable: true,
});

// Inject window.chrome (present in real Chrome, absent in headless)
if (!window.chrome) {
    window.chrome = {
        app: { isInstalled: false },
        runtime: {},
    };
}

// Fake at least 3 plugins (real Chrome has 3+)
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const arr = [{ name: 'PDF Viewer' }, { name: 'Chrome PDF Viewer' }, { name: 'Chromium PDF Viewer' }];
        arr.item  = (i) => arr[i];
        arr.namedItem = (n) => arr.find(p => p.name === n) || null;
        arr.refresh = () => {};
        Object.defineProperty(arr, 'length', { get: () => 3 });
        return arr;
    },
    configurable: true,
});

// Languages — set to realistic value
Object.defineProperty(navigator, 'languages', {
    get: () => ['en-US', 'en'],
    configurable: true,
});
"""


async def js_scrape(url: str) -> BrowserResult:
    """Public entry point — routes to ProactorEventLoop thread on Windows if needed.

    With uvicorn --reload, the server's event loop is SelectorEventLoop which
    cannot spawn subprocesses (Playwright requirement). This wrapper detects that
    case and runs Playwright in a ThreadPoolExecutor with its own ProactorEventLoop.
    On other platforms or when already on ProactorEventLoop, delegates directly.
    """
    if sys.platform == "win32":
        current_loop = asyncio.get_event_loop()
        if not isinstance(current_loop, asyncio.ProactorEventLoop):
            # SelectorEventLoop detected (uvicorn --reload mode).
            # Run Playwright in a thread with a dedicated ProactorEventLoop.
            def _run_in_proactor() -> BrowserResult:
                proactor_loop = asyncio.ProactorEventLoop()
                try:
                    return proactor_loop.run_until_complete(_js_scrape_impl(url))
                finally:
                    proactor_loop.close()

            return await current_loop.run_in_executor(
                _PLAYWRIGHT_THREAD_POOL, _run_in_proactor
            )

    return await _js_scrape_impl(url)


async def _js_scrape_impl(url: str) -> BrowserResult:
    """Internal Playwright implementation — always runs inside a ProactorEventLoop.

    F1: Returns BrowserResult instead of fragile (html, interactions, error) tuple.
    F2: Detects and reports challenge/block pages honestly without bypassing them.
    Stealth: hides webdriver markers before page scripts run.
    """
    result = BrowserResult(
        pages=[url],
        final_url=url,
        browser_attempted=True,
    )
    t0 = time.perf_counter()

    browser = None
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    # Stealth: remove automation flag from Blink feature list
                    "--disable-blink-features=AutomationControlled",
                    # Stability on headless Windows
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
                locale="en-US",
                timezone_id="America/New_York",
            )
            page = await context.new_page()

            # ── Navigate ──────────────────────────────────────────────────────
            try:
                response = await page.goto(
                    url, timeout=BROWSER_TIMEOUT, wait_until="domcontentloaded"
                )
                if response:
                    result.navigation_status = response.status
                try:
                    normalize_url(page.url)
                except URLPolicyError as exc:
                    result.browser_error = f"Browser navigation blocked by URL policy: {exc}"
                    result.errors.append({"message": result.browser_error, "phase": "navigation", "recoverable": False})
                    return result
                # Try networkidle for richer content, fall back silently on timeout
                try:
                    await page.wait_for_load_state("networkidle", timeout=1_500)
                except PlaywrightTimeout:
                    pass
                await page.wait_for_timeout(BROWSER_WAIT_AFTER_LOAD)
            except PlaywrightTimeout:
                result.browser_error = "Timeout while loading the page"
                result.errors.append({
                    "message": result.browser_error,
                    "phase": "render",
                    "recoverable": False,
                })
                return result

            # Fix 1: Capture the initial page HTML immediately after load.
            # This is what the user actually requested. Subsequent navigation
            # (pagination, carousel clicks) must NOT overwrite this.
            try:
                result.initial_html = await page.content()
                result.initial_url  = page.url
                result.html         = result.initial_html
                result.final_url    = result.initial_url
            except Exception as exc:
                result.warnings.append(f"Initial HTML capture failed: {exc}")

            # F2: Challenge/block detection — check BEFORE noise removal
            try:
                page_html_check = await page.content()
                page_title = await page.title()
                page_url_now = page.url
                is_challenge, challenge_reason = _detect_challenge(
                    page_html_check, page_title, page_url_now
                )
                if is_challenge:
                    result.blocked = True
                    result.challenge_detected = True
                    result.block_reason = challenge_reason
                    result.html = page_html_check
                    result.final_url = page_url_now
                    result.final_html_length = len(page_html_check)
                    result.render_duration_ms = round((time.perf_counter() - t0) * 1000, 1)
                    result.warnings.append(
                        f"Browser rendering encountered an access challenge: {challenge_reason}"
                    )
                    await context.close()
                    return result
            except Exception as exc:
                result.warnings.append(f"Challenge detection failed: {exc}")

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
                                result.clicks.append(f"{selector}[{i}]")
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
                                result.clicks.append(selector)
                                total_clicks += 1
                                await page.wait_for_timeout(1_200)
                            except Exception:
                                break
                        break
                except Exception:
                    continue

            # ── Pagination — Fix 2+3: validated, context-gated ────────────────
            visited: set[str] = {normalize_for_dedup(url)}
            current_html_lower = result.initial_html.lower()
            listing_confirmed  = _is_genuine_listing(result.initial_url, current_html_lower)

            for _depth in range(MAX_PAGES - 1):  # already counted page 1
                navigated = False

                # Build selector list: safe ones always, conditional only for listings
                selectors_to_try = list(_PAGINATION_SAFE)
                if listing_confirmed:
                    selectors_to_try += _PAGINATION_CONDITIONAL

                for selector in selectors_to_try:
                    try:
                        btn = page.locator(selector).first
                        if not await btn.is_visible(timeout=2_000):
                            continue

                        # Peek at the href before clicking
                        candidate_href = ""
                        try:
                            candidate_href = await btn.get_attribute("href") or ""
                        except Exception:
                            pass

                        original_page_url = page.url
                        await btn.click(timeout=5_000)
                        await page.wait_for_load_state("domcontentloaded")
                        await page.wait_for_timeout(BROWSER_WAIT_AFTER_NAV)

                        new_url = await page.evaluate("window.location.href")

                        # Fix 3: Validate new URL is a credible next-page candidate
                        if not _next_url_valid(result.initial_url, new_url):
                            # Navigated to a different path — back out
                            result.warnings.append(
                                f"Pagination candidate '{selector}' led to unrelated URL "
                                f"{new_url!r} (from {result.initial_url!r}); skipping."
                            )
                            try:
                                await page.go_back()
                                await page.wait_for_load_state("domcontentloaded")
                            except Exception:
                                pass
                            continue

                        norm = normalize_for_dedup(new_url)
                        if norm in visited:
                            break  # cycle detected

                        visited.add(norm)
                        result.pages.append(new_url)
                        result.clicks.append(f"pagination[{selector}]")
                        navigated = True
                        break
                    except Exception:
                        continue

                if not navigated:
                    break

            # ── Infinite scroll — null-safe, bounded ──────────────────────────
            previous_height = 0
            try:
                previous_height = await page.evaluate(_JS_SCROLL_HEIGHT)
            except Exception as exc:
                result.errors.append({
                    "message": str(exc),
                    "phase": "scroll",
                    "recoverable": True,
                })

            try:
                for _ in range(MAX_SCROLLS):
                    await page.evaluate(_JS_SCROLL_BOTTOM)
                    # Fix 5: Wait for lazy-image intersection observers to fire.
                    # 1800ms base + 600ms settle = 2400ms per scroll pass.
                    await page.wait_for_timeout(1_800)
                    result.scrolls += 1  # count every attempt

                    try:
                        new_height = await page.evaluate(_JS_SCROLL_HEIGHT)
                    except Exception as exc:
                        result.errors.append({
                            "message": str(exc),
                            "phase": "scroll",
                            "recoverable": True,
                        })
                        break

                    if new_height > previous_height:
                        result.scrolls_with_change += 1
                        previous_height = new_height
                        # Extra settle time when new content loaded (fix 5)
                        await page.wait_for_timeout(600)
                    else:
                        break  # no more content loading
            except Exception as exc:
                result.errors.append({
                    "message": str(exc),
                    "phase": "scroll",
                    "recoverable": True,
                })

            # ── Fix 1: html/final_url stay as initial page — never overwritten ─
            # The initial page is what the user requested. Interactions (scrolls,
            # pagination) are recorded in result.pages/clicks but do NOT change
            # what gets extracted. Re-capture initial HTML now that scroll has
            # potentially revealed lazy-loaded images on the original page.
            # Only re-capture if we are still on the initial page.
            try:
                current_url_now = page.url
                if _next_url_valid(result.initial_url, current_url_now) and \
                        normalize_for_dedup(current_url_now) == normalize_for_dedup(result.initial_url):
                    # Still on (or very close to) the initial page — refresh capture
                    result.html = await page.content()
                # If we navigated away during interactions, keep initial_html
                result.final_html_length = len(result.html)
            except Exception:
                pass
            result.browser_succeeded = True
            await context.close()

    except PlaywrightTimeout:
        result.browser_error = "Timeout during JS rendering"
        result.errors.append({
            "message": result.browser_error,
            "phase": "render",
            "recoverable": False,
        })
    except Exception as exc:
        result.browser_error = str(exc)
        result.errors.append({
            "message": str(exc),
            "phase": "render",
            "recoverable": False,
        })
    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass

    result.render_duration_ms = round((time.perf_counter() - t0) * 1000, 1)
    return result
