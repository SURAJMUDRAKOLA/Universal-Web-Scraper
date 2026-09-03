"""Async static fetching and shared HTML extraction pipeline.

Pipeline order (corrected — metadata BEFORE decomposition):
  1. fetch_page         – async httpx + urllib fallback
  2. _metadata          – extract BEFORE any decomposition (Bug 1 fix)
  3. _structured_data   – extract BEFORE any decomposition
  4. _extract_components – pull nav/footer/language-selector OUT (decomposes chrome)
  5. _clean             – strip remaining noise elements
  6. _primary_root      – two-pass: priority selectors first, scoring fallback (Bug 2 fix)
  7. _build_sections    – heading-driven hierarchy with ownership enforcement
  8. _dedup_sections    – fingerprint-based deduplication
  9. _merge_resources   – aggregate links/images across sections
 10. Compose final result dict
"""
from __future__ import annotations

import asyncio
import gzip as _gzip
import hashlib
import json
import re
import time
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, NavigableString, Tag

from app.scraper.config import (
    FETCH_CONNECT_TIMEOUT,
    FETCH_READ_TIMEOUT,
    FETCH_TOTAL_TIMEOUT,
    MAX_IMAGES,
    MAX_LINKS,
    MAX_LISTS,
    MAX_RESPONSE_BYTES,
    MAX_SECTIONS,
    MAX_TABLES,
    MAX_TEXT_PER_SECTION,
    MAX_TOTAL_TEXT,
    MIN_TEXT_LENGTH_STATIC,
    RAW_HTML_TRUNCATE,
)
from app.scraper.urls import URLPolicyError, normalize_url

# ── HTTP headers ──────────────────────────────────────────────────────────────
_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _CHROME_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}
# urllib variant — no Accept-Encoding so we don't need to decompress manually
_HEADERS_URLLIB = {**_HEADERS, "Accept-Encoding": "identity"}

# ── Noise selectors (CSS — no i-flag, keep compatible with soupsieve<2.4) ────
_NOISE_SELECTORS = [
    "script", "style", "template", "noscript", "iframe",
    "[hidden]",
    "[role='dialog']", "[aria-modal='true']",
]

# ── Bug 2 fix: Two-pass primary root selection ────────────────────────────────
# Pass 1: high-confidence named selectors tried in order; first match with
# >200 chars of text is returned immediately without scoring.
_PRIORITY_SELECTORS = [
    "#mw-content-text",    # Wikipedia article body
    "#bodyContent",        # Wikipedia older skin
    "article",             # Semantic article element
    "main",                # HTML5 main landmark
    "[role='main']",       # ARIA main role
    "#content",            # Common CMS pattern
    "#main-content",       # Common pattern
    ".article-body",       # News sites
    ".post-content",       # Blog pattern
    ".content",            # Generic fallback
]

# Pass 2 fallback: generic candidates fed into _score()
_CONTENT_SELECTORS = _PRIORITY_SELECTORS  # backward-compat alias

# ── Language-code regex (ISO 639: 2–5 alpha chars, optional subtag) ──────────
_LANG_CODE_RE = re.compile(r"^[a-zA-Z]{2,5}(-[a-zA-Z0-9]{2,4})?$")

# Public state constants
STATIC_COMPLETE = "STATIC_COMPLETE"
STATIC_PARTIAL  = "STATIC_PARTIAL"
STATIC_EMPTY    = "STATIC_EMPTY"
JS_REQUIRED     = "JS_REQUIRED"


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PageSource:
    requested_url: str
    final_url: str
    content_type: str
    status_code: int | None
    html: str
    error: str | None = None
    fetch_duration_ms: float = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Fetch layer
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_page(url: str) -> PageSource:
    """Async fetch with httpx first, urllib fallback for sites that 403 httpx.

    Fix E: normalize_url() is NOT called here — main.py already normalizes
    the URL before calling fetch_page(), so we skip the redundant DNS lookup.
    """
    t0 = time.perf_counter()
    current_url = url  # Already normalized by caller (Fix E)

    # ── httpx ─────────────────────────────────────────────────────────────────
    httpx_status: int | None = None
    final_url = current_url
    try:
        timeout = httpx.Timeout(
            connect=FETCH_CONNECT_TIMEOUT,
            read=FETCH_READ_TIMEOUT,
            write=10,
            pool=FETCH_TOTAL_TIMEOUT,
        )
        async with httpx.AsyncClient(
            headers=_HEADERS,
            follow_redirects=True,
            timeout=timeout,
            max_redirects=8,
        ) as client:
            resp = await client.get(current_url)
            final_url = str(resp.url)
            ct_header = resp.headers.get("content-type", "")
            content_type = ct_header.split(";", 1)[0].strip().lower()

            if resp.status_code == 200:
                raw = resp.content  # httpx auto-decompresses gzip/br
                if len(raw) > MAX_RESPONSE_BYTES:
                    ms = (time.perf_counter() - t0) * 1000
                    return PageSource(url, final_url, content_type, 200, "",
                                      "Response exceeded the size limit",
                                      fetch_duration_ms=ms)

                charset = _detect_charset(ct_header, raw)
                html = raw.decode(charset, errors="replace")
                ms = (time.perf_counter() - t0) * 1000
                return PageSource(url, final_url, content_type, 200, html,
                                  fetch_duration_ms=ms)

            httpx_status = resp.status_code

    except Exception:
        pass  # fall through to urllib

    # ── urllib fallback ───────────────────────────────────────────────────────
    try:
        html_fb, final_url_fb, ct_fb = await asyncio.get_event_loop().run_in_executor(
            None, _urllib_fetch, current_url
        )
        ms = (time.perf_counter() - t0) * 1000
        if html_fb:
            return PageSource(url, final_url_fb, ct_fb, 200, html_fb,
                              fetch_duration_ms=ms)
    except Exception:
        pass

    ms = (time.perf_counter() - t0) * 1000
    msg = (
        f"HTTP {httpx_status} returned by upstream"
        if httpx_status and httpx_status >= 400
        else "Fetch failed: network or timeout error"
    )
    return PageSource(url, final_url, "", httpx_status, "", msg,
                      fetch_duration_ms=ms)


