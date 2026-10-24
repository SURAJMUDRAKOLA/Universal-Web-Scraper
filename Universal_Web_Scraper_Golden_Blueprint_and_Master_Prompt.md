# Universal Web Scraper --- Golden Blueprint & Master Prompt

**Repository:** https://github.com/SURAJMUDRAKOLA/Universal-Web-Scraper\
**Mission:** Repair and improve the existing scraper through a general,
adaptive architecture---not site-specific patches. Prioritize
correctness, transparent outcomes, security, deterministic tests, and
maintainability.

> No scraper can be guaranteed to work perfectly on every website. Some
> pages require authentication, expose incomplete public data, or
> restrict automated access. The goal is robust behavior with honest
> outcomes, not bypassing access controls or pretending extraction
> succeeded.

# 1. Instructions to the coding agent

You are the senior engineer responsible for implementing and verifying
this existing project. Read this document before changing code.

-   Inspect the current repository, not just this prompt. Previous audit
    findings may be stale.
-   Trace the complete path from URL submission through fetching,
    parsing, browser fallback, merging, response composition, and
    frontend rendering.
-   Preserve the existing stack and API where reasonable. Avoid
    unnecessary rewrites and dependencies.
-   Do not hardcode Amazon, Foxtale, Wikipedia, or any other domain or
    site-specific selector.
-   Do not fabricate records, prices, media URLs, page counts, or
    successful interactions.
-   Do not equate browser launch/navigation success with extraction
    success.
-   Do not bypass CAPTCHA, anti-bot challenges, access controls,
    authentication, or denials. Detect and report them. Do not add
    stealth fingerprinting, challenge solving, or proxy rotation to
    evade restrictions.
-   Implement; do not stop after writing a plan. Add tests, run them,
    fix regressions, and report evidence.
-   If repository write access or a required service is unavailable, say
    so and provide exact patches or a clear manual workflow. Never claim
    changes were committed when they were not.

# 2. Priority order
## P0 — Adaptive Scrapeability and Honest Failure Classification

Build a generic capability-assessment and failure-classification layer. For every URL, determine what the current scraper can actually access and extract, using static HTTP fetching first and bounded browser rendering when necessary.

Return one of the following outcomes, based on observable evidence:

- `success`: useful content was extracted.
- `partial`: some useful content was extracted, but relevant extraction steps failed or content appears incomplete.
- `empty`: the request completed, but no useful content was extracted and no stronger failure category was established.
- `auth_required`: login, authentication, or authorization appears necessary.
- `challenge_detected`: CAPTCHA, anti-bot, or another access challenge was detected.
- `access_denied`: the server explicitly denied the request, such as with a 403 response, where the evidence does not establish a more specific cause.
- `unsupported`: the current implementation lacks the required extraction capability, with the limitation explicitly identified.
- `failed`: a request, navigation, parsing, or execution error prevented useful extraction.

Do not classify a website as permanently unsupported based on one failed request. Classify the outcome of the current attempt and include evidence, confidence, and the specific reason.

Separate:
1. HTTP request outcome and status code.
2. Browser launch and navigation outcome.
3. Authentication/challenge/access-denial detection.
4. Extraction success and extracted content counts.
5. Whether partial data was preserved.
6. The specific limitation and safe next steps.

Do not claim CAPTCHA, authentication, or anti-bot protection was definitively identified unless evidence supports it. A 403 alone does not prove a CAPTCHA or anti-bot system is responsible.

Never bypass access controls, solve CAPTCHAs automatically, evade anti-bot defenses, or misrepresent blocked content as successfully scraped. When authorized access is possible, explain the supported route, such as an official API or an explicitly authorized authenticated workflow.

Preserve valid partial results. Do not fabricate content. Keep status, strategy, reason codes, UI messages, and counts consistent across the backend, API response, frontend, documentation, and tests.

Add deterministic fixtures for every outcome and verify that the scraper reports the correct classification, evidence, extracted data, and next steps. Test the current attempt, not an unsupported claim about every possible access method for the entire website.

