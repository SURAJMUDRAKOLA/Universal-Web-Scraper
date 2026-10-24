"""FastAPI surface for the Universal Web Scraper v2.

Pipeline per request:
  1. URL validation & normalization (urls.py)
  2. Async static fetch (static.py fetch_page)
  3. Static extraction → static_result (extract_page)
  4. Static quality analysis — STATIC_COMPLETE / PARTIAL / EMPTY / JS_REQUIRED
  5. Optional browser render (js.py js_scrape) → BrowserResult
  6. If browser succeeded: extract browser_result, then MERGE (never replace)
  7. Schema composition + legacy field compat
  8. Return structured JSON
"""
import asyncio
import sys
import time
from datetime import datetime, timezone

# Windows: Playwright needs ProactorEventLoop to create subprocesses.
# Without this, asyncio.create_subprocess_exec raises NotImplementedError
# when uvicorn runs on Python 3.13+ on Windows.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.scraper.static import (
    PageSource,
    analyze_static_quality,
    extract_page,
    fetch_page,
    JS_REQUIRED,
    STATIC_COMPLETE,
    STATIC_EMPTY,
    STATIC_PARTIAL,
)
from app.scraper.js import js_scrape, BrowserResult
from app.scraper.urls import URLPolicyError, normalize_url

app = FastAPI(title="Universal Web Scraper", version="2.0")
templates = Jinja2Templates(directory="app/frontend/templates")


# ─────────────────────────────────────────────────────────────────────────────
# Request model
# ─────────────────────────────────────────────────────────────────────────────

class ScrapeRequest(BaseModel):
    url: str


# ─────────────────────────────────────────────────────────────────────────────
# Health check & frontend
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# ─────────────────────────────────────────────────────────────────────────────
# Schema-compliant failure response — used for validation and fatal errors
# ─────────────────────────────────────────────────────────────────────────────

def _failure(url: str, message: str, phase: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "result": {
            "url": url,
            "scrapedAt": now,
            "meta": {
                "title": "", "description": "", "language": "en",
                "canonical": None, "strategy": "none",
            },
            "sections": [],
            "components": {
                "navigation": [], "tableOfContents": [],
                "languageSelector": [], "sidebar": [], "footer": [],
            },
            "interactions": {"clicks": [], "scrolls": 0, "pages": []},
            "stats": {
                "fetchDurationMs": 0, "renderDurationMs": 0,
                "totalDurationMs": 0, "sectionsExtracted": 0,
                "sectionsDeduped": 0, "linksFound": 0, "imagesFound": 0,
            },
            "errors": [{"message": message, "phase": phase, "recoverable": False}],
            "attempt": {
                "outcome": "failed" if phase != "validation" else "unsupported",
                "requestSucceeded": False,
                "httpStatus": None,
                "browserAttempted": False,
                "browserLaunched": False,
                "navigationSucceeded": False,
                "extractionSucceeded": False,
                "challengeDetected": False,
                "partial": False,
            },
        }
    }


