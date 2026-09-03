"""Debug: find exactly which component extraction step removes mw-content-text."""
import asyncio, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from app.scraper.static import fetch_page, _safe_text, _tag_class_id, _safe_get, _is_language_selector, _has_interlanguage_children
from bs4 import BeautifulSoup

async def main():
    url = 'https://en.wikipedia.org/wiki/Artificial_intelligence'
    source = await fetch_page(url)

    def check_mw(soup, step):
        mw = soup.find(id='mw-content-text')
        h2 = len(soup.find_all('h2'))
        h3 = len(soup.find_all('h3'))
        status = f"{'EXISTS' if mw else 'GONE'}"
        print(f"  [{step}] mw-content-text: {status}, h2={h2}, h3={h3}")

    soup = BeautifulSoup(source.html, 'lxml')
    check_mw(soup, 'start')

    # Step 1: footers
    for tag in list(soup.find_all('footer')):
        tag.decompose()
    check_mw(soup, 'after footers')

    # Step 2: TOC
    toc_tags = []
    for candidate in soup.find_all(['div', 'nav', 'ul', 'ol'], limit=200):
        if not hasattr(candidate, 'get'): continue
        ci = _tag_class_id(candidate)
        if any(kw in ci for kw in ('toc', 'table-of-contents', 'tableofcontents')):
            toc_tags.append(candidate)
    print(f"  TOC candidates found: {len(toc_tags)}")
    for t in toc_tags[:3]:
        print(f"    <{t.name}> id='{t.get('id','')}' class='{' '.join(t.get('class',[]))[:50]}'")
        print(f"    parent: <{t.parent.name if t.parent else 'none'}> id='{t.parent.get('id','') if t.parent else ''}'")
    for tag in toc_tags:
        tag.decompose()
    check_mw(soup, 'after TOC')

    # Step 3: language selector
    wiki_lang = _has_interlanguage_children(soup)
    if wiki_lang:
        print(f"  Wiki lang parent: <{wiki_lang.name}> id='{wiki_lang.get('id','')}' class='{' '.join(wiki_lang.get('class',[]))[:50]}'")
        parent_of_lang = wiki_lang.parent
        print(f"  Parent of wiki_lang: <{parent_of_lang.name if parent_of_lang else 'none'}> id='{parent_of_lang.get('id','') if parent_of_lang else ''}'")
        wiki_lang.decompose()
    check_mw(soup, 'after wiki lang')

    # Keyword-based lang
    for candidate in soup.find_all(True, limit=600):
        if not hasattr(candidate, 'get'): continue
        ci = _tag_class_id(candidate)
        tag_id = _safe_get(candidate, 'id').lower()
        aria_label = _safe_get(candidate, 'aria-label').lower()
        keyword_match = (
            any(kw in ci for kw in ('lang', 'interlanguage', 'language'))
            or 'language' in aria_label
            or 'lang' in tag_id
        )
        if keyword_match and _is_language_selector(candidate):
            print(f"  Keyword lang match: <{candidate.name}> id='{candidate.get('id','')}' class='{' '.join(candidate.get('class',[]))[:50]}'")
            candidate.decompose()
    check_mw(soup, 'after keyword lang')

    # Step 4: nav
    nav_tags = list(soup.find_all('nav'))
    print(f"  Nav candidates: {len(nav_tags)}")
    for n in nav_tags[:3]:
        aria = n.get('aria-label','')
        print(f"    <nav> aria-label='{aria}' id='{n.get('id','')}' class='{' '.join(n.get('class',[]))[:50]}'")
    for tag in nav_tags:
        tag.decompose()
    check_mw(soup, 'after nav')

    # Step 5: sidebars
    sidebar_tags = []
    for candidate in soup.find_all(['div', 'aside', 'section'], limit=200):
        if not hasattr(candidate, 'get'): continue
        ci = _tag_class_id(candidate)
        if 'sidebar' in ci or candidate.name == 'aside':
            sidebar_tags.append(candidate)
    print(f"  Sidebar candidates: {len(sidebar_tags)}")
    for s in sidebar_tags[:3]:
        print(f"    <{s.name}> id='{s.get('id','')}' class='{' '.join(s.get('class',[]))[:50]}'")
    for tag in sidebar_tags:
        tag.decompose()
    check_mw(soup, 'after sidebars')

asyncio.run(main())