## P0 --- Critical correctness and safety (must complete first)

1.  Audit baseline and end-to-end call path.
2.  Correct static-versus-JavaScript strategy classification.
3.  Separate request/navigation success from extraction success.
4.  Preserve static data and implement field-level static/rendered
    merging.
5.  Extract page-wide tables and dedicated links, lists, images, videos,
    audio, documents, and metadata.
6.  Detect generic repeated cards/listing records without
    domain-specific rules.
7.  Fix bounded browser waits, scrolling, safe interactions, and
    pagination accounting.
8.  Validate initial URLs, redirects, pagination links, and browser
    navigation; enforce resource limits.
9.  Prevent frontend XSS and safely display untrusted scraped data.
10. Add deterministic fixtures, regression tests, and CI.

## P1 --- Extraction quality and resilience

1.  Improve main-content scoring and section boundaries.
2.  Improve lazy/responsive media and frame-aware embed detection.
3.  Improve generic repeated-record extraction, normalization, and
    deduplication.
4.  Add extraction-quality diagnostics, structured logs, timeout/retry
    handling, and resource cleanup.
5.  Audit config values and enforce or remove unused ones.

## P2 --- Documentation and product quality

1.  Update README, capability declarations, examples, and
    troubleshooting to match verified behavior.
2.  Improve UI states for empty, partial, blocked, unresolved, and
    failed outcomes.
3.  Add further fixture coverage and optional features only after
    correctness is measured.

Do not move to P1/P2 while P0 has false-success states, lost data,
unsafe navigation, or inaccurate strategy labels.

# 3. Phase A --- Inspect and record a baseline

Inspect: - Repository structure, dependencies, and runtime versions. -
Application entry point and API routes. - Static fetcher/parser and
quality analyzer. - Playwright/browser lifecycle, waits, scrolling,
click handling, and pagination. - URL validation, redirect handling, and
size/time limits. - Response models, merge/deduplication logic, and
count computation. - Frontend rendering, errors, copy/download
behavior. - Config variables and whether each is enforced. - README,
capability files, `.gitignore`, tests, and CI.

Trace a request end to end. Record actual call paths, errors swallowed,
mismatched fields, data overwritten, limits not enforced, and claims
that are not backed by tests. Run existing tests/build/lint where
available before editing.

Build local fixtures for a static article, lazy images, a table outside
main content, repeated cards, native video, iframe video, lazy iframe,
load-more, tabs, three-page pagination, repeated-page loops, empty app
shell, challenge page, redirects, HTTP errors, slow/oversized responses,
malformed HTML, and malicious strings.

Do not rely on live websites as the only test suite: they change and may
block requests.

# 4. P0 --- Strategy classification

A known failure mode is that soft signals such as lazy images or
pagination can incorrectly make a complete static article appear to need
JavaScript. Static quality must be judged by extracted usable content,
not just page features.

## Strategy semantics

Use these meanings consistently (preserve existing public enum names
only if compatibility requires it, and document their equivalents):

-   **`static`**: static HTML provided the usable content; browser
    rendering was unnecessary.
-   **`js`**: static HTML was insufficient, and rendered DOM supplied
    the usable content.
-   **`static+js`**: static and rendered extraction both contributed
    distinct, meaningful data.
-   **`blocked` / `challenge_detected`**: access challenge or denial was
    detected. This is an outcome, not proof of successful JavaScript
    extraction.
-   **`empty` / `failed`**: no usable content was extracted, with a
    clear reason.

## Evidence groups

**Hard signals of static insufficiency** may include a nearly empty body
plus a clear app-shell pattern, an explicit JavaScript-required message,
or extremely low useful content across meaningful types.

**Soft signals** include lazy images, pagination, tabs, expandable
sections, script bundles, and load-more controls. These may justify a
browser attempt, but alone must not force the strategy to JavaScript.