def _detect_charset(ct_header: str, raw: bytes) -> str:
    """Determine the best charset from Content-Type header or HTML meta."""
    if "charset=" in ct_header.lower():
        try:
            return ct_header.lower().split("charset=")[1].split(";")[0].strip() or "utf-8"
        except IndexError:
            pass
    # Peek at first 2KB for <meta charset="...">
    peek = raw[:2048].decode("ascii", errors="ignore")
    m = re.search(r'charset=["\']?([a-zA-Z0-9_-]+)', peek, re.IGNORECASE)
    if m:
        return m.group(1)
    return "utf-8"


def _urllib_fetch(url: str) -> tuple[str, str, str]:
    """Sync urllib fetch (run in executor). Handles gzip even when not requested."""
    req = urllib.request.Request(url)
    for k, v in _HEADERS_URLLIB.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=FETCH_READ_TIMEOUT) as resp:
        raw = resp.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("Response exceeded the size limit")
        # Some servers always gzip even when not requested
        if raw[:2] == b'\x1f\x8b':
            try:
                raw = _gzip.decompress(raw)
            except Exception:
                pass
        charset = resp.headers.get_content_charset() or "utf-8"
        content_type = resp.headers.get_content_type() or "text/html"
        return raw.decode(charset, errors="replace"), resp.url, content_type


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _safe_get(tag: Any, attr: str, default: str = "") -> str:
    """Safely call .get() on a BS4 tag — never raises even if tag is None."""
    if tag is None or not hasattr(tag, "get"):
        return default
    val = tag.get(attr, default)
    return str(val) if val is not None else default


def _safe_text(tag: Any, sep: str = " ") -> str:
    """Safely get text from a BS4 tag."""
    if tag is None or not hasattr(tag, "get_text"):
        return ""
    try:
        return tag.get_text(sep, strip=True)
    except Exception:
        return ""


def _tag_class_id(tag: Tag) -> str:
    """Get combined class + id string for a tag (lowercase)."""
    classes = " ".join(tag.get("class") or []) if hasattr(tag, "get") else ""
    tag_id = _safe_get(tag, "id")
    return (classes + " " + tag_id).lower()


# ─────────────────────────────────────────────────────────────────────────────
# Language-selector detection
# ─────────────────────────────────────────────────────────────────────────────

def _is_language_selector(tag: Tag) -> bool:
    """Return True when tag is a language selector.

    Two strategies:
    1. ISO-code text: ≥10 anchors whose text matches ^[a-z]{2,5}$
    2. interlanguage-link class: ≥10 child <li> elements with class containing
       'interlanguage-link' (current Wikipedia style)
    """
    try:
        # Strategy 1: ISO code anchor texts
        anchors = tag.find_all("a", limit=300)
        if len(anchors) >= 10:
            lang_like = sum(
                1 for a in anchors
                if a and hasattr(a, "get_text") and _LANG_CODE_RE.match(a.get_text(strip=True))
            )
            if lang_like >= max(8, len(anchors) * 0.5):
                return True

        # Strategy 2: interlanguage-link li children (Wikipedia current style)
        interlang_li = [
            li for li in tag.find_all("li", limit=300)
            if li and hasattr(li, "get") and
            any("interlanguage" in str(c) for c in (li.get("class") or []))
        ]
        if len(interlang_li) >= 10:
            return True

        return False
    except Exception:
        return False


