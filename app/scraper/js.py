from playwright.sync_api import sync_playwright, TimeoutError
import time

TAB_SELECTORS = [
    "[role='tab']",
    "button[aria-selected]",
    ".tab",
    "[data-tab]"
]

LOAD_MORE_SELECTORS = [
    "button:has-text('Load more')",
    "button:has-text('Show more')",
    "button:has-text('See more')",
    "a:has-text('Load more')",
    ".load-more",
    "[class*='load-more']",
    "[id*='load-more']"
]

PAGINATION_SELECTORS = [
    "a[rel='next']",
    "a:has-text('Next')",
    ".pagination a.next",
    ".pagination__next",
    "[aria-label='Next page']",
    "a.morelink"   # 🔥 Hacker News
]


def js_scrape(url: str):
    interactions = {
        "clicks": [],
        "scrolls": 0,
        "pages": [url]
    }

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=60000)

            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(1500)

            # ---------------- REMOVE NOISE ----------------
            try:
                page.evaluate("""
                    ['cookie','modal','popup','overlay','dialog','banner'].forEach(k => {
                        document.querySelectorAll(`[class*="${k}"],[id*="${k}"]`)
                        .forEach(el => el.remove());
                    });
                """)
            except:
                pass

            # ---------------- CLICK TABS ----------------
            for selector in TAB_SELECTORS:
                try:
                    tabs = page.locator(selector)
                    if tabs.count() > 1:
                        for i in range(min(3, tabs.count())):
                            tabs.nth(i).click(timeout=2000)
                            interactions["clicks"].append(f"{selector}[{i}]")
                            page.wait_for_timeout(700)
                        break
                except:
                    continue

            # ---------------- LOAD MORE ----------------
            for selector in LOAD_MORE_SELECTORS:
                try:
                    btn = page.locator(selector).first
                    if btn.is_visible(timeout=2000):
                        for _ in range(3):
                            btn.click(timeout=3000)
                            interactions["clicks"].append(selector)
                            page.wait_for_timeout(1200)
                        break
                except:
                    continue

            # ---------------- PAGINATION (DEPTH ≥ 3) ----------------
            current_url = page.url
            for depth in range(3):
                for selector in PAGINATION_SELECTORS:
                    try:
                        next_btn = page.locator(selector).first
                        if next_btn.is_visible(timeout=2000):
                            next_btn.click(timeout=5000)
                            page.wait_for_load_state("domcontentloaded")
                            page.wait_for_timeout(1200)

                            if page.url != current_url:
                                current_url = page.url
                                interactions["pages"].append(current_url)
                                interactions["clicks"].append(f"pagination[{selector}]")
                            break
                    except:
                        continue

            # ---------------- INFINITE SCROLL (FIXED & REAL) ----------------
            previous_height = page.evaluate("document.body.scrollHeight")

            for i in range(4):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1800)

                new_height = page.evaluate("document.body.scrollHeight")
                if new_height > previous_height:
                    interactions["scrolls"] += 1
                    interactions["pages"].append(f"{url}#scroll-{i+1}")
                    previous_height = new_height
                else:
                    break

            html = page.content()
            browser.close()
            return html, interactions, None

    except TimeoutError:
        return "", interactions, "Timeout during JS rendering"
    except Exception as e:
        return "", interactions, str(e)