**Quality evidence** includes meaningful text and headings, links,
images, tables/lists where present, repeated records where present,
metadata, and whether the page resembles a complete
article/listing/detail page or only an app shell.

## Decision policy

1.  Fetch initial HTML safely.
2.  Extract static content.
3.  Score the actual result and record why.
4.  If static content is sufficient, return `static`; lazy images alone
    must not trigger browser rendering.
5.  If static content is insufficient---or the user explicitly requested
    browser rendering---try Playwright within limits.
6.  Run the same core extraction schema against rendered DOM.
7.  Merge valid complementary data.
8.  If browser ran but contributed nothing, do not label `static+js`.
9.  Detect challenge/denial separately and do not treat challenge HTML
    as target content.
10. If both paths fail, return a structured empty/failure result.

Calibrate scores and thresholds with fixtures. Keep reasons observable;
avoid arbitrary thresholds without tests.

## Required regressions

-   Complete static article + lazy images + no rendered additions →
    `static`.
-   Empty app shell + useful rendered content → `js`.
-   Static content plus new rendered-only records → `static+js`.
-   Successful browser navigation with no usable content → not success
    merely because navigation worked.
-   Challenge fixture → challenge status, not successful extraction.
-   Browser failure after useful static extraction → retain static data
    and report partial browser failure.

# 5. P0 --- Separate request, browser, and extraction status

The response should distinguish these concepts: - `requestSucceeded` -
`httpStatus` when available - `browserAttempted` - `browserLaunched` -
`navigationSucceeded` - `extractionSucceeded` - `challengeDetected` -
`partial` - `strategy` - machine-readable `errors` and `warnings`

A 200 response is not proof of useful content. Successful `page.goto()`
is not proof of extraction. A page title such as "JavaScript is
disabled" must not become the target page title if the page is actually
an error/fallback/challenge page.

Preserve HTTP 403, 404, 429, and 5xx meaningfully. Differentiate invalid
URL, DNS/connect failure, timeout, HTTP denial, challenge, size limit,
browser failure, parse failure, and "no content found." Do not leak
cookies, authorization headers, secrets, internal network details, or
raw stack traces.

**Invariant:** no field may report success unless that field's
documented success criterion was met.

# 6. P0 --- Static extraction architecture

Use multiple independent layers, not only heading-owned fragments:

1.  Document metadata.
2.  Main text and headings.
3.  Non-overlapping sections.
4.  Page-wide global resources.
5.  Generic repeated records/cards.
6.  Safe interaction and pagination candidates.
7.  Diagnostics and confidence/provenance.

Run equivalent core extraction on static HTML and rendered DOM.

## Main content and sections

-   Score candidate containers instead of returning the first selector
    match over a fixed text threshold.
-   Use semantic tags such as `main`, `article`, headings, and lists as
    evidence, not absolute truth.
-   Consider text density, link density, repeated structure, heading
    distribution, and container size.
-   Keep global resource extraction separate from main-content
    selection.
-   Exclude scripts/styles and irrelevant implementation text from
    visible text.
-   Avoid selecting broad containers that duplicate navigation, footer,
    or nested content.
-   Associate heading content only until the next heading of the same or
    higher level. Avoid duplicating child headings' content in parent
    sections.
-   Use stable fingerprints where practical, but do not remove
    legitimate repeated phrases or rows just because text matches.

## Tables --- critical

Implement a dedicated **page-wide table pass**. Tables must not be
discovered only when they happen to fall inside a selected section.

Extract caption, headers, rows, cells, order, and relevant table
metadata. Handle `thead`, `tbody`, `tfoot`, `th`, `td`, and reasonable
rowspan/colspan behavior. Distinguish "no table found" from "table
detected but parsing failed." Avoid layout tables where confidently
identified, but do not over-filter data tables. Deduplicate by a
conservative fingerprint and source context.

Counts must reflect unique tables in the final response, not only tables
inside article sections.

## Links and lists

