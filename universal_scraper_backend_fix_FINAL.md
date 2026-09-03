# Universal Web Scraper — Backend Master Fix Prompt

**Instructions for the coding agent:** Read this entire document before touching a single file. Every diagnosis in this document is based on reading the actual current codebase. Do not skip sections. Do not start coding until you have read and understood all five bug diagnoses.

---

## Part 0 — What You Are Working With

The project is a FastAPI + Playwright web scraper. The architecture is already correct and well-designed. The pipeline in `static.py` is the right approach. You are NOT rewriting the project. You are fixing specific, diagnosed bugs and filling in gaps that are causing wrong output. The code that works must be preserved exactly.

**Current file structure:**
```
app/
  main.py              — FastAPI routes and pipeline orchestration
  scraper/
    static.py          — fetch_page, extract_page, all extraction logic
    js.py              — async Playwright browser engine
    config.py          — all limits and timeouts (do not add magic numbers elsewhere)
    urls.py            — URL validation, SSRF protection, normalization
  frontend/
    templates/
      index.html       — frontend UI (890 lines, correct wiring to backend)
requirements.txt       — all versions pinned correctly
run.sh                 — correct startup script
```

**What works correctly and must not be touched:**
- `config.py` — complete and correct, do not modify
- `urls.py` — complete and correct, do not modify
- `js.py` — architecture is correct, one small bug to fix (documented below)
- `requirements.txt` — correct, do not modify
- `run.sh` — correct, do not modify
- `index.html` — correct wiring, do not modify the JS data-reading logic

---

## Part 1 — The Five Diagnosed Bugs

### Bug 1 — `_metadata()` is called after `_extract_components()` strips the soup

**Location:** `static.py`, inside `extract_page()`

**What happens:** `_extract_components()` runs first and calls `decompose()` on nav, sidebar, TOC, language selector, and footer tags. This is correct — these should be separated. But `_metadata()` is currently called AFTER this destructive step. On Wikipedia, `lxml` sometimes places `<title>` inside the parsed document tree in a position that gets touched by aggressive decompose operations. The result: `meta.title` comes back empty even though Wikipedia's page title is perfectly accessible.

**The fix:** Move the `_metadata()` call to happen BEFORE `_extract_components()`. Metadata lives in `<head>` and is completely independent of body chrome removal. Extract it from the original soup before any decomposition happens.

**Correct order inside `extract_page()`:**
```
1. Parse HTML with BeautifulSoup
2. Extract metadata (FIRST — before any decomposition)
3. Extract structured data (also before decomposition)
4. Extract and remove components (_extract_components)
5. Clean remaining noise (_clean)
6. Find primary content root (_primary_root)
7. Build sections (_build_sections)
8. Deduplicate
9. Merge resources
10. Compose result
```

---

### Bug 2 — `_primary_root()` scores the wrong element after aggressive component removal

**Location:** `static.py`, `_primary_root()` function

**What happens:** After `_extract_components()` removes nav, sidebar, TOC, language selector, and footer from the soup, the remaining DOM is much smaller. `_primary_root()` then scores `div` and `section` candidates. On Wikipedia, the article content lives in `#mw-content-text`. But the scoring function searches `_CONTENT_SELECTORS` AND generic `div`/`section` tags. After component removal, some wrapper divs score higher than the actual content root because their tag bonus or paragraph count is inflated relative to stripped content.

**The fix:** Change `_primary_root()` to use a two-pass strategy:

**Pass 1 — Try high-confidence named selectors first, in order:**
```python
_PRIORITY_SELECTORS = [
    "#mw-content-text",       # Wikipedia article body
    "#bodyContent",           # Wikipedia older skin
    "article",                # Semantic article element
    "main",                   # HTML5 main landmark
    "[role='main']",          # ARIA main role
    "#content",               # Common CMS pattern
    "#main-content",          # Common pattern
    ".article-body",          # News sites
    ".post-content",          # Blog pattern
    ".content",               # Generic fallback
]
```