# ─────────────────────────────────────────────────────────────────────────────
# Global exception handler
# ─────────────────────────────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    requested = ""
    try:
        body = await request.json()
        requested = body.get("url", "")
    except Exception:
        requested = str(request.url)
    return JSONResponse(
        status_code=500,
        content=_failure(requested, "Unexpected server error", "internal"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# F3: True static-browser merge — never replace, always augment
# ─────────────────────────────────────────────────────────────────────────────

def _merge_results(static_result: dict, browser_result: dict) -> dict:
    """Merge browser extraction into static extraction without destroying static data.

    Rules (blueprint §17):
    - Prefer richer (non-empty) metadata fields — static wins if browser is empty
    - Add browser sections not already present (fingerprint-based dedup)
    - Merge resources: union of links/images (deduplicated by href/src)
    - Preserve static errors/warnings; append browser errors as recoverable
    """
    import hashlib, json as _json

    merged = dict(static_result)  # shallow copy — we'll replace specific sub-dicts

    # ── Fix 4: Metadata — title priority, not length ─────────────────────────
    # Titles known to be acquisition artefacts, not real page titles.
    _GARBAGE_TITLES = {
        "javascript is disabled",
        "javascript is not available",
        "enable javascript",
        "attention required",
        "access denied",
        "just a moment",
        "403 forbidden",
        "please wait",
        "are you human",
        "checking your browser",
        "ddos-guard",
    }

    def _is_garbage_title(t: str) -> bool:
        return not t or t.lower().strip() in _GARBAGE_TITLES

    s_meta = static_result.get("metadata", {})
    b_meta = browser_result.get("metadata", {})
    merged_meta = {}

    # Merge all keys, preferring non-empty values (static wins on tie)
    for key in set(list(s_meta.keys()) + list(b_meta.keys())):
        s_val = s_meta.get(key, "")
        b_val = b_meta.get(key, "")
        merged_meta[key] = s_val if s_val else b_val

    # Title override: garbage titles must lose to any real title.
    # Priority: good browser title > good static title > any title.
    s_title = s_meta.get("title", "")
    b_title = b_meta.get("title", "")
    if not _is_garbage_title(b_title):
        merged_meta["title"] = b_title       # Browser got a real title
    elif not _is_garbage_title(s_title):
        merged_meta["title"] = s_title       # Static had a real title
    else:
        merged_meta["title"] = b_title or s_title  # Both bad — use whatever we have

    merged["metadata"] = merged_meta

    # ── Sections: add browser-only sections ──────────────────────────────────
    def _sec_fp(sec):
        h = "|".join(sec.get("content", {}).get("headings", []))
        t = sec.get("content", {}).get("text", "")[:100].strip()
        return hashlib.md5(f"{h}::{t}".encode()).hexdigest()

    s_sections = static_result.get("content", {}).get("sections", [])
    b_sections = browser_result.get("content", {}).get("sections", [])
    existing_fps = {_sec_fp(s) for s in s_sections}
    added = [s for s in b_sections if _sec_fp(s) not in existing_fps]
    merged_sections = s_sections + added
    merged["content"] = dict(static_result.get("content", {}))
    merged["content"]["sections"] = merged_sections

    # ── Resources: union deduplicated ─────────────────────────────────────────
    s_res = static_result.get("resources", {})
    b_res = browser_result.get("resources", {})
    merged_resources = {}
    for key in ("links", "images", "lists", "tables", "videos", "audio", "documents"):
        s_items = s_res.get(key, [])
        b_items = b_res.get(key, [])
        seen = set()
        deduped = []
        for item in s_items + b_items:
            try:
                k = _json.dumps(item, sort_keys=True)
                if k not in seen:
                    seen.add(k)
                    deduped.append(item)
            except Exception:
                deduped.append(item)
        merged_resources[key] = deduped
    merged["resources"] = merged_resources

    # ── Statistics: recalculate from merged data ──────────────────────────────
    s_stats = static_result.get("statistics", {})
    merged["statistics"] = dict(s_stats)
    merged["statistics"]["sections"] = len(merged_sections)
    merged["statistics"]["linksFound"] = len(merged_resources.get("links", []))
    merged["statistics"]["imagesFound"] = len(merged_resources.get("images", []))

    # ── Errors/warnings: append browser errors as recoverable ─────────────────
    s_errors = list(static_result.get("errors", []))
    b_errors = browser_result.get("errors", [])
    for e in b_errors:
        e_copy = dict(e)
        e_copy["recoverable"] = True
        s_errors.append(e_copy)
    merged["errors"] = s_errors
    merged["warnings"] = list(static_result.get("warnings", [])) + list(browser_result.get("warnings", []))

    return merged


# ─────────────────────────────────────────────────────────────────────────────
# F6: Count links from components into global stats
# ─────────────────────────────────────────────────────────────────────────────

def _count_component_links(components: dict) -> int:
    """Count links found in navigation, footer, sidebar etc. for global linksFound."""
    total = 0
    for comp_list in components.values():
        if not isinstance(comp_list, list):
            continue
        for comp in comp_list:
            if isinstance(comp, dict):
                # nav items are stored as {text, href} directly or inside {items}
                if "href" in comp:
                    total += 1
                if "items" in comp and isinstance(comp["items"], list):
                    total += len([i for i in comp["items"] if isinstance(i, dict) and "href" in i])
    return total


# ─────────────────────────────────────────────────────────────────────────────
# Helper — compose final schema-compliant response
# ─────────────────────────────────────────────────────────────────────────────

def _compose_response(
    url: str,
    result: dict,
    interactions: dict,
    strategy: str,
    browser_res: BrowserResult | None,
    t_start: float,
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    meta_raw = result.get("metadata", {})
    stats_raw = result.get("statistics", {})

    total_ms = round((time.perf_counter() - t_start) * 1000, 1)
    fetch_ms = stats_raw.get("fetchDurationMs", 0.0)
    render_ms = browser_res.render_duration_ms if browser_res else 0.0

    sections = result.get("content", {}).get("sections", [])
    components = result.get("components", {
        "navigation": [], "tableOfContents": [],
        "languageSelector": [], "sidebar": [], "footer": [],
    })

    # F6: Global linksFound = section links + component links (nav, footer, etc.)
    section_links = stats_raw.get("linksFound", len(result.get("resources", {}).get("links", [])))
    component_links = _count_component_links(components)
    total_links_found = section_links + component_links

    meta = {
        "title":       meta_raw.get("title", ""),
        "description": meta_raw.get("description", ""),
        "language":    meta_raw.get("language", "en"),
        "canonical":   meta_raw.get("canonical"),
        "strategy":    strategy,
        # F2: expose acquisition diagnostics
        "browserAttempted":   browser_res.browser_attempted if browser_res else False,
        "browserSucceeded":   browser_res.browser_succeeded if browser_res else False,
        "blocked":            browser_res.blocked if browser_res else False,
        "challengeDetected":  browser_res.challenge_detected if browser_res else False,
        "blockReason":        browser_res.block_reason if browser_res else "",
    }

    stats = {
        "fetchDurationMs":   fetch_ms,
        "renderDurationMs":  render_ms,
        "totalDurationMs":   total_ms,
        "sectionsExtracted": stats_raw.get("sections", len(sections)),
        "sectionsDeduped":   stats_raw.get("sectionsDeduped", 0),
        "linksFound":        total_links_found,
        "imagesFound":       stats_raw.get("imagesFound", len(result.get("resources", {}).get("images", []))),
        "pagesVisited":      len(interactions.get("pages", [])),
        "scrollsAttempted":  browser_res.scrolls if browser_res else 0,
        "scrollsWithChange": browser_res.scrolls_with_change if browser_res else 0,
    }

    errors = list(result.get("errors", []))
    warnings = list(result.get("warnings", []))

    # Convert any warnings → errors with recoverable=True
    for w in warnings:
        if isinstance(w, str):
            errors.append({"message": w, "phase": "warning", "recoverable": True})
        elif isinstance(w, dict):
            errors.append({
                "message": w.get("message", str(w)),
                "phase": w.get("stage", "unknown"),
                "recoverable": True,
            })

    usable = bool(sections) or any(result.get("resources", {}).get(name) for name in ("links", "images", "tables", "videos", "audio", "documents", "records"))
    if browser_res and browser_res.challenge_detected:
        outcome = "partial" if usable else "challenge_detected"
    elif browser_res and browser_res.blocked:
        outcome = "partial" if usable else "access_denied"
    elif usable and errors:
        outcome = "partial"
    elif usable:
        outcome = "success"
    elif errors:
        outcome = "failed"
    else:
        outcome = "empty"
    attempt = {
        "outcome": outcome,
        "requestSucceeded": result.get("page", {}).get("httpStatus") is not None,
        "httpStatus": result.get("page", {}).get("httpStatus"),
        "browserAttempted": bool(browser_res and browser_res.browser_attempted),
        "browserLaunched": bool(browser_res and browser_res.browser_attempted and not browser_res.browser_error),
        "navigationSucceeded": bool(browser_res and browser_res.navigation_status and browser_res.navigation_status < 400),
        "extractionSucceeded": usable,
        "challengeDetected": bool(browser_res and browser_res.challenge_detected),
        "partial": outcome == "partial",
    }

    return {
        "result": {
            "url": url,
            "scrapedAt": now,
            "meta": meta,
            "sections": sections,
            "page": result.get("page", {}),
            "metadata": meta_raw,
            "components": components,
            "content": result.get("content", {}),
            "resources": result.get("resources", {}),
            "interactions": interactions,
            "stats": stats,
            "attempt": attempt,
            "errors": errors,
        }
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main scrape endpoint
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/scrape")
async def scrape(data: ScrapeRequest):
    t_start = time.perf_counter()

    # ── Phase 1: URL validation ───────────────────────────────────────────────
    try:
        url = normalize_url(data.url)
    except (URLPolicyError, ValueError) as exc:
        return JSONResponse(status_code=400, content=_failure(data.url, str(exc), "validation"))

    # ── Phase 2: Static fetch ─────────────────────────────────────────────────
    source = await fetch_page(url)

    if source.error:
        return _failure(url, source.error, "fetch")

    # ── Phase 3: Static extraction ────────────────────────────────────────────
    try:
        static_result = extract_page(source, strategy="static")
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content=_failure(url, f"Extraction error: {exc}", "extract"),
        )

    interactions: dict = {"clicks": [], "scrolls": 0, "pages": [source.final_url]}
    strategy = "static"
    browser_res: BrowserResult | None = None

    # ── Phase 4: Quality analysis → decide on browser ─────────────────────────
    quality = analyze_static_quality(source, static_result)

    if quality in (JS_REQUIRED, STATIC_EMPTY, STATIC_PARTIAL):
        browser_res = await js_scrape(source.final_url)

        if browser_res.blocked or browser_res.challenge_detected:
            # F2: Challenge detected — preserve static result, report block
            static_result["errors"].append({
                "message": f"Browser encountered access challenge: {browser_res.block_reason}",
                "phase": "render",
                "recoverable": True,
            })
            # Strategy reflects partial success
            strategy = "static" if static_result.get("content", {}).get("sections") else "blocked"

        elif not browser_res.browser_succeeded or not browser_res.html:
            # Browser failed entirely — preserve static result, append error
            if browser_res.browser_error:
                static_result["errors"].append({
                    "message": browser_res.browser_error,
                    "phase": "render",
                    "recoverable": True,
                })
            # Keep static result unchanged — never erase it on browser failure

        else:
            # F3: Browser succeeded — extract then MERGE, never replace
            rendered_source = PageSource(
                requested_url=url,
                final_url=browser_res.final_url or source.final_url,
                content_type="text/html",
                status_code=200,
                html=browser_res.html,
                fetch_duration_ms=source.fetch_duration_ms,
            )
            try:
                browser_extracted = extract_page(rendered_source, strategy="js")
                # MERGE: augment static result with browser result
                static_result = _merge_results(static_result, browser_extracted)
            except Exception as exc:
                static_result["errors"].append({
                    "message": f"JS extraction error: {exc}",
                    "phase": "extract",
                    "recoverable": True,
                })

            # Append any scroll/interaction errors from browser
            for e in browser_res.errors:
                e_copy = dict(e)
                e_copy["recoverable"] = True
                static_result["errors"].append(e_copy)

            strategy = "js" if quality == JS_REQUIRED else "static+js"

        # Use browser interactions dict
        interactions = browser_res.to_interactions()

    return _compose_response(url, static_result, interactions, strategy, browser_res, t_start)
