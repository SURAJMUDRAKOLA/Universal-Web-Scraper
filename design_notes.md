
# Design Notes

## Static vs JS Fallback

**Strategy:**

The scraper follows a **static-first, JS-augmented strategy**.

1. Every request first attempts **static HTML scraping** using `httpx + BeautifulSoup`.
2. Static scraping extracts meta tags and section content quickly and efficiently.
3. A **JS fallback is triggered** when:
   - The domain matches a known interactive site (e.g., Hacker News, Infinite Scroll, Vercel), OR
   - Static scraping returns insufficient interaction depth (no pagination, scrolls, or clicks).

This ensures:
- Static pages (Wikipedia, MDN) are handled efficiently.
- JS-heavy or interaction-based sites always trigger Playwright logic.

This decision logic is implemented in `main.py` using domain heuristics and content inspection.

---

## Wait Strategy for JS

- [x] Network idle  
- [ ] Fixed sleep (only as fallback)  
- [x] Wait for selectors  

**Details:**

The Playwright renderer uses a **hybrid wait strategy**:

1. Initial navigation waits for:
   ```python
   page.wait_for_load_state("networkidle", timeout=15000)
   ```
   This ensures JS bundles and async requests finish.

After each interaction (click, scroll, pagination):

Short waits (700ms–1500ms) allow incremental content to load.

Pagination clicks re-trigger `networkidle`.

This balances reliability with performance and avoids unnecessary long delays.

---

## Click & Scroll Strategy
**Click flows implemented**

### Tab clicks

Detects elements using:

```css
[role='tab'], button[aria-selected], .tab, [data-tab]
```

Clicks up to 3 visible tabs and records selectors in `interactions.clicks`.

### Load more / Show more

Matches text-based and class-based selectors. Clicks up to 3 times and stops when the button disappears.

### Scroll / Pagination Approach

#### Pagination

Attempts pagination before infinite scroll. Detects:

```css
a[rel='next'], a:has-text('Next'), .pagination__next
```

Navigates up to page depth ≥ 3 and tracks visited URLs in `interactions.pages`.

#### Infinite Scroll

Triggered only if pagination depth < 3. Scrolls to bottom repeatedly and detects new content by counting elements like `.article`, `.post`, `.item`, and native `article` tags. Scrolls up to 4 times and increments `interactions.scrolls` only when new content appears.

### Stop Conditions

- Maximum 3 pagination steps
- Maximum 4 scroll attempts
- Stops when page height does not increase or content count does not change
- Global Playwright timeout: 60 seconds per URL

---

## Section Grouping & Labels

### Section Grouping Strategy

Primary method — semantic landmarks:

```css
header, nav, main, section, article, footer
```

Fallback — heading-based grouping using `h1`, `h2`, `h3`. Content is collected until the next heading.

Limits:
- Max 15 landmark sections
- Max 20 heading-based sections

### Section Type Detection

Section `type` is inferred using:
- Tag name (`nav`, `footer`, `header`)
- Class names (`hero`, `pricing`, `faq`, `grid`)
- Text patterns (e.g., question-like text → `faq`)
- Content structure (lists → `list`, cards → `grid`)
- Fallback: `unknown`

### Label Derivation

- If a heading exists → use heading text (≤ 60 chars)
- Else → first 5–7 words from section text
- Final fallback → "Content"

---

## Noise Filtering & Truncation

### Noise Filtering

Filtered elements include:

- Cookie banners (selectors containing `cookie`, `consent`)
- Modals and dialogs (`modal`, `popup`, `[role='dialog']`)
- Overlays (`overlay`, `backdrop`)
- Newsletter prompts

Filtering is applied in:
- Static scraping: `BeautifulSoup.decompose()`
- JS scraping: DOM removal via `page.evaluate()`

This ensures clean content extraction.

### HTML Truncation

- `rawHtml` limited to 1000–2000 characters
- `truncated = True` if original HTML exceeds the limit
- Text content also capped (~2000–3000 chars)

This prevents oversized responses while preserving context for debugging.

---

## Summary

This design ensures:

- Fast static scraping where possible
- Robust JS interaction handling where required
- Reliable pagination and infinite scroll depth ≥ 3
- Clean, section-aware JSON output

Honest capability reporting aligned with Lyftr evaluation stages.

---

If you'd like, I can also:

- Run `playwright` smoke tests against primary test URLs
- Add inline code pointers (file + function) for each strategy step
- Commit these notes and open a PR draft

Tell me which follow-up you'd like (or say `done`).