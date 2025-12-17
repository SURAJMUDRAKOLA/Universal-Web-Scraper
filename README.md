# Universal Website Scraper (MVP) + JSON Viewer

A full-stack universal website scraper built for the **Lyftr AI Full-Stack Assignment**.
The system extracts structured, section-aware data from both **static** and **JavaScript-rendered** websites and provides a minimal frontend to view and download the scraped JSON.

---

## Features

- ✅ Static HTML scraping
- ✅ JavaScript-rendered page scraping (Playwright)
- ✅ Section-aware content extraction
- ✅ Click flows (tabs, “Load more / Show more”)
- ✅ Pagination and infinite scroll (depth ≥ 3)
- ✅ Noise filtering (cookie banners, modals, overlays)
- ✅ Interaction tracking (clicks, scrolls, visited pages)
- ✅ JSON API + Web UI viewer
- ✅ Download scraped JSON

---

## Tech Stack

- **Language**: Python 3.10+
- **Backend**: FastAPI + Uvicorn
- **Static Parsing**: httpx, BeautifulSoup4, lxml
- **JS Rendering**: Playwright (Chromium)
- **Frontend**: Jinja2 templates + Vanilla JavaScript
- **Runtime**: uvicorn

---

## Setup & Run Instructions

### Requirements
- Python 3.10 or higher
- Internet connection (for Playwright browser download)

---

### Run Using `run.sh` (Recommended)

```bash
chmod +x run.sh
./run.sh
```

What `run.sh` does:
- Creates and activates a virtual environment (on Unix-like shells)
- Installs dependencies from `requirements.txt`
- Installs Playwright browsers
- Starts the server at `http://localhost:8000`

> Note: On Windows `run.sh` requires Git Bash or WSL. See Manual Setup for native PowerShell steps.

---

### Manual Setup (Native Windows PowerShell)

Open PowerShell and run:

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
playwright install
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Or run the server directly after activating the venv:

```powershell
venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --port 8000
```

---

## API Endpoints

- `GET /healthz` — Health check

Response:

```json
{ "status": "ok" }
```

- `POST /scrape` — Scrape a given URL and return structured JSON

Request JSON example:

```json
{
	"url": "https://example.com"
}
```

Simplified response example:

```json
{
	"result": {
		"url": "https://example.com",
		"scrapedAt": "2025-12-17T00:00:00Z",
		"meta": {
			"title": "Page Title",
			"description": "",
			"language": "en",
			"canonical": "https://example.com"
		},
		"sections": [ /* section-aware content */ ],
		"interactions": {
			"clicks": [],
			"scrolls": 3,
			"pages": ["https://example.com"]
		},
		"errors": []
	}
}
```

---

## Frontend (JSON Viewer)

Open http://localhost:8000 in your browser. The UI provides:

- URL input field
- “Scrape” button
- Loading indicator
- Expandable section viewer
- View raw JSON per section
- Download full JSON result

---

## Primary Test URLs

1. Wikipedia — Artificial Intelligence

```
https://en.wikipedia.org/wiki/Artificial_intelligence
```

- Mostly static content — validates static scraping and section extraction

2. Hacker News

```
https://news.ycombinator.com/
```

- Pagination-based listing — tests pagination depth ≥ 3 and click tracking

3. Infinite Scroll Demo

```
https://infinite-scroll.com/demo/full-page/
```

- Infinite scroll content loading — tests scroll depth ≥ 3 and scroll tracking

Additional tested sites:

- `https://vercel.com/` — JS-heavy marketing site
- `https://dev.to/t/javascript` — “Load more” content
- `https://nextjs.org/docs` — SPA documentation site

---

## How the Scraper Works

1. Static-First Strategy

- Fetches HTML using `httpx`
- Parses with `BeautifulSoup` + `lxml`
- Extracts meta tags, semantic sections, headings, text, links, images, lists, tables

2. JS Rendering Fallback

Triggered when site is known JS-heavy or static output appears insufficient. Uses Playwright to:

- Render JavaScript
- Wait for network idle
- Remove noise elements (cookie banners, modals)
- Perform interactions (tabs, load-more, pagination, infinite scroll)

3. Interactions Implemented

- Tabs: `[role="tab"]` and common button selectors
- Load More: buttons with text like “Load more” or “Show more”
- Pagination: “Next” links
- Infinite Scroll: scroll height & content growth detection

All interactions are logged under `interactions: { clicks, scrolls, pages }` in the JSON.

---

## Noise Filtering

Automatically removes cookie banners, modals, popups, overlays and dialogs via DOM cleanup before extraction.

---

## Known Limitations

- Bot-protected sites may block scraping
- Infinite scroll detection is heuristic-based
- Very complex SPAs may yield partial sections
- No retry or rate-limiting implemented
- Same-origin navigation only
- Long-loading sites may hit timeouts
- `run.sh` requires Git Bash / WSL on Windows

---

## Project Structure

```
universal-web-scraper/
├── app/
│   ├── main.py
│   ├── scraper/
│   │   ├── static.py
│   │   └── js.py
│   └── frontend/
│       └── templates/
│           └── index.html
├── run.sh
├── requirements.txt
├── README.md
├── design_notes.md
└── capabilities.json
```

---

## License

MIT License — Created exclusively for Lyftr AI – Full-Stack Assignment

---

## Author

Name: Suraj Mudrakola

Email: surajmudrakola808@gmail.com

---