def _has_interlanguage_children(soup: BeautifulSoup) -> Tag | None:
    """Find the top-level language-selector container for Wikipedia-style pages.

    Scans up to 500 li elements (performance limit), then walks up up to 6
    ancestor levels looking for a tag with 'lang' or 'interlanguage' in its
    id/class. Returns that named ancestor, or the immediate <ul> parent.
    """
    try:
        li_tags = soup.find_all("li", limit=500)
        inter_li = [
            li for li in li_tags
            if li and hasattr(li, "get") and
            any("interlanguage" in str(c) for c in (li.get("class") or []))
        ]
        if len(inter_li) < 10:
            return None

        candidate = inter_li[0].parent
        if not candidate:
            return None
        for _ in range(6):
            parent = candidate.parent if candidate else None
            if not parent or getattr(parent, 'name', None) in (None, "body", "[document]"):
                break
            pid = str(parent.get("id") or "").lower()
            cls = " ".join(parent.get("class") or []).lower()
            if "lang" in pid or "lang" in cls or "interlanguage" in cls:
                candidate = parent
            else:
                break
        return candidate
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Component extraction — runs AFTER metadata, decomposes chrome from soup
# ─────────────────────────────────────────────────────────────────────────────

def _extract_components(soup: BeautifulSoup, base_url: str) -> dict[str, list]:
    """Extract page chrome into components dict and remove from soup.

    Fix A: Sidebar removal now requires the candidate to have < 500 chars of
    text. Real article content containers are large; true sidebars are small.
    """
    components: dict[str, list] = {
        "navigation": [],
        "tableOfContents": [],
        "languageSelector": [],
        "sidebar": [],
        "footer": [],
    }

    # Collect tags first, then decompose — avoids modifying a live iterator
    def _collect_and_remove(tags: list, action):
        to_remove = []
        for tag in tags:
            if tag is None or not hasattr(tag, "decompose"):
                continue
            try:
                action(tag, components)
                to_remove.append(tag)
            except Exception:
                to_remove.append(tag)  # remove anyway to keep soup clean
        for tag in to_remove:
            try:
                tag.decompose()
            except Exception:
                pass

    # ── Footers ───────────────────────────────────────────────────────────────
    def _footer_action(tag, comps):
        comps["footer"].append({"text": _safe_text(tag)[:300]})
    _collect_and_remove(list(soup.find_all("footer")), _footer_action)

    # ── Table of Contents — by ID/class patterns ──────────────────────────────
    def _toc_action(tag, comps):
        items = [_safe_text(li) for li in tag.find_all("li", limit=60) if li]
        if items:
            comps["tableOfContents"].append({"items": items})

    toc_tags = []
    for candidate in soup.find_all(["div", "nav", "ul", "ol"], limit=200):
        if not hasattr(candidate, "get"):
            continue
        ci = _tag_class_id(candidate)
        if any(kw in ci for kw in ("toc", "table-of-contents", "tableofcontents")):
            toc_tags.append(candidate)
    _collect_and_remove(toc_tags, _toc_action)

    # ── Language selectors ────────────────────────────────────────────────────
    def _lang_action(tag, comps):
        links = [
            _safe_text(a) for a in tag.find_all("a", limit=200)
            if a and hasattr(a, "get_text")
        ]
        if links:
            comps["languageSelector"].append({"languages": links})

    lang_tags = []
    seen_lang_ids: set[int] = set()

    # Strategy B: Wikipedia-specific — find parent of interlanguage-link <li> items
    wiki_lang_parent = _has_interlanguage_children(soup)
    if wiki_lang_parent is not None and id(wiki_lang_parent) not in seen_lang_ids:
        seen_lang_ids.add(id(wiki_lang_parent))
        for child in wiki_lang_parent.find_all(True):
            seen_lang_ids.add(id(child))
        lang_tags.append(wiki_lang_parent)

    # Strategy A: keyword-based search
    # IMPORTANT: Skip structural root elements (html, head, body, main).
    # Wikipedia's <html> element has class names containing 'language'
    # (e.g. 'vector-feature-language-in-header-enabled'), causing the entire
    # document to be matched and decomposed. We must only match chrome widgets,
    # not the document root or primary landmark elements.
    _SKIP_LANG_TAGS = {"html", "head", "body", "main", "[document]"}
    for candidate in soup.find_all(True, limit=600):
        if not hasattr(candidate, "get"):
            continue
        # Skip structural root elements — they are never a language selector
        if getattr(candidate, 'name', None) in _SKIP_LANG_TAGS:
            continue
        eid = id(candidate)
        if eid in seen_lang_ids:
            continue
        ci = _tag_class_id(candidate)
        tag_id = _safe_get(candidate, "id").lower()
        aria_label = _safe_get(candidate, "aria-label").lower()
        keyword_match = (
            any(kw in ci for kw in ("lang", "interlanguage", "language"))
            or "language" in aria_label
            or "lang" in tag_id
        )
        if keyword_match and _is_language_selector(candidate):
            seen_lang_ids.add(eid)
            for child in candidate.find_all(True):
                seen_lang_ids.add(id(child))
            lang_tags.append(candidate)
    _collect_and_remove(lang_tags, _lang_action)

    # ── Navigation ────────────────────────────────────────────────────────────
    def _nav_action(tag, comps):
        label = _safe_get(tag, "aria-label") or _safe_get(tag, "id") or ""
        links = []
        for a in tag.find_all("a", href=True, limit=40):
            href_val = _safe_get(a, "href")
            if href_val:
                links.append({
                    "text": _safe_text(a)[:100],
                    "href": urljoin(base_url, href_val),
                })
        if links:
            comps["navigation"].append({"label": label, "links": links})
    _collect_and_remove(list(soup.find_all("nav")), _nav_action)

    # ── Sidebars (Fix A: only remove small sidebars < 500 chars) ─────────────
    def _sidebar_action(tag, comps):
        comps["sidebar"].append({"text": _safe_text(tag)[:300]})

    sidebar_tags = []
    for candidate in soup.find_all(["div", "aside", "section"], limit=200):
        if not hasattr(candidate, "get"):
            continue
        ci = _tag_class_id(candidate)
        if "sidebar" in ci or candidate.name == "aside":
            # Fix A: skip if candidate contains too much text — likely article content
            if len(_safe_text(candidate)) < 500:
                sidebar_tags.append(candidate)
    _collect_and_remove(sidebar_tags, _sidebar_action)

    return components


