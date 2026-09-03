"""FastAPI surface for the Universal Web Scraper v2.

Pipeline per request:
  1. URL validation & normalization (urls.py)
  2. Async static fetch (static.py fetch_page)
  3. Static quality analysis — STATIC_COMPLETE / PARTIAL / EMPTY / JS_REQUIRED
  4. Optional browser render (js.py js_scrape) — only when quality signals demand it
  5. Content extraction via shared extract_page() pipeline
  6. Schema composition + legacy field compat
  7. Return structured JSON
"""
import time
from datetime import datetime, timezone

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
from app.scraper.js import js_scrape
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
# Helper — compose final schema-compliant response
# ─────────────────────────────────────────────────────────────────────────────

def _compose_response(
    url: str,
    result: dict,
    interactions: dict,
    strategy: str,
    render_ms: float,
    t_start: float,
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    meta_raw = result.get("metadata", {})
    stats_raw = result.get("statistics", {})

    total_ms = round((time.perf_counter() - t_start) * 1000, 1)
    fetch_ms = stats_raw.get("fetchDurationMs", 0.0)

    # sections[] at top level (assignment-compat) = content.sections
    sections = result.get("content", {}).get("sections", [])

    meta = {
        "title":       meta_raw.get("title", ""),
        "description": meta_raw.get("description", ""),
        "language":    meta_raw.get("language", "en"),
        "canonical":   meta_raw.get("canonical"),
        "strategy":    strategy,
    }

    stats = {
        "fetchDurationMs":   fetch_ms,
        "renderDurationMs":  render_ms,
        "totalDurationMs":   total_ms,
        "sectionsExtracted": stats_raw.get("sections", len(sections)),
        "sectionsDeduped":   stats_raw.get("sectionsDeduped", 0),
        "linksFound":        stats_raw.get("linksFound", 0),
        "imagesFound":       stats_raw.get("imagesFound", 0),
    }

    errors = result.get("errors", [])
    warnings = result.get("warnings", [])

    # Convert any warnings → errors with recoverable=True
    for w in warnings:
        errors.append({
            "message": w.get("message", str(w)),
            "phase": w.get("stage", "unknown"),
            "recoverable": True,
        })

    return {
        "result": {
            # Core fields (assignment-required)
            "url": url,
            "scrapedAt": now,
            "meta": meta,
            "sections": sections,
            # Extended fields
            "page": result.get("page", {}),
            "metadata": meta_raw,
            "components": result.get("components", {
                "navigation": [], "tableOfContents": [],
                "languageSelector": [], "sidebar": [], "footer": [],
            }),
            "content": result.get("content", {}),
            "resources": result.get("resources", {}),
            "interactions": interactions,
            "stats": stats,
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
        # Fix D: return 200 with schema-compliant error body — frontend reads result.errors[]
        return _failure(url, source.error, "fetch")

    # ── Phase 3: Static extraction ────────────────────────────────────────────
    try:
        result = extract_page(source, strategy="static")
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content=_failure(url, f"Extraction error: {exc}", "extract"),
        )

    interactions: dict = {"clicks": [], "scrolls": 0, "pages": [source.final_url]}
    strategy = "static"
    render_ms = 0.0

    # ── Phase 4: Quality analysis → decide on browser ─────────────────────────
    quality = analyze_static_quality(source, result)

    if quality in (JS_REQUIRED, STATIC_EMPTY, STATIC_PARTIAL):
        html, browser_interactions, browser_error = await js_scrape(source.final_url)

        render_ms = browser_interactions.pop("_renderDurationMs", 0.0)

        if browser_error and not html:
            # Browser failed entirely — keep static result, append error
            result["errors"].append({
                "message": browser_error,
                "phase": "render",
                "recoverable": True,
            })
        elif html:
            # Browser succeeded — extract from rendered HTML
            rendered_source = PageSource(
                requested_url=url,
                final_url=browser_interactions["pages"][-1] if browser_interactions["pages"] else source.final_url,
                content_type="text/html",
                status_code=200,
                html=html,
                fetch_duration_ms=source.fetch_duration_ms,
            )
            try:
                result = extract_page(rendered_source, strategy="js")
            except Exception as exc:
                result["errors"].append({
                    "message": f"JS extraction error: {exc}",
                    "phase": "extract",
                    "recoverable": True,
                })

            interactions = browser_interactions
            strategy = "js" if quality == JS_REQUIRED else "static+js"

            if browser_error:
                result["errors"].append({
                    "message": browser_error,
                    "phase": "scroll",
                    "recoverable": True,
                })

    return _compose_response(url, result, interactions, strategy, render_ms, t_start)