-   Extract resolved href, visible text, title/rel, and source context.
-   Do not treat `javascript:` or invalid pseudo-URLs as navigable
    links.
-   Preserve useful accessible labels when visible text is empty.
-   Resolve relative URLs against the correct page URL.
-   Extract ordered/unordered/nested list structure.
-   Distinguish navigation lists from content lists where possible.
-   Avoid turning menus into the only extracted lists.

# 7. P0/P1 --- Generic repeated cards and listings

Product/search/directory pages often store information in repeated cards
rather than article sections. Add generic repeated-sibling detection.

1.  Identify candidate sibling elements with similar tag/child
    structure.
2.  Compare structural fingerprints, link/image patterns, text
    distribution, and metadata layout.
3.  Select record containers without choosing the whole page or an
    individual nested child.
4.  Extract each candidate independently.
5.  Deduplicate conservatively and preserve source context.
6.  Do not hardcode domains or site-specific selectors.

Suggested record schema: - `recordType` (conservative inferred type such
as product/article/result/event/listing/unknown) - `title` - `text` -
`url` - `imageUrls` - `priceText` only if actually present -
`metadata` - `sourceContext` - `confidence` - `fingerprint`

Do not label every card a product. Do not invent prices, ratings, or
missing fields. Keep raw text if structured interpretation is uncertain.
Test cards with missing images, separate image/title links, nested
recommendations, and nested buttons.

# 8. P0/P1 --- Images, video, audio, and documents

Zero videos on every site can mean either incomplete detection or
misleading response reporting. Fix both extraction and diagnostics.

## Images

Support common candidates: - `src`, `data-src`, `data-lazy-src`,
`data-original`, `data-image`, `data-lazy` - `srcset`, `data-srcset` -
`<picture><source srcset=...>` - browser `currentSrc` where useful -
Open Graph image and supported structured metadata

Keep alt text, title, dimensions when available, and source context.
Normalize candidates without assuming the first URL is best. Do not
fetch every image binary just to discover its URL. Avoid counting tiny
UI icons as primary content images unless the schema intentionally
includes them.

## Video and embeds

Inspect: - `<video>` and `<source>` elements. - Common lazy-source
attributes. - `<iframe>` and lazy iframe source attributes. - Relevant
Open Graph/structured video metadata. - Embedded player URLs and
discoverable media URLs. - Accessible child frames where permitted and
safe.

Do not assume every iframe is a video. Do not claim a direct playable
media URL when only an opaque player/embed page is available.

Suggested video fields: - `url` (nullable) - `embedUrl` (nullable) -
`provider` (only when supported by evidence) - `title` (nullable) -
`sourceType` (`native`, `iframe`, `metadata`, `unknown`) -
`detectionStatus` (`direct_url`, `embed_detected`, `metadata_only`,
`unresolved`) - `sourceContext` - `confidence`

If a player is detected but the media URL is opaque, report
`embed_detected` or `unresolved`, not "no videos."

## Audio and documents

Add dedicated extraction for native `<audio>`/`<source>`, document links
(PDF and common office formats), lazy-source attributes, and relevant
metadata. Treat file extensions as evidence, not absolute proof. If the
response has audio/document arrays, they must be populated by real
extractor paths rather than left permanently empty.

## Counts

Recompute counts from the final unique result. Distinguish: -
`0 found` - `not inspected` - `extraction failed` -
`detected but unresolved`

Keep backend schema, API counts, frontend fields, README, and capability
declarations synchronized. If the frontend expects `videosFound`, the
response must define it correctly or the frontend must be updated.

# 9. P0 --- Static/rendered merge

Static and rendered extraction must use the same core schema. Merge at
field level, not merely at section level.

-   Preserve useful static fields if browser extraction fails.
-   Add rendered-only content.
-   Deduplicate URLs and records using conservative normalization.
-   Merge complementary resources into matching sections where source
    context supports it.
-   Do not discard links/images/tables just because section fingerprints
    match.