For each selector, find the first matching element. If it has more than 200 characters of text, return it immediately without scoring. This guarantees Wikipedia always gets `#mw-content-text` without depending on the scoring function.

**Pass 2 — Fall back to scoring only if no priority selector matched:**
Keep the existing `_score()` function and candidate scoring as the fallback for unknown site structures.

---

### Bug 3 — The fallback section produces `text: "html"` when `_build_sections()` returns empty

**Location:** `static.py`, inside `extract_page()`, the fallback section block

**What happens:** When `_build_sections()` returns an empty list (because `_primary_root()` selected a bad root with no heading tags), the fallback code runs:
```python
text = _text(root)[:MAX_TEXT_PER_SECTION]
```

If `root` at this point is the soup document itself (not a real content element), `_text()` iterates `root.descendants` and finds a `Doctype` NavigableString with value `"html"`. This becomes `text: "html"` and `rawHtml: "<!DOCTYPE html>\n"`.

**The fix:** Two parts:

Part A — After Bug 2 fix, `_primary_root()` should return the correct content root so this fallback rarely triggers. But add an explicit guard anyway:

```python
# In the fallback section block:
text = _text(root)[:MAX_TEXT_PER_SECTION]
# Guard: if text is a single word under 10 chars, the root was wrong
if len(text.strip()) < 10:
    # Try body directly
    body = soup.body
    if body:
        text = _text(body)[:MAX_TEXT_PER_SECTION]
```

Part B — The fallback section label should use `meta["title"]` if available (after Bug 1 fix, this will be populated). Currently it uses `meta.get("title") or "Content"` — this is correct but only works after Bug 1 is fixed.

---

### Bug 4 — `_build_sections()` is called on a root that may not contain the article headings

**Location:** `static.py`, `_build_sections()` function

**What happens:** `_build_sections()` calls `root.find_all(re.compile(r"^h[1-6]$"))` to find all headings within the root. On Wikipedia, after aggressive component removal in Bug 2's broken state, if the wrong root is selected (e.g. a sidebar wrapper that happened to survive), `find_all` finds zero headings and returns empty list. This cascades into Bug 3.

**The fix:** After Bug 2 is fixed, this resolves itself because `_primary_root()` will correctly return `#mw-content-text` which contains all Wikipedia article headings. No separate fix needed — but add a diagnostic warning to `errors[]` when `_build_sections()` returns empty from a non-empty root:

```python
if not sections and root and len(_text(root)) > 200:
    errors.append({
        "message": f"Section builder found no headings in root element ({root.name}#{root.get('id', '')}). Root has {len(_text(root))} chars of text.",
        "phase": "extract",
        "recoverable": True
    })
```

This gives visible diagnostic information without crashing.

---

### Bug 5 — `analyze_static_quality()` can incorrectly return `JS_REQUIRED` for Wikipedia

**Location:** `static.py`, `analyze_static_quality()` function

**What happens:** The function checks:
```python
has_root_app = (
    'id="root"' in html_lower or "id='root'" in html_lower or
    'id="app"'  in html_lower or "id='app'"  in html_lower
)
```

Wikipedia's HTML contains `id="app"` on one of its internal elements (Wikipedia uses Vue.js for some UI components). Combined with the `p_count < 3` check failing on the raw HTML (Wikipedia has many `<p>` tags), this specific check may not trigger. But on other sites that do trigger it, the SPA detection is fine.

The real issue here is sequence: `analyze_static_quality()` is called with the result of `extract_page()`. But if `extract_page()` returned broken sections due to Bugs 1-3, `analyze_static_quality()` sees zero sections and returns `STATIC_EMPTY`, triggering Playwright unnecessarily for Wikipedia.

**The fix:** After Bugs 1-3 are fixed, Wikipedia will have real sections and `analyze_static_quality()` will correctly return `STATIC_COMPLETE`. No code change needed in the function itself. But add one additional check: if `total_text` is zero but the raw HTML is large (over 50,000 chars), return `STATIC_PARTIAL` instead of `STATIC_EMPTY` to avoid triggering full browser rendering on static pages where extraction merely failed:

