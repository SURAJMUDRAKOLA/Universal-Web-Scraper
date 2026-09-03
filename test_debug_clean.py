"""Debug: what keywords match in Wikipedia HTML."""
import asyncio, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from app.scraper.static import fetch_page, _extract_components, _tag_class_id, _NOISE_SELECTORS
from bs4 import BeautifulSoup

_NOISE_KEYWORDS = ["cookie", "consent", "modal", "popup", "overlay",
    "toast", "newsletter", "subscribe", "gdpr", "banner"]

async def main():
    url = 'https://en.wikipedia.org/wiki/Artificial_intelligence'
    source = await fetch_page(url)

    soup = BeautifulSoup(source.html, 'lxml')
    _extract_components(soup, url)

    # Check before clean step
    print(f"Before clean: h2={len(soup.find_all('h2'))} h3={len(soup.find_all('h3'))}")

    # Step 1: noise selectors
    for selector in _NOISE_SELECTORS:
        for el in soup.select(selector):
            el.decompose()
    print(f"After noise selectors: h2={len(soup.find_all('h2'))} h3={len(soup.find_all('h3'))}")

    # Step 2: keyword check — log matches
    keyword_matches = []
    for tag in list(soup.find_all(True)):
        if not hasattr(tag, 'get'): continue
        ci = _tag_class_id(tag)
        for kw in _NOISE_KEYWORDS:
            if kw in ci:
                keyword_matches.append((kw, tag.name, tag.get('id',''), ' '.join(tag.get('class',[]))[:60]))
                break
    print(f"\nKeyword matches: {len(keyword_matches)}")
    for kw, name, tid, cls in keyword_matches[:10]:
        print(f"  kw='{kw}' <{name}> id='{tid}' class='{cls}'")

    print("\nKeyword counts by keyword:")
    from collections import Counter
    kw_count = Counter(m[0] for m in keyword_matches)
    for kw, cnt in kw_count.most_common():
        print(f"  {kw}: {cnt}")

    # Now actually remove them and check
    for tag in list(soup.find_all(True)):
        if not hasattr(tag, 'get'): continue
        ci = _tag_class_id(tag)
        if any(kw in ci for kw in _NOISE_KEYWORDS):
            tag.decompose()
    print(f"\nAfter keyword removal: h2={len(soup.find_all('h2'))} h3={len(soup.find_all('h3'))}")

    # Step 3: aria-hidden
    for tag in list(soup.find_all(attrs={"aria-hidden": "true"})):
        tag.decompose()
    print(f"After aria-hidden removal: h2={len(soup.find_all('h2'))} h3={len(soup.find_all('h3'))}")

asyncio.run(main())
