import httpx
from bs4 import BeautifulSoup
from urllib.parse import urljoin


# Noise selectors to remove
NOISE_SELECTORS = [
    "[id*='cookie']", "[class*='cookie']",
    "[id*='consent']", "[class*='consent']",
    "[class*='modal']", "[class*='popup']",
    "[class*='newsletter']", "[class*='overlay']",
    "[role='dialog']", "[aria-modal='true']"
]


def detect_section_type(tag):
    """Detect semantic section type based on tag and attributes."""
    tag_name = tag.name.lower() if tag.name else ""
    classes = " ".join(tag.get("class", [])).lower()
    tag_id = (tag.get("id") or "").lower()
    text = tag.get_text(" ", strip=True).lower()[:200]

    # Check for specific semantic types
    if tag_name == "nav" or "nav" in classes:
        return "nav"
    if tag_name == "footer" or "footer" in classes:
        return "footer"
    if "hero" in classes or "banner" in classes or tag_name == "header":
        return "hero"
    if "faq" in classes or "faq" in tag_id or "question" in text:
        return "faq"
    if "pricing" in classes or "pricing" in tag_id or "price" in text:
        return "pricing"
    if "grid" in classes or "cards" in classes:
        return "grid"
    if tag.find_all(["ul", "ol"], limit=3):
        return "list"

    return "section"


def static_scrape(url: str):
    meta = {
        "title": "",
        "description": "",
        "language": "en",
        "canonical": None,
    }
    sections = []

    try:
        resp = httpx.get(url, timeout=15, follow_redirects=True)
        html = resp.text
    except Exception as e:
        return meta, sections, str(e)

    soup = BeautifulSoup(html, "lxml")

    # === NOISE FILTERING ===
    for selector in NOISE_SELECTORS:
        for elem in soup.select(selector):
            elem.decompose()

    # === META EXTRACTION ===
    if soup.title:
        meta["title"] = soup.title.text.strip()

    # Try multiple meta description sources
    desc = soup.find("meta", attrs={"name": "description"}) or \
           soup.find("meta", attrs={"property": "og:description"})
    if desc and desc.get("content"):
        meta["description"] = desc["content"]

    html_tag = soup.find("html")
    if html_tag and html_tag.get("lang"):
        meta["language"] = html_tag["lang"]

    canon = soup.find("link", rel="canonical")
    if canon and canon.get("href"):
        meta["canonical"] = urljoin(url, canon["href"])

    # === SECTION EXTRACTION ===
    # Try semantic landmarks first
    landmarks = soup.find_all(["header", "nav", "main", "section", "article", "footer"])
    
    if landmarks:
        for i, section_tag in enumerate(landmarks[:15]):  # Limit to 15 sections
            section_data = extract_section(section_tag, url, i)
            if section_data and section_data["content"]["text"]:
                sections.append(section_data)
    
    # Fallback: heading-based extraction
    if not sections:
        for i, h in enumerate(soup.find_all(["h1", "h2", "h3"])[:20]):
            title = h.get_text(" ", strip=True)
            if not title:
                continue

            # Gather text blocks after heading
            text_blocks = []
            for sib in h.find_next_siblings():
                if sib.name in ["h1", "h2", "h3"]:
                    break
                if sib.name == "p":
                    text_blocks.append(sib.get_text(" ", strip=True))

            text = " ".join(text_blocks)[:2000]

            # Extract links
            links = []
            for a in h.find_all_next("a", href=True, limit=10):
                next_heading = a.find_parent(["h1", "h2", "h3"])
                if next_heading and next_heading != h:
                    break
                href = urljoin(url, a["href"])
                links.append({
                    "text": a.get_text(strip=True)[:100],
                    "href": href
                })

            # Extract images
            images = []
            for img in h.find_all_next("img", limit=5):
                if img.find_parent(["h1", "h2", "h3"]):
                    continue
                src = img.get("src") or img.get("data-src")
                if src:
                    images.append({
                        "src": urljoin(url, src),
                        "alt": img.get("alt", "")
                    })

            sections.append({
                "id": f"section-{i}",
                "type": "section",
                "label": title[:60],
                "sourceUrl": url,
                "content": {
                    "headings": [title],
                    "text": text,
                    "links": links[:10],
                    "images": images[:5],
                    "lists": [],
                    "tables": []
                },
                "rawHtml": str(h)[:1000],
                "truncated": len(str(h)) > 1000
            })

    return meta, sections, None


def extract_section(tag, base_url, index):
    """Extract structured data from a section tag."""
    # Get section type
    section_type = detect_section_type(tag)

    # Extract headings
    headings = [h.get_text(" ", strip=True) for h in tag.find_all(["h1", "h2", "h3", "h4"], limit=5)]
    
    # Generate label
    if headings:
        label = headings[0][:60]
    else:
        text_preview = tag.get_text(" ", strip=True)[:50]
        words = text_preview.split()[:7]
        label = " ".join(words)

    # Extract text
    paragraphs = [p.get_text(" ", strip=True) for p in tag.find_all("p", limit=10)]
    text = " ".join(paragraphs)[:3000]

    # Extract links (absolute URLs)
    links = []
    for a in tag.find_all("a", href=True, limit=15):
        href = urljoin(base_url, a["href"])
        links.append({
            "text": a.get_text(strip=True)[:100],
            "href": href
        })

    # Extract images
    images = []
    for img in tag.find_all("img", limit=8):
        src = img.get("src") or img.get("data-src")
        if src:
            images.append({
                "src": urljoin(base_url, src),
                "alt": img.get("alt", "")
            })

    # Extract lists
    lists = []
    for ul in tag.find_all(["ul", "ol"], limit=5):
        items = [li.get_text(" ", strip=True) for li in ul.find_all("li", limit=10)]
        if items:
            lists.append(items)

    # Extract tables
    tables = []
    for table in tag.find_all("table", limit=3):
        rows = []
        for tr in table.find_all("tr", limit=10):
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if cells:
                rows.append(cells)
        if rows:
            tables.append(rows)

    # Get raw HTML
    raw_html = str(tag)[:2000]

    return {
        "id": f"{section_type}-{index}",
        "type": section_type,
        "label": label or "Content",
        "sourceUrl": base_url,
        "content": {
            "headings": headings,
            "text": text,
            "links": links,
            "images": images,
            "lists": lists,
            "tables": tables
        },
        "rawHtml": raw_html,
        "truncated": len(str(tag)) > 2000
    }