```python
if not sections or total_text == 0:
    # Large HTML but no extracted text = extraction bug, not JS page
    if len(html) > 50_000:
        return STATIC_PARTIAL
    return STATIC_EMPTY
```

---

## Part 2 — Additional Fixes Required

### Fix A — `_extract_components()` sidebar removal is too aggressive

**Location:** `static.py`, `_extract_components()`, sidebar section

**Current code:** Removes any `div`, `aside`, or `section` with "sidebar" in class/id.

**Problem:** Wikipedia's `#mw-content-text` is inside a `div` that has sibling divs with sidebar-like classes. The sibling removal is fine, but if the selector accidentally matches a parent of `#mw-content-text`, it removes the article content before `_primary_root()` can find it.

**Fix:** Add a minimum size check before sidebar decompose:
```python
# Only remove sidebar if it contains less than 500 chars of text
# (real sidebars are small; article content containers are large)
for candidate in sidebar_candidates:
    if len(_safe_text(candidate)) < 500:
        sidebar_tags.append(candidate)
    # else: too much content to be a sidebar, skip it
```

---

### Fix B — `_clean()` removes `[aria-hidden="true"]` elements but Wikipedia uses these for decorative icons

**Location:** `static.py`, `_clean()` function

**Problem:** The line `for tag in list(soup.find_all(attrs={"aria-hidden": "true"})):` removes elements with `aria-hidden="true"`. Wikipedia uses `aria-hidden="true"` on SVG icons embedded in text, which is correct accessibility practice. But it also uses it on empty decorative spans. Removing all `aria-hidden` elements is fine. The issue is that Wikipedia's edit section links and some structural elements have this attribute and their removal can create gaps in the heading structure.

**Fix:** Keep the `aria-hidden` removal but add an exception for heading tags:
```python
for tag in list(soup.find_all(attrs={"aria-hidden": "true"})):
    # Never remove heading tags even if aria-hidden
    if tag.name and re.match(r"^h[1-6]$", tag.name):
        continue
    try:
        tag.decompose()
    except Exception:
        pass
```

---

### Fix C — `js.py` pagination selector for Hacker News

**Location:** `js.py`, `_PAGINATION_SELECTORS`

**Current:** `"a.morelink"` is listed but Hacker News uses class `morelink` on an anchor. The `:has-text()` selectors like `"a:has-text('Next')"` require Playwright's extended CSS which works in `page.locator()` but the current code uses `page.locator(selector).first` — this is correct. The real issue is that `"a.morelink"` should be the FIRST selector tried since it's specific and reliable.

**Fix:** Reorder `_PAGINATION_SELECTORS` to put the most reliable selectors first:
```python
_PAGINATION_SELECTORS = [
    "a.morelink",               # Hacker News (highly specific)
    "a[rel='next']",            # Standard rel=next
    ".pagination__next",        # Common framework pattern
    ".pagination a.next",       # Common pattern
    "[aria-label='Next page']", # ARIA pattern
    "a:has-text('Next')",       # Text-based fallback
]
```

---

### Fix D — `main.py` error response for failed fetch returns 502 which frontend may not handle

**Location:** `main.py`, fetch error block

**Current:** Returns `JSONResponse(status_code=502, content=_failure(...))`.

**Problem:** The frontend `index.html` JavaScript catches the response and reads `data.result`. A 502 with JSON body works in some fetch implementations but may throw in others depending on the `Content-Type` header. The frontend's `catch` block shows a generic error.

**Fix:** Change the failed fetch response to return HTTP 200 with the error in the result body, consistent with how partial results are returned:
```python
if source.error:
    # Return 200 with schema-compliant error body — frontend reads result.errors[]
    return _failure(url, source.error, "fetch")
    # Remove the JSONResponse(status_code=502) wrapper
```

The `_failure()` function already produces a correct schema-compliant body. Wrap it in a plain `return` not `JSONResponse(status_code=502)`. The frontend already reads `result.errors[]` and displays them correctly.