-   Avoid duplicate paragraphs and overlapping sections.
-   Track provenance (`static`, `rendered`, or both) where useful.
-   Empty browser metadata must not overwrite valid static metadata.
-   Do not replace a valid title with an error/challenge-page title
    without recording the issue.
-   Recompute all derived counts and summaries after merge.

Record diagnostics for static item count, rendered item count,
duplicates removed, new items added, fields enriched, and fields that
could not be merged reliably.

Strategy is determined by what contributed meaningful data, not by
whether Playwright ran: - only static contributed → `static` - rendered
supplied usable data because static was insufficient → `js` - both
supplied complementary meaningful data → `static+js`

# 10. P0 --- Browser waiting, scrolling, and interactions

A scroll attempt is not proof of new content. A click attempt is not
proof of successful extraction.

## Browser lifecycle

-   Use bounded navigation and overall job timeouts.
-   Avoid indefinite `networkidle` waits on sites with persistent
    requests.
-   Wait for a suitable readiness state, then bounded
    stabilization/observable DOM change.
-   Clean up pages, contexts, and browser processes in `finally` paths.
-   Catch navigation, execution-context, closed-page, and
    browser-process errors distinctly.
-   Limit concurrency, interaction count, runtime, DOM size, and
    extracted text.
-   One failed interaction must not abort all extraction.
-   Do not use long fixed sleeps as the main synchronization method.

## Scroll loop

For each scroll, compare before/after: - scroll position and document
height - visible record/resource counts - DOM fingerprint and meaningful
text - new DOM nodes or lazy-loaded content

Wait briefly for changes only when evidence suggests loading. Permit a
bounded number of no-change rounds. Stop at maximum scroll count,
runtime budget, repeated snapshots, stable bottom, or challenge
detection. Do not break after one no-change observation unless that is
the configured stop condition.

## Safe click/tab candidates

Prefer generic accessible evidence: - `role=tab`, `aria-controls`, and
related tab semantics - buttons with meaningful text or ARIA labels -
safe expand/load-more controls - pagination links with `rel=next` or
semantic labels

Do not click indiscriminately. Skip purchase, payment, login, submit,
delete, account-changing, or other high-impact actions unless explicitly
supported and safe. Inspect href, label, role, form association, and
target before action. Validate navigation destinations before clicking
and revalidate final URLs afterward.

After an action, compare URL, DOM fingerprint, text, records, and
resource counts. Record whether the action executed, whether content
changed, and how many new records were extracted.

Suggested structured event:

``` json
{
  "type": "scroll",
  "attempted": true,
  "succeeded": true,
  "contentChanged": false,
  "newItemsFound": 0,
  "reason": "No new content detected",
  "before": {"height": 2400, "records": 8},
  "after": {"height": 2400, "records": 8}
}
```

For load-more, `contentChanged` is true only when an observable change
is detected. Use typed models if the project uses Pydantic.

Report separate totals: candidates discovered, actions attempted,
actions executed without error, actions that changed content, new
records/resources, and actions skipped with reasons. If clicks are zero,
explain whether there were no candidates, candidates were unsafe, or
candidate detection failed.

# 11. P0 --- Pagination must extract each page

A list of visited URLs is not a list of extracted pages.

For every page: 1. Validate target URL. 2. Fetch/navigate safely. 3.
Extract the page independently. 4. Record requested/final URL, HTTP
status, strategy, extraction success, counts, errors, and fingerprint.
5. Merge page data with provenance. 6. Deduplicate conservatively across
pages. 7. Stop at configured page cap, runtime budget, missing next
link, challenge, repeated URL/fingerprint, or loop detection.

Each page record should include `requestedUrl`, `finalUrl`,
`httpStatus`, `extractionSucceeded`, `itemsFound`, `fingerprint`,
`error`/`warnings`, and `includedInFinalMerge`.

