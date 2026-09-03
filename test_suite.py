import urllib.request
import urllib.error
import json
import sys

def test_url(url, expected_checks, label):
    print(f"=== Testing {label}: {url} ===")
    req = urllib.request.Request(
        "http://localhost:8000/scrape",
        data=json.dumps({"url": url}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            code = r.status
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        code = e.code
        data = json.loads(e.read().decode("utf-8"))

    res = data.get("result", {})
    meta = res.get("meta", {})
    sections = res.get("sections", [])
    interactions = res.get("interactions", {})
    errors = res.get("errors", [])

    print(f"Status code: {code}")
    print(f"Title: {meta.get('title', '')[:80]}")
    print(f"Sections count: {len(sections)}")
    if sections:
        s0 = sections[0]
        print(f"First section: id={s0.get('id')} type={s0.get('type')} label={s0.get('label', '')[:40]} text_len={len(s0.get('content', {}).get('text', ''))}")
    print(f"Interactions: pages={len(interactions.get('pages', []))} ({interactions.get('pages', [])[:3]}), clicks={len(interactions.get('clicks', []))}, scrolls={interactions.get('scrolls', 0)}")
    print(f"Errors: {errors}")

    passed = True
    for check_name, check_fn in expected_checks.items():
        ok = check_fn(code, res)
        print(f"  Check [{check_name}]: {'PASS' if ok else 'FAIL'}")
        if not ok:
            passed = False
    print(f"RESULT for {label}: {'ALL PASSED' if passed else 'FAILED'}\n")
    return passed

print("Starting Verification Suite...\n")

# Test 1: Wikipedia
ok1 = test_url(
    "https://en.wikipedia.org/wiki/Artificial_intelligence",
    {
        "200 OK": lambda code, res: code == 200,
        "title populated": lambda code, res: bool(res.get("meta", {}).get("title")),
        "has sections with text": lambda code, res: any(len(s.get("content", {}).get("text", "")) > 0 for s in res.get("sections", [])),
        "no errors": lambda code, res: len(res.get("errors", [])) == 0,
        "static mode (1 page, 0 clicks)": lambda code, res: len(res.get("interactions", {}).get("pages", [])) == 1 and len(res.get("interactions", {}).get("clicks", [])) == 0
    },
    "1. Wikipedia AI"
)

# Test 2: MDN
ok2 = test_url(
    "https://developer.mozilla.org/en-US/docs/Web/JavaScript",
    {
        "200 OK": lambda code, res: code == 200,
        "title populated": lambda code, res: bool(res.get("meta", {}).get("title")),
        "description populated": lambda code, res: bool(res.get("meta", {}).get("description")),
        "has sections with text": lambda code, res: any(len(s.get("content", {}).get("text", "")) > 0 for s in res.get("sections", [])),
        "no errors": lambda code, res: len(res.get("errors", [])) == 0,
        "static mode (1 page, 0 clicks)": lambda code, res: len(res.get("interactions", {}).get("pages", [])) == 1 and len(res.get("interactions", {}).get("clicks", [])) == 0
    },
    "2. MDN JavaScript"
)

# Test 3: Hacker News
ok3 = test_url(
    "https://news.ycombinator.com/",
    {
        "200 OK": lambda code, res: code == 200,
        "pages >= 3": lambda code, res: len(res.get("interactions", {}).get("pages", [])) >= 3,
        "clicks >= 1": lambda code, res: len(res.get("interactions", {}).get("clicks", [])) >= 1,
        "has sections with text": lambda code, res: any(len(s.get("content", {}).get("text", "")) > 0 for s in res.get("sections", []))
    },
    "3. Hacker News"
)

# Test 4: Vercel
ok4 = test_url(
    "https://vercel.com/",
    {
        "200 OK": lambda code, res: code == 200,
        "scrolls > 0": lambda code, res: res.get("interactions", {}).get("scrolls", 0) > 0,
        "has sections with text": lambda code, res: any(len(s.get("content", {}).get("text", "")) > 0 for s in res.get("sections", []))
    },
    "4. Vercel"
)

# Test 5: Validation errors
ok_val1 = test_url(
    "ftp://example.com/file",
    {
        "400 Bad Request": lambda code, res: code == 400,
        "Validation error message": lambda code, res: any(e.get("phase") == "validation" for e in res.get("errors", []))
    },
    "Validation Non-HTTP"
)

ok_val2 = test_url(
    "not_a_valid_url",
    {
        "400 Bad Request": lambda code, res: code == 400,
        "Validation error message": lambda code, res: any(e.get("phase") == "validation" for e in res.get("errors", []))
    },
    "Validation Malformed"
)

print(f"SUMMARY: Wikipedia={ok1}, MDN={ok2}, HN={ok3}, Vercel={ok4}, Val1={ok_val1}, Val2={ok_val2}")
if all([ok1, ok2, ok3, ok4, ok_val1, ok_val2]):
    print("ALL TESTS PASSED SUCCESSFULLY!")
    sys.exit(0)
else:
    print("SOME TESTS FAILED.")
    sys.exit(1)