---

### Fix E — Double `normalize_url()` call causes DNS lookup twice per request

**Location:** `main.py` calls `normalize_url(data.url)`, then `fetch_page(url)` calls `normalize_url(url)` again internally.

**Problem:** `normalize_url()` does a `socket.getaddrinfo()` DNS lookup for SSRF protection. This DNS lookup happens TWICE per request — once in `main.py` and once inside `fetch_page()`. On slow DNS servers or restricted environments, this doubles the validation latency and can cause timeout differences between the two calls.

**Fix:** Remove the internal `normalize_url()` call from `fetch_page()`. Since `main.py` already validates and normalizes the URL before calling `fetch_page()`, the second normalization is redundant:

```python
# In fetch_page(), change:
async def fetch_page(url: str) -> PageSource:
    t0 = time.perf_counter()
    try:
        current_url = normalize_url(url)  # ← REMOVE THIS
    except URLPolicyError as exc:
        return PageSource(url, url, "", None, "", str(exc))
    # Change to:
    current_url = url  # Already normalized by caller
```

Keep the `try/except URLPolicyError` block only in `main.py` where the user input first arrives.

---

## Part 3 — Implementation Order

**Do not reorder these steps.** Each step must complete and be verified before the next begins.

**Step 1 — Fix Bug 1 (metadata order).** Move `_metadata()` call before `_extract_components()` in `extract_page()`. Test: scrape Wikipedia and confirm `meta.title` returns `"Artificial intelligence - Wikipedia"`.

**Step 2 — Fix Bug 2 (primary root selection).** Implement the two-pass `_primary_root()` with priority selectors first. Test: scrape Wikipedia and confirm sections contain real article content (Goals, Techniques, History etc.), not nav chrome.

**Step 3 — Fix Bug 3 (fallback section guard).** Add the `len(text.strip()) < 10` guard in the fallback section block. Test: confirm no section has `text: "html"` or `rawHtml: "<!DOCTYPE html>\n"`.

**Step 4 — Fix Bug 4 (diagnostic warning).** Add the diagnostic error when `_build_sections()` returns empty from a non-empty root. Test: confirm the warning appears when expected and does not appear after Bugs 2-3 are fixed.

**Step 5 — Fix Bug 5 (quality analyzer).** Add the large-HTML guard to `analyze_static_quality()`. Test: confirm Wikipedia is classified as `STATIC_COMPLETE` after Bugs 1-3 are fixed.

**Step 6 — Fix A (sidebar size guard).** Add the 500-char minimum check before sidebar decompose. Test: scrape MDN and confirm article content is not accidentally removed.

**Step 7 — Fix B (aria-hidden heading exception).** Add heading tag exception to `_clean()`. Test: confirm heading tags survive even if they have `aria-hidden="true"`.

**Step 8 — Fix C (pagination selector order).** Reorder `_PAGINATION_SELECTORS` in `js.py`. Test: scrape Hacker News and confirm `interactions.pages` contains at least 3 entries.

**Step 9 — Fix D (fetch error response).** Change `JSONResponse(status_code=502)` to plain `return _failure(...)`. Test: scrape an invalid URL and confirm the frontend displays the error correctly.

**Step 10 — Fix E (double DNS lookup).** Remove redundant `normalize_url()` call inside `fetch_page()`. Test: confirm requests are not slower than before.

---

## Part 4 — Verification Tests

After all fixes, verify all four URLs pass before considering the work done.

**Test 1 — Wikipedia (static extraction)**
```
POST /scrape {"url": "https://en.wikipedia.org/wiki/Artificial_intelligence"}
```
Expected:
- `meta.title` = `"Artificial intelligence - Wikipedia"` (NOT empty)
- `meta.strategy` = `"static"`
- `sections` length >= 10 (real article sections: Goals, Techniques, History, Ethics, etc.)
- `sections[0].content.text` NOT `"html"` — must be real article text
- `sections[0].rawHtml` NOT `"<!DOCTYPE html>\n"` — must be real HTML
- `components.tableOfContents` contains Wikipedia TOC items (already working)
- `components.languageSelector` populated (already working)
- `stats.linksFound` > 0
- `errors` is empty or contains only recoverable warnings

