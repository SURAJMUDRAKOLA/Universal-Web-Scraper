"""Debug: primary root selection on Wikipedia."""
import asyncio, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from app.scraper.static import (
    fetch_page, _extract_components, _clean, _primary_root, _build_sections, _score,
    _CONTENT_SELECTORS
)
from bs4 import BeautifulSoup

async def main():
    url = 'https://en.wikipedia.org/wiki/Artificial_intelligence'
    source = await fetch_page(url)

    soup = BeautifulSoup(source.html, 'lxml')
    _extract_components(soup, url)
    _clean(soup)

    print(f"After clean: h2={len(soup.find_all('h2'))} h3={len(soup.find_all('h3'))}")

    # Check specific content selectors
    for sel in _CONTENT_SELECTORS:
        try:
            nodes = soup.select(sel)
            if nodes:
                n = nodes[0]
                print(f"  {sel}: found <{n.name}> id='{n.get('id','')}' h2={len(n.find_all('h2'))} score={_score(n):.0f}")
        except Exception as e:
            print(f"  {sel}: ERROR {e}")

    # Top scored candidates
    candidates = [
        node for sel in _CONTENT_SELECTORS
        for node in soup.select(sel)
    ] + list(soup.find_all(['div', 'section'], limit=120))
    unique = list({id(node): node for node in candidates if node}.values())
    scored = [(n, _score(n)) for n in unique]
    scored.sort(key=lambda x: -x[1])

    print(f"\nTop 5 candidates by score:")
    for n, score in scored[:5]:
        print(f"  score={score:.0f} <{n.name}> id='{n.get('id','')}' class='{' '.join(n.get('class',[]))[:40]}' h2={len(n.find_all('h2'))}")

    root = _primary_root(soup)
    print(f"\nSelected root: <{root.name}> id='{root.get('id','')}' class='{' '.join(root.get('class',[]))[:50]}'")
    print(f"  h2 in root: {len(root.find_all('h2'))}")
    print(f"  h3 in root: {len(root.find_all('h3'))}")

    sections = _build_sections(root, url)
    print(f"\nSections: {len(sections)}")
    for s in sections[:5]:
        print(f"  [H{s.get('level')}] {s.get('label','')[:50]}")

asyncio.run(main())
