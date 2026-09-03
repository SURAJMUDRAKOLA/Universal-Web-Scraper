"""Debug: what root is selected and are headings found."""
import asyncio, sys, io, time, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from app.scraper.static import fetch_page, _extract_components, _clean, _primary_root, _build_sections, _score
from bs4 import BeautifulSoup

async def main():
    url = 'https://en.wikipedia.org/wiki/Artificial_intelligence'
    source = await fetch_page(url)
    print(f'HTML len: {len(source.html)}')

    soup = BeautifulSoup(source.html, 'lxml')
    print(f'Before extract_components: h2={len(soup.find_all("h2"))} h3={len(soup.find_all("h3"))}')

    # Check if mw-content-text exists
    mw = soup.find(id='mw-content-text')
    if mw:
        print(f'mw-content-text found, h2 inside: {len(mw.find_all("h2"))}')
    else:
        print('mw-content-text NOT found')

    # Run component extraction
    comps = _extract_components(soup, url)
    print(f'After extract_components: h2={len(soup.find_all("h2"))} h3={len(soup.find_all("h3"))}')
    print(f'  lang_sel: {len(comps.get("languageSelector", []))} groups')

    mw2 = soup.find(id='mw-content-text')
    if mw2:
        print(f'mw-content-text still there after components, h2 inside: {len(mw2.find_all("h2"))}')
    else:
        print('mw-content-text GONE after components!')

    _clean(soup)
    print(f'After clean: h2={len(soup.find_all("h2"))} h3={len(soup.find_all("h3"))}')

    root = _primary_root(soup)
    print(f'\nPrimary root: <{root.name}> id="{root.get("id","")}" class="{" ".join(root.get("class", []))[:50]}"')
    print(f'  h2 in root: {len(root.find_all("h2"))}')
    print(f'  h3 in root: {len(root.find_all("h3"))}')
    print(f'  score: {_score(root):.1f}')

    sections = _build_sections(root, url)
    print(f'\nSections built: {len(sections)}')
    for s in sections[:5]:
        print(f'  [H{s.get("level")}] {s.get("label","")[:50]}')

asyncio.run(main())
