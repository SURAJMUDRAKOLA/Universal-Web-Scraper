"""Debug: which TOC candidates contain h2/h3 elements?"""
import asyncio, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from app.scraper.static import fetch_page, _tag_class_id
from bs4 import BeautifulSoup

async def main():
    url = 'https://en.wikipedia.org/wiki/Artificial_intelligence'
    source = await fetch_page(url)

    soup = BeautifulSoup(source.html, 'lxml')
    print(f"Initial: h2={len(soup.find_all('h2'))} h3={len(soup.find_all('h3'))}")

    # Find TOC candidates
    toc_tags = []
    for candidate in soup.find_all(['div', 'nav', 'ul', 'ol'], limit=200):
        if not hasattr(candidate, 'get'): continue
        ci = _tag_class_id(candidate)
        if any(kw in ci for kw in ('toc', 'table-of-contents', 'tableofcontents')):
            h2_inside = len(candidate.find_all('h2'))
            h3_inside = len(candidate.find_all('h3'))
            if h2_inside + h3_inside > 0:
                print(f"  CONTENT TOC CANDIDATE: <{candidate.name}> id='{candidate.get('id','')}' class='{' '.join(candidate.get('class',[]))[:50]}' h2={h2_inside} h3={h3_inside}")
            toc_tags.append(candidate)

    print(f"\nTotal TOC candidates: {len(toc_tags)}")
    print(f"After finding (no removal yet): h2={len(soup.find_all('h2'))} h3={len(soup.find_all('h3'))}")

    # Decompose them
    for tag in toc_tags:
        try:
            tag.decompose()
        except Exception:
            pass

    print(f"After TOC decompose: h2={len(soup.find_all('h2'))} h3={len(soup.find_all('h3'))}")

    # Check mw-content-text
    mw = soup.find(id='mw-content-text')
    if mw:
        print(f"mw-content-text: EXISTS, h2={len(mw.find_all('h2'))}, h3={len(mw.find_all('h3'))}")
    else:
        print("mw-content-text: GONE")

asyncio.run(main())