# ─────────────────────────────────────────────────────────────────────────────
# Noise removal
# ─────────────────────────────────────────────────────────────────────────────

_NOISE_KEYWORDS = [
    "cookie", "consent", "modal", "popup", "overlay",
    "toast", "newsletter", "subscribe", "gdpr", "banner",
]


def _clean(soup: BeautifulSoup) -> None:
    """Remove script/style/noise tags.

    Fix B: Never remove heading tags (h1-h6) even if they carry aria-hidden.
    Wikipedia uses aria-hidden on edit-section spans inside headings.
    """
    # Remove by tag name first
    for selector in _NOISE_SELECTORS:
        for el in soup.select(selector):
            try:
                el.decompose()
            except Exception:
                pass

    # Remove by class/id keyword (case-insensitive, Python-level)
    for tag in list(soup.find_all(True)):
        if not hasattr(tag, "get"):
            continue
        ci = _tag_class_id(tag)
        if any(kw in ci for kw in _NOISE_KEYWORDS):
            try:
                tag.decompose()
            except Exception:
                pass

    # Remove hidden elements
    for tag in list(soup.find_all(attrs={"hidden": True})):
        try:
            tag.decompose()
        except Exception:
            pass

    # Fix B: Remove aria-hidden elements but NEVER remove heading tags
    for tag in list(soup.find_all(attrs={"aria-hidden": "true"})):
        # Never remove heading tags even if aria-hidden (Fix B)
        if tag.name and re.match(r"^h[1-6]$", tag.name):
            continue
        try:
            tag.decompose()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Text helpers
# ─────────────────────────────────────────────────────────────────────────────

def _text(tag: Tag) -> str:
    pieces = []
    try:
        for node in tag.descendants:
            if isinstance(node, NavigableString):
                value = re.sub(r"\s+", " ", str(node)).strip()
                if value:
                    pieces.append(value)
    except Exception:
        pass
    return re.sub(r"\s+", " ", " ".join(pieces)).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Content scoring — used as fallback in two-pass primary root selection
# ─────────────────────────────────────────────────────────────────────────────

def _score(tag: Tag) -> float:
    try:
        text = _text(tag)
        if len(text) < 80:
            return -1000

        anchors = tag.find_all("a", limit=300)
        p_count = len(tag.find_all("p"))
        h_count = len(tag.find_all(re.compile(r"^h[1-6]$")))
        ctrl_count = len(tag.find_all(["button", "input", "select", "form"]))
        nav_count = len(tag.find_all("nav"))

        lang_anchors = sum(
            1 for a in anchors[:200]
            if a and hasattr(a, "get_text") and _LANG_CODE_RE.match(a.get_text(strip=True))
        )

        tag_id = _safe_get(tag, "id").lower()
        tag_bonus = (
            180 if tag.name == "article" else
            120 if tag.name == "main" or _safe_get(tag, "role") == "main" else
            80  if tag_id in {"content", "main-content", "mw-content-text", "bodyContent"} else
            0
        )

        return (
            tag_bonus
            + min(len(text), 12_000) / 25
            + p_count * 35
            + h_count * 15
            - len(anchors) * 4
            - ctrl_count * 25
            - nav_count * 40
            - lang_anchors * 30
        )
    except Exception:
        return -1000


def _primary_root(soup: BeautifulSoup) -> Tag:
    """Two-pass primary content root selection (Bug 2 fix).

    Pass 1 — Priority selectors tried in order. The first matching element
    that contains more than 200 characters of text is returned immediately,
    bypassing scoring. This guarantees Wikipedia always gets #mw-content-text.

    Pass 2 — If no priority selector matched with enough text, fall back to
    scoring all div/section candidates (original behaviour).
    """
    # Pass 1: high-confidence named selectors
    for selector in _PRIORITY_SELECTORS:
        try:
            node = soup.select_one(selector)
            if node and len(_text(node)) > 200:
                return node
        except Exception:
            continue

    # Pass 2: scoring fallback for unknown site structures
    candidates = list(soup.find_all(["div", "section"], limit=120))
    unique = list({id(node): node for node in candidates if node}.values())
    if not unique:
        return soup.body or soup
    return max(unique, key=_score)


