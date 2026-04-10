"""
Debug script - fetch rocketpunch and save raw HTML for analysis
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scrapling.fetchers import Fetcher

print("[1] Fetching rocketpunch /jobs ...")
page = Fetcher.get("https://www.rocketpunch.com/jobs?page=1&order=recent", stealthy_headers=True)

print(f"Status: {page.status}")
body = str(page.body)
print(f"HTML length: {len(body)}")

# Save full HTML
with open("debug_page.html", "w", encoding="utf-8") as f:
    f.write(body if isinstance(body, str) else body.decode("utf-8", errors="ignore"))
print("Saved: debug_page.html")

# Quick analysis
print("\n--- Quick Analysis ---")
print(f"Title: {page.css('title::text').getall()}")

# Try various selectors
selectors = [
    ("a[href*='/jobs/']", "job links"),
    ("a[href*='/companies/']", "company links"),
    ("[class*='job']", "job class elements"),
    ("[class*='Job']", "Job class elements"),
    ("[class*='card']", "card elements"),
    ("[class*='Card']", "Card elements"),
    ("[class*='list']", "list elements"),
    ("[class*='item']", "item elements"),
    ("[class*='posting']", "posting elements"),
    ("article", "article tags"),
    ("h2", "h2 tags"),
    ("h3", "h3 tags"),
]

for sel, desc in selectors:
    try:
        els = page.css(sel)
        if els:
            print(f"  {desc} ({sel}): {len(els)} found")
            if len(els) <= 3:
                for el in els:
                    text = " ".join(el.css("::text").getall()).strip()[:80]
                    print(f"    -> {text}")
    except Exception as e:
        pass

# Show all links
print("\n--- All links containing 'job' ---")
for a in page.css("a"):
    href = a.attrib.get("href", "")
    if "job" in href.lower():
        text = " ".join(a.css("::text").getall()).strip()[:60]
        print(f"  {href} | {text}")

print("\nDone! Open debug_page.html to inspect full HTML.")