Prefer `rel=next`, accessible labels, and semantically credible
pagination. Validate href **before** navigation, then validate the final
URL again. Do not follow generic "next" links if context does not
support pagination. Do not claim depth of three or more unless three
pages were actually extracted and merged. Generated URLs are not
"visited" until requested; visited pages are not "extracted" until
extraction succeeds.

# 12. P0 --- URL safety and resource limits

Scraping user-provided URLs is security-sensitive. Validate every
destination, not only the initial URL.

-   Permit only supported schemes such as HTTP/HTTPS.
-   Reject embedded credentials unless a safe authenticated workflow is
    explicitly supported.
-   Normalize and validate hostname, port, IP literals, IPv4 and IPv6.
-   Block loopback, private, link-local, multicast, reserved, and
    metadata-service addresses.
-   Consider DNS rebinding and re-check resolved destinations where
    practical.
-   Revalidate each redirect, pagination target, browser navigation, and
    any discovered URL the backend might fetch.
-   Do not fetch every extracted link or media URL automatically.
-   Centralize URL policy and share it across HTTP fetch, browser
    navigation, and pagination.
-   Consider network egress restrictions as defense in depth.

Enforce: - connection/read/overall deadlines - redirect count -
response-size limit while streaming, not after full buffering -
decompressed-size limit - page, interaction, DOM, text, and
media-candidate caps - concurrency and per-host request-rate limits

Handle compressed responses safely. Avoid downloading media binaries
unless required. Config variables must actually enforce the named
limits. Preserve meaningful HTTP statuses. Do not bypass a 403, CAPTCHA,
or anti-bot challenge; explain that an authorized API or permission may
be needed.

# 13. P0 --- Frontend security

All scraped content is untrusted input.

-   Do not interpolate title, language, metadata, URLs, error messages,
    or content into `innerHTML`.
-   Prefer `textContent`, safe element creation, and explicit attribute
    assignment.
-   If rich HTML display is required, sanitize it with a reputable
    sanitizer and narrow allowlist.
-   Never execute scripts from scraped pages.
-   Validate URL schemes before creating clickable links.
-   Use safe target/rel behavior for external links.
-   Do not render raw error strings as HTML.
-   Validate values used in CSS classes, selectors, and attributes.

UI requirements: - Handle missing optional fields without crashing. -
Distinguish zero, not inspected, failed, blocked, and unresolved. - Show
partial results when some valid data exists. - Show real
strategy/diagnostics without implying rendering guarantees extraction. -
Handle clipboard/download errors visibly. - Revoke object URLs after
downloads where appropriate. - Keep accessibility and
loading/timeout/error states clear.

# 14. P0 --- Deterministic tests and CI

Check `.gitignore` for broad patterns that accidentally ignore `tests/`,
`test_*.py`, or `*_test.py`. Fix this if it prevents tests from being
committed. Verify the tests are actually present in the repository.

Use local HTML fixtures and a small local test server. Do not rely on
commercial live websites in CI.

## Required test groups

**Unit tests:** URL validation/normalization; strategy scoring;
metadata; main content; section boundaries;
tables/lists/links/images/video/audio/documents; repeated records;
fingerprints/deduplication; field-level merge; count recomputation;
challenge detection; schema validation.

**Integration tests:** static request; static-to-browser fallback;
browser augmentation; browser failure preserving static data; three-page
extraction/merge; scroll/load-more revealing new records; challenge
response; redirect safety; malicious content rendered safely.

**Invariants:** - Browser launch alone never means extraction success. -
Strategy reflects data that actually contributed. - Empty browser output
never erases useful static content. - Counts equal final unique items
represented in the response. - Every claimed extracted page has a
page-level extraction record. - Every followed redirect/navigation
target is validated. - Scraped content is never executed as frontend
HTML. - Repeated runs on a fixture are stable. - No site-specific branch
is needed to pass generic fixtures.

Add CI to install dependencies and run tests. Install Playwright
browsers correctly if needed. Keep tests deterministic and reasonably
fast. CI must not depend on Amazon/Foxtale/Wikipedia being reachable.

# 15. P1 --- Metadata, diagnostics, and configuration