# ─────────────────────────────────────────────────────────────────────────────
# Resource extraction
# ─────────────────────────────────────────────────────────────────────────────

def _resources(tag: Tag, base_url: str) -> dict[str, list]:
    links, images, lists, tables = [], [], [], []
    seen_links: set[tuple] = set()

    try:
        for anchor in tag.find_all("a", href=True, limit=MAX_LINKS * 2):
            href_val = _safe_get(anchor, "href")
            if not href_val:
                continue
            href = urljoin(base_url, href_val)
            txt = _safe_text(anchor)[:200]
            key = (txt, href)
            if href.startswith(("http://", "https://")) and key not in seen_links:
                seen_links.add(key)
                links.append({"text": txt, "href": href})
                if len(links) >= MAX_LINKS:
                    break
    except Exception:
        pass

    try:
        for img in tag.find_all("img", limit=MAX_IMAGES):
            src = _safe_get(img, "src") or _safe_get(img, "data-src") or _safe_get(img, "data-lazy-src")
            if src:
                images.append({"src": urljoin(base_url, src), "alt": _safe_get(img, "alt")})
    except Exception:
        pass

    try:
        for ul in tag.find_all(["ul", "ol"], limit=MAX_LISTS):
            items = [_safe_text(li) for li in ul.find_all("li", recursive=False) if li]
            if items:
                lists.append({"ordered": ul.name == "ol", "items": items})
    except Exception:
        pass

    try:
        for table in tag.find_all("table", limit=MAX_TABLES):
            rows = [
                [_safe_text(cell) for cell in tr.find_all(["th", "td"])]
                for tr in table.find_all("tr")
            ]
            rows = [r for r in rows if r]
            if rows:
                caption = _safe_text(table.caption) if table.caption else ""
                tables.append({"caption": caption, "rows": rows})
    except Exception:
        pass

    return {
        "links": links,
        "images": images[:MAX_IMAGES],
        "lists": lists,
        "tables": tables,
    }


# ─────────────────────────────────────────────────────────────────────────────
# DOM ownership — blocks owned by a heading, stop at next sibling heading
# ─────────────────────────────────────────────────────────────────────────────

_BLOCK_NAMES = {"p", "blockquote", "pre", "figure", "figcaption", "ul", "ol", "table", "dl"}


def _owned_blocks(heading: Tag) -> list[Tag]:
    level = int(heading.name[1])
    blocks: list[Tag] = []
    seen_ids: set[int] = set()
    try:
        for element in heading.next_elements:
            if element is heading:
                continue
            if isinstance(element, Tag):
                if re.fullmatch(r"h[1-6]", element.name or ""):
                    if int(element.name[1]) <= level:
                        break
                if element.name not in _BLOCK_NAMES:
                    continue
                eid = id(element)
                if eid in seen_ids:
                    continue
                if any(id(p) in seen_ids for p in element.parents):
                    continue
                seen_ids.add(eid)
                blocks.append(element)
    except Exception:
        pass
    return blocks


# ─────────────────────────────────────────────────────────────────────────────
# Section building
# ─────────────────────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")


def _build_sections(root: Tag, base_url: str) -> list[dict]:
    sections: list[dict] = []
    fingerprints: set[str] = set()
    slug_counts: dict[str, int] = {}
    stack: list[tuple[int, str]] = []
    total_chars = 0

    try:
        headings = root.find_all(re.compile(r"^h[1-6]$"))
    except Exception:
        return []

    for heading in headings[:MAX_SECTIONS]:
        try:
            level = int(heading.name[1])
            label = _safe_text(heading)[:200]
            if not label:
                continue

            while stack and stack[-1][0] >= level:
                stack.pop()

            owned = _owned_blocks(heading)
            text = re.sub(
                r"\s+", " ",
                " ".join(_text(node) for node in owned)
            ).strip()[:MAX_TEXT_PER_SECTION]

            fp = re.sub(r"\W+", "", (label + " " + text[:100]).lower())
            if fp in fingerprints:
                continue
            fingerprints.add(fp)

            slug = _slugify(label)[:60]
            base_slug = f"h{level}-{slug}"
            count = slug_counts.get(base_slug, 0)
            slug_counts[base_slug] = count + 1
            section_id = base_slug if count == 0 else f"{base_slug}-{count}"

            parent_id = stack[-1][1] if stack else None
            depth = level - 1

            fragment_html = "".join(str(b) for b in owned)
            try:
                fragment = BeautifulSoup(fragment_html, "lxml")
            except Exception:
                fragment = BeautifulSoup("", "lxml")

            raw_html = (str(heading) + fragment_html)[:RAW_HTML_TRUNCATE]

            # Fix 1: extract resources from heading's parent container, not just
            # owned blocks. This captures links/images in surrounding wrappers
            # (e.g. Foxtale SSR pages where links live in the section container).
            section_root = heading.parent if heading.parent else heading
            try:
                section_resources = _resources(section_root, base_url)
            except Exception:
                section_resources = _resources(fragment, base_url)

            sections.append({
                "id": section_id,
                "parentId": parent_id,
                "depth": depth,
                "level": level,
                "type": "section",
                "label": label,
                "sourceUrl": base_url,
                "content": {
                    "headings": [label],
                    "text": text,
                    **section_resources,
                },
                "rawHtml": raw_html,
                "truncated": len(str(heading) + fragment_html) > RAW_HTML_TRUNCATE,
            })

            stack.append((level, section_id))
            total_chars += len(text)
            if total_chars >= MAX_TOTAL_TEXT:
                break

        except Exception:
            continue  # skip this heading and continue

    return sections


