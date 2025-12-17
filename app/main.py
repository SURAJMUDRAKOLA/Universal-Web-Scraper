from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

from app.scraper.static import static_scrape, extract_section
from app.scraper.js import js_scrape

app = FastAPI()
templates = Jinja2Templates(directory="app/frontend/templates")


class ScrapeRequest(BaseModel):
    url: str


# ---------------- JS HEAVY SITES ----------------
JS_HEAVY_PATTERNS = [
    "vercel.com",
    "nextjs.org",
    "mui.com",
    "dev.to",
    "news.ycombinator.com",
    "infinite-scroll.com",
    "unsplash.com",
    "reddit.com",
]


def should_use_js(url: str, sections: list) -> bool:
    """
    Decide when JS scraping is REQUIRED even if static scraping worked.
    """
    domain = urlparse(url).netloc.lower().replace("www.", "")

    # 1️⃣ Force JS for known interactive sites
    for pattern in JS_HEAVY_PATTERNS:
        if pattern in domain:
            return True

    # 2️⃣ Force JS if static content is weak
    if not sections:
        return True

    # If sections exist but almost no text, still use JS
    total_text = sum(len(s["content"]["text"]) for s in sections)
    if total_text < 300:
        return True

    return False


# ---------------- HEALTH CHECK ----------------
@app.get("/healthz")
def healthz():
    return {"status": "ok"}


# ---------------- FRONTEND ----------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# ---------------- SCRAPE API ----------------
@app.post("/scrape")
def scrape(data: ScrapeRequest):
    meta, sections, error = static_scrape(data.url)

    errors = []
    interactions = {
        "clicks": [],
        "scrolls": 0,
        "pages": [data.url]
    }

    if error:
        errors.append({"message": error, "phase": "fetch"})

    # ================= JS STRATEGY =================
    if should_use_js(data.url, sections):
        html, js_interactions, js_error = js_scrape(data.url)

        if js_error:
            errors.append({"message": js_error, "phase": "render"})
        else:
            interactions = js_interactions
            soup = BeautifulSoup(html, "lxml")
            sections = []

            # ---------- LANDMARK SECTIONS ----------
            landmarks = soup.find_all(
                ["header", "nav", "main", "section", "article", "footer"]
            )

            for i, tag in enumerate(landmarks[:20]):
                section = extract_section(tag, data.url, i)
                if section and section["content"]["text"]:
                    sections.append(section)

            # ---------- HEADING FALLBACK ----------
            if not sections:
                for i, h in enumerate(soup.find_all(["h1", "h2", "h3"])[:30]):
                    title = h.get_text(" ", strip=True)
                    if not title:
                        continue

                    text_blocks = []
                    for sib in h.find_next_siblings():
                        if sib.name in ["h1", "h2", "h3"]:
                            break
                        if sib.name == "p":
                            text_blocks.append(sib.get_text(" ", strip=True))

                    sections.append({
                        "id": f"js-section-{i}",
                        "type": "section",
                        "label": title[:60],
                        "sourceUrl": data.url,
                        "content": {
                            "headings": [title],
                            "text": " ".join(text_blocks)[:2000],
                            "links": [],
                            "images": [],
                            "lists": [],
                            "tables": []
                        },
                        "rawHtml": str(h)[:1000],
                        "truncated": len(str(h)) > 1000
                    })

    # ---------------- FINAL SAFETY ----------------
    if not sections:
        sections = [{
            "id": "fallback-0",
            "type": "unknown",
            "label": "Content",
            "sourceUrl": data.url,
            "content": {
                "headings": [],
                "text": "No content extracted",
                "links": [],
                "images": [],
                "lists": [],
                "tables": []
            },
            "rawHtml": "",
            "truncated": False
        }]

    return {
        "result": {
            "url": data.url,
            "scrapedAt": datetime.utcnow().isoformat() + "Z",
            "meta": meta,
            "sections": sections,
            "interactions": interactions,
            "errors": errors
        }
    }