Where available, parse Open Graph, Twitter cards, JSON-LD,
microdata/RDFa, canonical URL, language, description, author/date, and
image/video metadata. Parse malformed JSON defensively; never execute
metadata scripts; treat metadata as evidence, not guaranteed truth.
Deduplicate metadata-derived records against DOM-derived records.

Add structured diagnostics for: - static status, duration, and response
bytes - static quality score and reasons - browser
attempted/launched/navigation status - rendered DOM size and extraction
duration - strategy decision - tables/media/cards found by source -
interactions discovered/attempted/content-changed - pages
discovered/visited/extracted/merged - duplicates removed -
challenge/denial signals - time/size/resource-limit events

Do not log cookies, tokens, full page contents, or personal data by
default. Redact sensitive values.

Audit each config setting: either wire it in and test it, or remove it
and update documentation. Do not leave decorative limits that are never
enforced.

Capabilities are supported only when tests prove them. For example, do
not claim tabs are supported merely because the code attempts tab
clicks; do not claim pagination extraction if pages are only visited.

# 16. P2 --- Documentation

Update README after the behavior is implemented. Document: - supported
extraction types - static-first and browser-fallback strategy -
strategy/status meanings - backend/frontend startup - timeout/resource
configuration - Playwright setup - test commands - examples of success,
partial, blocked, and failure responses - limitations: authentication,
CAPTCHA, access restrictions, opaque media players, dynamic state, and
content unavailable to public clients - troubleshooting for zero
tables/videos and zero interaction changes

Remove stale claims such as guaranteed pagination depth unless verified.

# 17. Required implementation workflow

## Phase A: Baseline

Inspect files and call paths; run existing tests/build/lint; record
failures and API compatibility constraints.

## Phase B: P0 foundation

Fix strategy and status semantics; merge logic and counts; global
table/media extraction; generic repeated records; browser interaction
and pagination accounting; URL policy/limits; frontend XSS; fixtures and
CI.

## Phase C: Verify P0

Run all tests. Add a regression test for every discovered bug. Verify
static article + lazy images remains static when rendering adds nothing;
JS-only fixture is extracted; tables/videos work in fixtures;
interactions/pages report actual results; static data survives browser
failure; unsafe navigation is blocked.

## Phase D: P1

Improve records, metadata, media, logging, quality diagnostics, and
error recovery with tests.

## Phase E: P2 and release readiness

Update docs/capabilities; run full tests and frontend checks; review
security and errors; produce the final report.

Make small, coherent changes. Do not rewrite unrelated modules. Add
tests alongside fixes. If an approach fails, inspect the cause rather
than layering brittle selectors. Never report "fixed" without test
evidence.

# 18. Acceptance checklist

The agent must provide evidence for each item.

## Strategy/outcomes

-   [ ] Complete static content stays `static` if rendering adds
    nothing.
-   [ ] JavaScript-only fixture becomes `js` when rendered content is
    extracted.
-   [ ] Complementary sources become `static+js`.
-   [ ] Navigation success alone never means extraction success.
-   [ ] Challenge, denial, empty, partial, and failed outcomes are
    distinct.

## Extraction

-   [ ] Page-wide tables work outside main content.
-   [ ] Links, lists, images, videos, audio, and documents have tested
    paths.
-   [ ] Lazy/responsive images are recognized.
-   [ ] Video detection distinguishes direct URL, embed, metadata-only,
    and unresolved.
-   [ ] Generic repeated cards work without domain-specific selectors.
-   [ ] Nested sections avoid duplicate content.
-   [ ] Final counts match final deduplicated data.

## Browser/pagination

-   [ ] Scrolls are bounded and stop for a documented reason.
-   [ ] Attempted/executed/content-changed/new-item metrics are
    separate.
-   [ ] Unsafe/high-impact actions are skipped.
-   [ ] Every claimed pagination page is independently extracted.
-   [ ] Redirects and final navigation URLs are validated.
-   [ ] Repeated-page loops terminate safely.