# ─────────────────────────────────────────────────────────────────────────────
# Deduplication
# ─────────────────────────────────────────────────────────────────────────────

def _section_fingerprint(section: dict) -> str:
    heading_str = "|".join(section.get("content", {}).get("headings", []))
    text_prefix = section.get("content", {}).get("text", "")[:100].strip()
    return hashlib.md5(f"{heading_str}::{text_prefix}".encode()).hexdigest()


def _dedup_sections(sections: list[dict]) -> tuple[list[dict], int]:
    seen: set[str] = set()
    unique: list[dict] = []
    for section in sections:
        try:
            fp = _section_fingerprint(section)
            if fp not in seen:
                seen.add(fp)
                unique.append(section)
        except Exception:
            unique.append(section)
    return unique, len(sections) - len(unique)


# ─────────────────────────────────────────────────────────────────────────────
# Resource aggregation
# ─────────────────────────────────────────────────────────────────────────────

def _merge_resources(sections: list[dict]) -> dict[str, list]:
    merged: dict[str, list] = {
        "links": [], "images": [], "lists": [], "tables": [],
        "videos": [], "audio": [], "documents": [],
    }
    seen: set[tuple] = set()
    for section in sections:
        content = section.get("content", {})
        for name in ("links", "images", "lists", "tables"):
            for item in content.get(name, []):
                try:
                    key = (name, json.dumps(item, sort_keys=True))
                    if key not in seen:
                        seen.add(key)
                        merged[name].append(item)
                except Exception:
                    pass
    return merged


# ─────────────────────────────────────────────────────────────────────────────
# Metadata extraction — called BEFORE any decomposition (Bug 1 fix)
# ─────────────────────────────────────────────────────────────────────────────

def _metadata(soup: BeautifulSoup, base_url: str) -> dict:
    """Extract page metadata with full fallback chain. Never raises.

    Bug 1 fix: This must be called BEFORE _extract_components() so that the
    <title> tag and <head> meta tags are still intact in the soup.
    """
    try:
        def _meta(attrs: dict) -> str:
            try:
                tag = soup.find("meta", attrs=attrs)
                if tag and hasattr(tag, "get"):
                    return str(tag.get("content") or "").strip()
            except Exception:
                pass
            return ""

        # Title
        title = ""
        try:
            if soup.title:
                title = soup.title.get_text(" ", strip=True)
        except Exception:
            pass
        if not title:
            title = _meta({"property": "og:title"}) or _meta({"name": "twitter:title"})
        if not title:
            try:
                h1 = soup.find("h1")
                if h1:
                    title = _safe_text(h1)[:200]
            except Exception:
                pass

        # Description
        description = (
            _meta({"name": "description"})
            or _meta({"property": "og:description"})
            or _meta({"name": "twitter:description"})
        )
        if not description:
            try:
                p = soup.find("p")
                if p:
                    description = _safe_text(p)[:200]
            except Exception:
                pass

        # Language
        language = "en"
        try:
            html_tag = soup.find("html")
            if html_tag and hasattr(html_tag, "get"):
                lang_val = html_tag.get("lang")
                if lang_val:
                    language = str(lang_val).strip() or "en"
        except Exception:
            pass
        if language == "en":
            language = _meta({"http-equiv": "content-language"}) or "en"

        # Canonical
        canonical: str | None = None
        try:
            c_tag = soup.find("link", rel=lambda v: isinstance(v, list) and "canonical" in v)
            if c_tag and hasattr(c_tag, "get"):
                href_val = c_tag.get("href")
                if href_val:
                    canonical = urljoin(base_url, str(href_val))
        except Exception:
            pass

        # OpenGraph
        og: dict = {}
        try:
            for tag in soup.select("meta[property^='og:']"):
                prop = _safe_get(tag, "property")
                if prop and len(prop) > 3:
                    og[prop[3:]] = str(tag.get("content") or "")
        except Exception:
            pass

        # Twitter
        tw: dict = {}
        try:
            for tag in soup.select("meta[name^='twitter:']"):
                name = _safe_get(tag, "name")
                if name and len(name) > 8:
                    tw[name[8:]] = str(tag.get("content") or "")
        except Exception:
            pass

        return {
            "title": title or "",
            "description": description or "",
            "language": language,
            "canonical": canonical,
            "openGraph": og,
            "twitter": tw,
        }

    except Exception:
        return {
            "title": "", "description": "", "language": "en",
            "canonical": None, "openGraph": {}, "twitter": {},
        }


