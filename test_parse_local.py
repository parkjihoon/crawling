"""
로컬 HTML 파일을 대상으로 parse_list를 테스트한다.

사용법:
    python test_parse_local.py [html_file_path]

기본값: debug_page.html (debug_rocketpunch.cmd로 저장한 파일)
"""

import sys
import os
import re
import json
from pathlib import Path

# 프로젝트 루트를 PYTHONPATH에 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def parse_with_regex(html: str) -> list[dict]:
    """
    Scrapling 없이 순수 regex로 파싱한다.
    로컬 테스트에서 Scrapling 의존성 없이 셀렉터 검증 가능.
    """
    items = []

    # data-index 기준으로 카드 분할
    card_pattern = r'data-index="(\d+)"(.*?)(?=data-index="|$)'
    for match in re.finditer(card_pattern, html, re.DOTALL):
        idx = match.group(1)
        card_html = match.group(2)

        # 회사명/카테고리: textStyle_Body.BodyS + secondary + lc_1
        body_s_matches = re.findall(
            r'textStyle_Body\.BodyS[^"]*c_foregrounds\.neutral\.secondary[^"]*lc_1">'
            r"(.*?)</p>",
            card_html,
        )
        company = body_s_matches[0] if body_s_matches else ""
        category = body_s_matches[1] if len(body_s_matches) >= 2 else ""

        # 제목: textStyle_Body.BodyM_Bold + primary
        title_match = re.search(
            r'textStyle_Body\.BodyM_Bold[^"]*c_foregrounds\.neutral\.primary">'
            r"(.*?)</p>",
            card_html,
        )
        title = title_match.group(1) if title_match else ""

        if not title:
            continue

        # company ID from image
        company_id = ""
        id_match = re.search(
            r"image\.rocketpunch\.com/company/(\d+)/", card_html
        )
        if id_match:
            company_id = id_match.group(1)

        # company logo URL
        logo_match = re.search(
            r'src="(https://www\.rocketpunch\.com/_next/image\?url=[^"]+)"',
            card_html,
        )
        logo_url = logo_match.group(1) if logo_match else ""

        # match info (check vs x icons)
        headers = re.findall(r'ta_center">(.*?)</p>', card_html)
        # filter out non-match headers (keep only known ones)
        match_headers = [h for h in headers if h in ("직군", "숙련도", "규모", "근무 방식")]
        checks = len(re.findall(r'#check-thick-outline', card_html))
        x_marks = len(re.findall(r'#x-circle-outline', card_html))

        match_info = {}
        for i, h in enumerate(match_headers):
            match_info[h] = i < checks  # first N are checks, rest are X

        posting_id = f"rp-{company_id}-{idx}" if company_id else f"rp-list-{idx}"

        items.append({
            "posting_id": posting_id,
            "data_index": idx,
            "title": title.strip(),
            "company_name": company.strip(),
            "category": category.strip(),
            "company_id": company_id,
            "company_logo_url": logo_url[:80] + "..." if len(logo_url) > 80 else logo_url,
            "match_info": match_info,
        })

    return items


def main():
    html_path = sys.argv[1] if len(sys.argv) > 1 else "debug_page.html"

    if not Path(html_path).exists():
        print(f"ERROR: File not found: {html_path}")
        print("Run debug_rocketpunch.cmd first to save the HTML,")
        print("or provide a path to the saved HTML file.")
        sys.exit(1)

    print(f"Reading: {html_path}")
    html = Path(html_path).read_text(encoding="utf-8")
    print(f"HTML size: {len(html):,} bytes")

    print("\n" + "=" * 60)
    print("  Parsing job listings (regex mode)")
    print("=" * 60)

    items = parse_with_regex(html)
    print(f"\nFound {len(items)} job postings:\n")

    for item in items:
        print(f"[{item['data_index']}] {item['company_name']} | {item['title']}")
        print(f"     Category: {item['category']}")
        print(f"     Company ID: {item['company_id'] or 'N/A'}")
        if item['match_info']:
            match_str = ", ".join(
                f"{k}: {'O' if v else 'X'}" for k, v in item['match_info'].items()
            )
            print(f"     Match: {match_str}")
        print()

    # JSON 출력
    output_path = Path(html_path).stem + "_parsed.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"Saved parsed data to: {output_path}")

    # 통계
    print("\n" + "=" * 60)
    print("  Statistics")
    print("=" * 60)
    print(f"  Total postings: {len(items)}")
    companies_with_id = sum(1 for i in items if i['company_id'])
    print(f"  With company ID: {companies_with_id}")
    print(f"  Without company ID: {len(items) - companies_with_id}")
    print(f"  (Company ID comes from image URL pattern company/{{id}}/)")
    print()

    # Scrapling 테스트 (선택적)
    try:
        from scrapling.parser import Adaptor
        print("=" * 60)
        print("  Scrapling CSS selector test")
        print("=" * 60)

        page = Adaptor(html, url="https://www.rocketpunch.com/jobs")

        # Test selectors
        selectors = {
            "div[data-index]": "Job cards",
            'p[class*="BodyS"]': "BodyS paragraphs",
            'p[class*="BodyM_Bold"]': "BodyM_Bold paragraphs",
            'img[alt="image"]': "Company logos",
            'use[href="#check-thick-outline"]': "Check icons",
            'use[href="#x-circle-outline"]': "X icons",
            'p[class*="ta_center"]': "Center-aligned text",
        }

        for sel, desc in selectors.items():
            try:
                els = page.css(sel)
                print(f"  {desc} ({sel}): {len(els)} found")
            except Exception as e:
                print(f"  {desc} ({sel}): ERROR - {e}")

        # Full parse test with Scrapling
        print("\n  Parsing with Scrapling selectors...")
        cards = page.css("div[data-index]")
        for card in cards[:3]:
            idx = card.attrib.get("data-index", "?")
            body_s = card.css('p[class*="BodyS"]')
            body_m = card.css('p[class*="BodyM_Bold"]')
            company = " ".join(body_s[0].css("::text").getall()).strip() if body_s else "?"
            title = " ".join(body_m[0].css("::text").getall()).strip() if body_m else "?"
            print(f"  [{idx}] {company} | {title}")

        print(f"\n  Scrapling parsed all {len(cards)} cards successfully!")

    except ImportError:
        print("\n(Scrapling not installed - CSS selector test skipped)")
        print("Install: pip install scrapling")


if __name__ == "__main__":
    main()