## Safety/release

-   [ ] SSRF checks cover initial URL, redirects, pagination, and
    browser navigation.
-   [ ] Time/size/DOM/page/runtime/concurrency limits are enforced.
-   [ ] Access challenges are detected and not bypassed.
-   [ ] Frontend handles untrusted content safely.
-   [ ] Errors are useful without exposing secrets.
-   [ ] Tests are not accidentally ignored; CI runs deterministic
    fixtures.
-   [ ] README and capabilities match tested behavior.

# 19. GOLDEN PROMPT --- paste into your coding agent

You are the senior engineer responsible for completing the existing
Universal Web Scraper at
`https://github.com/SURAJMUDRAKOLA/Universal-Web-Scraper`. Read this
entire blueprint first. Inspect the current repository and verify every
prior finding against the actual code before editing.

Your mission is to make this scraper robust across static HTML,
JavaScript-rendered apps, articles, product/listing grids, tables, media
embeds, lazy-loaded content, and pagination. Build general, adaptive
capabilities. Do not hardcode Amazon, Foxtale, Wikipedia, or any other
site.

**Work in strict priority order.**

**P0:** (1) Correct strategy classification so lazy images and other
soft signals alone do not force browser rendering; (2) separate
request/navigation success from extraction success; (3) preserve static
results and implement field-level static/rendered merging with final
count recomputation; (4) add dedicated page-wide extraction for tables,
links, lists, images, videos, audio, documents, metadata, and generic
repeated records; (5) make scroll/click/tab/pagination bounded,
observable, safe, and accurately reported; (6) validate initial URLs,
redirects, pagination and browser navigation and enforce resource
limits; (7) eliminate frontend XSS risks; (8) add deterministic
fixtures, regression tests, and CI.

**P1:** Improve main-content scoring, section boundaries, repeated
records, lazy/responsive media, frame-aware embed detection, structured
metadata, deduplication, diagnostics, logging, timeout handling, and
resource cleanup.

**P2:** Update README, API examples, capability declarations,
troubleshooting, and developer workflow so every advertised capability
matches verified behavior.

Engineering rules: - Inspect first; produce a concise baseline audit
tied to actual files/functions. - Reuse the existing stack and preserve
API compatibility where reasonable. - Make small, reviewable changes and
add regression tests alongside fixes. - Never fabricate data or silently
swallow important errors. - Never lose useful static content because
browser extraction failed. - Never equate Playwright launch/navigation
with successful extraction. - `static` means static extraction
contributed usable data; `js` means rendered extraction supplied usable
data because static was insufficient; `static+js` means both contributed
distinct meaningful data. - Do not report a table, video, record, page,
or interaction as extracted unless it was actually found and included. -
Distinguish attempted actions, successful execution, observed content
change, and newly extracted items. - Extract every pagination page
independently before claiming it was scraped. - Detect and report
CAPTCHA, anti-bot challenges, authentication walls, and denials. Do not
bypass them or use stealth evasion, challenge solving, or proxy
rotation. - Treat all scraped content as untrusted. Do not inject it
directly into `innerHTML` or execute it. - Test with deterministic local
fixtures, not only live websites. - Check `.gitignore` so tests and CI
can be committed. - Enforce or remove unused config. - Align
documentation/capabilities with verified behavior. - If blocked by
access rights, credentials, deployment permissions, or restrictions,
document the exact blocker and finish all work that can be tested
offline.

Do not stop after a plan. Implement, run tests, fix regressions, and
verify the acceptance checklist. Do not claim perfection or full
functionality without evidence. End with files changed, fixes, exact
test commands/results, verified features, known limitations, and manual
deployment steps. If you lack write access, say so and provide exact
patches or a branch/PR workflow rather than pretending to commit
changes.

**First action:** audit the current repository, identify P0/P1/P2 issues
with file/function evidence, then fix P0 one issue at a time and test
each fix before proceeding.
