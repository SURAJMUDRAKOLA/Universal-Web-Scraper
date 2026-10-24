"""Deterministic regression tests for the scraper's core contract."""
from pathlib import Path
import unittest

from app.scraper.static import PageSource, analyze_static_quality, extract_page, STATIC_COMPLETE
from app.scraper.urls import URLPolicyError, normalize_url


PAGES = Path(__file__).parent / "pages"


def fixture(name: str) -> PageSource:
    url = f"https://example.test/{name}"
    return PageSource(url, url, "text/html", 200, (PAGES / name).read_text(encoding="utf-8"))


class StaticPipelineTests(unittest.TestCase):
    def test_complete_static_article_with_lazy_image_stays_static(self):
        source = fixture("static_article.html")
        result = extract_page(source)
        self.assertEqual(analyze_static_quality(source, result), STATIC_COMPLETE)
        self.assertGreater(result["statistics"]["textCharacters"], 300)

    def test_lazy_media_urls_are_extracted_without_rendering(self):
        result = extract_page(fixture("lazy_images.html"))
        srcs = {image["src"] for image in result["resources"]["images"]}
        self.assertTrue(any("/id/10/" in src for src in srcs))
        self.assertTrue(any("/id/50/" in src for src in srcs))

    def test_global_tables_and_media_are_not_limited_to_sections(self):
        html = """<html><body><main><h1>Article</h1><p>""" + ("content " * 60) + """</p></main>
        <aside><table><caption>Outside main</caption><tr><th>A</th></tr><tr><td>B</td></tr></table>
        <audio src='/clip.mp3'></audio><a href='/report.pdf'>Report</a></aside></body></html>"""
        source = PageSource("https://example.test/a", "https://example.test/a", "text/html", 200, html)
        result = extract_page(source)
        self.assertEqual(len(result["resources"]["tables"]), 1)
        self.assertEqual(result["resources"]["audio"][0]["url"], "https://example.test/clip.mp3")
        self.assertEqual(result["resources"]["documents"][0]["url"], "https://example.test/report.pdf")

    def test_private_destinations_are_rejected(self):
        with self.assertRaises(URLPolicyError):
            normalize_url("http://127.0.0.1:9000/private")


if __name__ == "__main__":
    unittest.main()