# ─────────────────────────────────────────────────────────────────────────────
# Structured data (JSON-LD)
# ─────────────────────────────────────────────────────────────────────────────

def _structured_data(soup: BeautifulSoup) -> list:
    result = []
    try:
        for node in soup.select("script[type='application/ld+json']"):
            try:
                result.append(json.loads(node.string or node.get_text()))
            except Exception:
                pass
    except Exception:
        pass
    return result[:20]


# ─────────────────────────────────────────────────────────────────────────────
# Four-state quality analysis (Bug 5 fix)
# ─────────────────────────────────────────────────────────────────────────────

def analyze_static_quality(source: PageSource, result: dict) -> str:
    """Decide whether browser rendering is needed.

    Bug 5 fix: Large HTML with no extracted text → STATIC_PARTIAL (not STATIC_EMPTY).
    Fix 2: Detect bot-block / JS-disabled pages → JS_REQUIRED.
    Fix 3: SSR text with no links/images → STATIC_PARTIAL for JS enrichment.
    """
    html = source.html or ""
    html_lower = html.lower()
    sections = result.get("content", {}).get("sections", [])
    total_text = result.get("statistics", {}).get("textCharacters", 0)

    # Fix 2: Detect explicit JS-disabled / bot-block pages (e.g. Amazon)
    _JS_DISABLED_SIGNALS = [
        "javascript is disabled",
        "javascript is not available",
        "enable javascript",
        "please enable javascript",
    ]
    if any(signal in html_lower for signal in _JS_DISABLED_SIGNALS):
        return JS_REQUIRED

    # SPA signals
    has_root_app = (
        'id="root"' in html_lower or "id='root'" in html_lower or
        'id="app"'  in html_lower or "id='app'"  in html_lower
    )
    script_count = html_lower.count("<script")
    p_count = html_lower.count("<p")

    if has_root_app and p_count < 3 and script_count > 5:
        return JS_REQUIRED

    # Noscript with meaningful content
    try:
        ns_tags = BeautifulSoup(html, "lxml").find_all("noscript")
        for ns in ns_tags:
            if len(_safe_text(ns)) > 100:
                return JS_REQUIRED
    except Exception:
        pass

    if not sections or total_text == 0:
        # Bug 5 fix: large HTML but no extracted text = extraction issue,
        # not a JS-rendered page — return STATIC_PARTIAL to avoid browser overhead
        if len(html) > 50_000:
            return STATIC_PARTIAL
        return STATIC_EMPTY
    if total_text < MIN_TEXT_LENGTH_STATIC:
        return STATIC_PARTIAL

    # Fix 3: SSR pages with substantial text but no links AND no images
    # (e.g. Foxtale, React-hydrated pages) — trigger JS enrichment to load resources
    total_links = sum(len(s.get("content", {}).get("links", [])) for s in sections)
    total_images = sum(len(s.get("content", {}).get("images", [])) for s in sections)
    if total_text > 2000 and total_links == 0 and total_images == 0:
        return STATIC_PARTIAL  # trigger JS enrichment to capture hydrated resources

    return STATIC_COMPLETE


# ─────────────────────────────────────────────────────────────────────────────
# Main extraction function — shared by static and browser paths
# ─────────────────────────────────────────────────────────────────────────────