**Test 2 — MDN (static extraction, different site structure)**
```
POST /scrape {"url": "https://developer.mozilla.org/en-US/docs/Web/JavaScript"}
```
Expected:
- `meta.title` populated
- `meta.description` populated
- `sections` length >= 3 with real documentation content
- `meta.strategy` = `"static"`
- All link hrefs are absolute URLs starting with `https://`

**Test 3 — Hacker News (pagination)**
```
POST /scrape {"url": "https://news.ycombinator.com/"}
```
Expected:
- `meta.strategy` = `"js"` (Hacker News is in known JS-heavy patterns)
- `interactions.pages` contains >= 3 distinct absolute URLs
- `interactions.clicks` contains at least 1 entry with `"pagination"` in the string
- `sections` contains real story items

**Test 4 — Vercel (JS rendering)**
```
POST /scrape {"url": "https://vercel.com/"}
```
Expected:
- `meta.strategy` = `"js"`
- `sections` contains real marketing content
- `interactions.scrolls` > 0
- `errors` does not contain `"scrollHeight"` null reference errors

**Test 5 — Security validation**
```
POST /scrape {"url": "file:///etc/passwd"}
POST /scrape {"url": "http://localhost:8000/healthz"}
POST /scrape {"url": "http://192.168.1.1/"}
```
Expected: All three return 400 with `errors[0].phase = "validation"` and descriptive messages. No network requests made.

**Test 6 — Health check**
```
GET /healthz
```
Expected: `{"status": "ok"}`

---

## Part 5 — Things You Must Not Change

- Do not change `config.py` — all limits are correct
- Do not change `urls.py` except to remove the internal call from `fetch_page()` (Fix E)
- Do not change `requirements.txt` — all versions are pinned correctly
- Do not change `run.sh`
- Do not change the JSON response schema shape — the frontend reads specific field paths
- Do not add new dependencies
- Do not add new files unless absolutely necessary
- Do not change the frontend `index.html` JavaScript data-reading logic
- Do not rewrite `js.py` — only reorder `_PAGINATION_SELECTORS` and keep existing logic
- Do not change `_score()` function — it is used as the fallback in the two-pass root selection

---

## Part 6 — After All Fixes Pass

Update `design_notes.md` with accurate descriptions of the fixed behavior. Specifically update:

**Static vs JS Fallback section:** Describe the two-pass `_primary_root()` — priority selectors first, scoring fallback second. Describe the four quality states: `STATIC_COMPLETE`, `STATIC_PARTIAL`, `STATIC_EMPTY`, `JS_REQUIRED`.

**Section Grouping & Labels section:** Describe that metadata is extracted BEFORE component removal. Describe that `#mw-content-text` is hit as a priority selector for Wikipedia-style pages.

**Noise Filtering & Truncation section:** Describe the sidebar size guard (500-char minimum) and the aria-hidden heading exception.

Update `capabilities.json` — all booleans must reflect only verified and tested capabilities after the fixes pass all 6 verification tests above.

---

## Part 7 — Root Cause Summary for Reference

The broken output (`text: "html"`, `rawHtml: "<!DOCTYPE html>\n"`, `meta.title: ""`) is caused by a cascade:

1. `_metadata()` called after `_extract_components()` → title extraction unreliable
2. After aggressive component removal, `_primary_root()` scores wrong element (not `#mw-content-text`)
3. Wrong root has no `h1-h6` headings → `_build_sections()` returns `[]`
4. Fallback section runs `_text(root)` on near-empty soup → gets Doctype NavigableString `"html"`
5. Result: one fake section with `text: "html"`, empty meta, zero links/images

Fix order: Bug 1 → Bug 2 → Bug 3 → verify → remaining fixes. The entire broken output chain resolves from fixing just Bugs 1 and 2 in the correct order.
