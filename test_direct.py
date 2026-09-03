"""Direct extraction test — run with venv from project root."""
import asyncio, sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from app.scraper.static import fetch_page, extract_page, analyze_static_quality

async def main():
    url = 'https://en.wikipedia.org/wiki/Artificial_intelligence'
    print('Fetching...')
    t0 = time.time()
    source = await fetch_page(url)
    elapsed = round((time.time()-t0)*1000)
    print(f'Fetch done in {elapsed}ms')
    print(f'  status={source.status_code}')
    print(f'  error={source.error}')
    print(f'  html_len={len(source.html)}')
    print(f'  content_type={source.content_type}')

    if not source.html:
        print('FAIL: No HTML returned!')
        return

    html = source.html
    print(f'  <h2 count: {html.lower().count("<h2")}')
    print(f'  <h3 count: {html.lower().count("<h3")}')
    print(f'  interlanguage count: {html.lower().count("interlanguage")}')
    print(f'  <p count: {html.lower().count("<p ")}')

    print('\nExtracting...')
    t1 = time.time()
    result = extract_page(source, strategy='static')
    print(f'Extraction done in {round((time.time()-t1)*1000)}ms')

    sections = result.get('content', {}).get('sections', [])
    errors = result.get('errors', [])
    comps = result.get('components', {})
    stats = result.get('statistics', {})
    quality = analyze_static_quality(source, result)

    print(f'  sections: {len(sections)}')
    print(f'  text_chars: {stats.get("textCharacters")}')
    print(f'  quality: {quality}')
    print(f'  lang_sel: {len(comps.get("languageSelector", []))} groups')
    print(f'  nav: {len(comps.get("navigation", []))} groups')

    if errors:
        print(f'  errors: {errors}')

    print('\nFirst 8 sections:')
    for s in sections[:8]:
        text_len = len(s.get('content', {}).get('text', ''))
        print(f'  [H{s.get("level")}] depth={s.get("depth")} {s.get("label","")[:50]} ({text_len} chars)')

asyncio.run(main())