def extract_page(source: PageSource, strategy: str = "static") -> dict:
    """Parse HTML → canonical result dict. Never raises; errors go into result.

    Correct pipeline order (Bug 1 fix):
    1. Parse HTML
    2. Extract metadata FIRST (before any decomposition)
    3. Extract structured data FIRST (before any decomposition)
    4. Extract and remove components (_extract_components)
    5. Clean remaining noise (_clean)
    6. Find primary content root (two-pass: priority → scoring)
    7. Build sections
    8. Diagnostic warning if no sections from non-empty root (Bug 4)
    9. Fallback section with guard against Doctype text (Bug 3)
    10. Deduplicate, merge resources, compose result
    """
    # Non-HTML content types
    if source.content_type and not source.content_type.startswith(
        ("text/html", "application/xhtml", "text/")
    ):
        return _empty_page(source, strategy, "")

    try:
        soup = BeautifulSoup(source.html or "", "lxml")
    except Exception as exc:
        return _empty_page(source, strategy, "", [{"message": f"Parse error: {exc}", "phase": "parse", "recoverable": False}])

    errors: list[dict] = []

    # ── Step 1: Extract metadata BEFORE any decomposition (Bug 1 fix) ─────────
    try:
        meta = _metadata(soup, source.final_url)
    except Exception as exc:
        meta = {"title": "", "description": "", "language": "en", "canonical": None, "openGraph": {}, "twitter": {}}
        errors.append({"message": f"Metadata error: {exc}", "phase": "parse", "recoverable": True})

    # ── Step 2: Extract structured data BEFORE decomposition ──────────────────
    try:
        structured = _structured_data(soup)
    except Exception:
        structured = []

    # ── Step 3: Extract and remove page chrome ─────────────────────────────────
    try:
        components = _extract_components(soup, source.final_url)
    except Exception as exc:
        components = {"navigation": [], "tableOfContents": [], "languageSelector": [], "sidebar": [], "footer": []}
        errors.append({"message": f"Component extraction error: {exc}", "phase": "analysis", "recoverable": True})

    # ── Step 4: Clean remaining noise ─────────────────────────────────────────
    try:
        _clean(soup)
    except Exception:
        pass

    # ── Step 5: Primary content root (two-pass, Bug 2 fix) ───────────────────
    try:
        root = _primary_root(soup)
    except Exception:
        root = soup.body or soup

    # ── Step 6: Build sections ────────────────────────────────────────────────
    try:
        sections = _build_sections(root, source.final_url)
    except Exception as exc:
        sections = []
        errors.append({"message": f"Section build error: {exc}", "phase": "extract", "recoverable": True})

    # ── Step 7: Diagnostic warning when sections empty but root has content (Bug 4) ──
    if not sections and root and len(_text(root)) > 200:
        errors.append({
            "message": (
                f"Section builder found no headings in root element "
                f"({root.name}#{_safe_get(root, 'id')}). "
                f"Root has {len(_text(root))} chars of text."
            ),
            "phase": "extract",
            "recoverable": True,
        })

    # ── Step 8: Fallback section with guard against Doctype text (Bug 3) ──────
    if not sections:
        try:
            text = _text(root)[:MAX_TEXT_PER_SECTION]
            # Bug 3 fix: guard against Doctype NavigableString producing "html"
            if len(text.strip()) < 10:
                body = soup.body
                if body:
                    text = _text(body)[:MAX_TEXT_PER_SECTION]
            if text and len(text.strip()) >= 10:
                sections = [{
                    "id": "h1-content",
                    "parentId": None,
                    "depth": 0,
                    "level": 1,
                    "type": "section",
                    "label": meta.get("title") or "Content",
                    "sourceUrl": source.final_url,
                    "content": {
                        "headings": [],
                        "text": text,
                        **_resources(root, source.final_url),
                    },
                    "rawHtml": str(root)[:RAW_HTML_TRUNCATE],
                    "truncated": len(str(root)) > RAW_HTML_TRUNCATE,
                }]
        except Exception as exc:
            errors.append({"message": f"Fallback extraction error: {exc}", "phase": "extract", "recoverable": True})

    # ── Step 9: Deduplication ─────────────────────────────────────────────────
    try:
        sections, deduped_count = _dedup_sections(sections)
    except Exception:
        deduped_count = 0

    # ── Step 10: Merge resources ──────────────────────────────────────────────
    try:
        resources = _merge_resources(sections)
    except Exception:
        resources = {"links": [], "images": [], "lists": [], "tables": [], "videos": [], "audio": [], "documents": []}

    total_text_chars = sum(len(s.get("content", {}).get("text", "")) for s in sections)

    return {
        "page": {
            "finalUrl": source.final_url,
            "contentType": source.content_type or "text/html",
            "title": meta.get("title", ""),
            "language": meta.get("language", "en"),
        },
        "metadata": {**meta, "structuredData": structured},
        "components": components,
        "content": {
            "title": next((s["label"] for s in sections if s.get("level") == 1), meta.get("title", "")),
            "introduction": "",
            "sections": sections,
        },
        "resources": {**resources, "videos": [], "audio": [], "documents": []},
        "statistics": {
            "strategy": strategy,
            "sections": len(sections),
            "sectionsDeduped": deduped_count,
            "textCharacters": total_text_chars,
            "linksFound": len(resources.get("links", [])),
            "imagesFound": len(resources.get("images", [])),
            "fetchDurationMs": round(source.fetch_duration_ms, 1),
            "renderDurationMs": 0.0,
            "totalDurationMs": 0.0,
        },
        "warnings": [],
        "errors": errors,
    }


def _empty_page(source: PageSource, strategy: str, text: str, errors: list | None = None) -> dict:
    return {
        "page": {
            "finalUrl": source.final_url,
            "contentType": source.content_type or "",
            "title": "",
            "language": "en",
        },
        "metadata": {
            "title": "", "description": "", "language": "en",
            "canonical": None, "openGraph": {}, "twitter": {}, "structuredData": [],
        },
        "components": {
            "navigation": [], "tableOfContents": [],
            "languageSelector": [], "sidebar": [], "footer": [],
        },
        "content": {"title": "", "introduction": text, "sections": []},
        "resources": {
            "links": [], "images": [], "lists": [], "tables": [],
            "videos": [], "audio": [], "documents": [],
        },
        "statistics": {
            "strategy": strategy,
            "sections": 0,
            "sectionsDeduped": 0,
            "textCharacters": len(text),
            "linksFound": 0,
            "imagesFound": 0,
            "fetchDurationMs": round(source.fetch_duration_ms, 1),
            "renderDurationMs": 0.0,
            "totalDurationMs": 0.0,
        },
        "warnings": [],
        "errors": errors or [],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Backward-compat aliases
# ─────────────────────────────────────────────────────────────────────────────

GENERIC_CONTENT_SELECTORS = _CONTENT_SELECTORS
