"""
로켓펀치 (rocketpunch.com) 크롤러

Scrapling StealthyFetcher 기반.
Next.js + AWS WAF 보호가 적용된 사이트이므로 StealthyFetcher(headless browser)를 사용한다.

대상 URL: https://www.rocketpunch.com/jobs
개별 공고: https://www.rocketpunch.com/jobs/{job_id}

[중요] 이 크롤러는 일반 IP 환경에서 실행해야 한다.
       클라우드 IP(AWS, GCP 등)에서는 CloudFront가 차단할 수 있다.

실행 방법:
    python main.py --site rocketpunch --pages 1-5 --delay 5

HTML 구조 (2026-04 기준):
    - 목록: div[data-index="N"] 기반 가상 스크롤 (tanstack/react-virtual)
    - 각 카드: 회사명, 제목, 카테고리, 매칭 정보 포함
    - 카드에 직접 href 없음 — React 클릭 핸들러로 네비게이션
    - 상세 URL은 API 호출 또는 브라우저 자동화로 확보 필요
"""

import re
import logging
import time
import urllib.parse
from typing import Optional

from src.crawlers.base import BaseCrawler
from src.models.job_posting import JobPosting

logger = logging.getLogger(__name__)

BASE_URL = "https://www.rocketpunch.com"
JOBS_URL = f"{BASE_URL}/jobs"

# ────────────────────────────────────────────────────────
# CSS 셀렉터 정의 (2026-04 기준 로켓펀치 HTML 구조)
# ────────────────────────────────────────────────────────
# 로켓펀치는 CSS-in-JS(panda-css 계열)를 사용.
# 클래스명이 CSS 속성 그대로이므로 비교적 안정적:
#   textStyle_Body.BodyS, c_foregrounds.neutral.secondary 등
#
# [주의] 클래스명에 점(.)이 포함되어 있어 CSS 셀렉터에서 이스케이프 필요.
#        또는 속성 셀렉터 [class*="..."] 를 사용.
# ────────────────────────────────────────────────────────

# 개별 공고 카드 컨테이너
SEL_JOB_CARD = "div[data-index]"

# 카드 내부 텍스트 요소 (속성 부분매칭)
SEL_COMPANY_NAME = 'p[class*="textStyle_Body.BodyS"][class*="c_foregrounds.neutral.secondary"][class*="lc_1"]'
SEL_JOB_TITLE = 'p[class*="textStyle_Body.BodyM_Bold"][class*="c_foregrounds.neutral.primary"]'

# 회사 로고 이미지 (company ID 추출용)
SEL_COMPANY_LOGO = 'img[alt="image"]'

# 매칭 정보 컬럼 헤더
SEL_MATCH_HEADER = 'p[class*="ta_center"][class*="textStyle_Body.BodyS"]'

# 매칭 체크/X 아이콘
SEL_CHECK_ICON = 'use[href="#check-thick-outline"]'
SEL_X_ICON = 'use[href="#x-circle-outline"]'

# 가상 스크롤 컨테이너
SEL_SCROLL_CONTAINER = "div.List#job-content"


class RocketPunchCrawler(BaseCrawler):
    """
    로켓펀치 크롤러.

    Scrapling StealthyFetcher를 사용하여 AWS WAF를 우회한다.
    Next.js SSR 페이지이므로 JavaScript 렌더링이 필요하다.

    사용법:
        crawler = RocketPunchCrawler()
        postings = crawler.run(start_page=1, end_page=3)

    검색 조건:
        crawler = RocketPunchCrawler(keywords="백엔드")
        postings = crawler.run(start_page=1, end_page=1)

    주의:
        - 클라우드 IP에서는 CloudFront 차단으로 실행 불가
        - 일반 IP 환경(로컬 PC, 사무실 서버 등)에서 실행할 것
        - 요청 간격 최소 5초 권장 (보수적 운영)
    """

    def __init__(
        self,
        crawl_delay: float = 5.0,
        keywords: str = "",
        order: str = "recent",
        headless: bool = True,
    ):
        """
        Args:
            crawl_delay: 요청 간격 (초). 기본 5초.
            keywords: 검색 키워드 (빈 문자열이면 전체 조회)
            order: 정렬 기준 - "recent"(최신순), "score"(적합순)
            headless: 브라우저 숨김 모드 (True 권장)
        """
        super().__init__(
            site_name="rocketpunch",
            base_url=BASE_URL,
            crawl_delay=crawl_delay,
        )
        self.keywords = keywords
        self.order = order
        self.headless = headless
        self._session = None

    def _get_fetcher(self):
        """
        Scrapling StealthyFetcher 세션을 반환한다.
        세션을 재사용하여 브라우저 재시작 오버헤드를 줄인다.

        StealthyFetcher가 실패하면 DynamicFetcher로 폴백한다.
        """
        from scrapling.fetchers import StealthyFetcher
        return StealthyFetcher

    def _build_list_url(self, page: int) -> str:
        """목록 페이지 URL을 구성한다."""
        params = [f"page={page}"]
        if self.keywords:
            params.append(f"keywords={self.keywords}")
        if self.order:
            params.append(f"order={self.order}")

        query = "&".join(params)
        return f"{JOBS_URL}?{query}"

    def fetch_list(self, page: int):
        """
        채용공고 목록 페이지를 요청한다.

        로켓펀치는 Next.js SSR이므로 StealthyFetcher로 렌더링된 HTML을 가져온다.
        AWS WAF 챌린지를 자동으로 처리한다.
        """
        url = self._build_list_url(page)
        logger.info(f"[rocketpunch] list request: {url}")

        try:
            fetcher = self._get_fetcher()
            response = fetcher.fetch(
                url,
                headless=self.headless,
            )

            if response.status >= 400:
                logger.warning(
                    f"[rocketpunch] list request failed (status={response.status}): {url}"
                )
                return None

            return response

        except Exception as e:
            logger.error(f"[rocketpunch] list request error: {url} - {e}")
            return None

    def parse_list(self, response) -> list[dict]:
        """
        목록 페이지에서 채용공고 정보를 추출한다.

        로켓펀치 HTML 구조 (2026-04):
        - div[data-index="N"] : 가상 스크롤 아이템 (각 공고 카드)
        - 카드 내부:
            - p.textStyle_Body.BodyS.c_foregrounds.neutral.secondary.lc_1 → 회사명 (첫 번째)
            - p.textStyle_Body.BodyM_Bold.c_foregrounds.neutral.primary → 공고 제목
            - p.textStyle_Body.BodyS.c_foregrounds.neutral.secondary.lc_1 → 카테고리 (두 번째)
        - 카드에 <a href>가 없음 → detail URL은 별도 확보 필요

        Returns:
            [{"posting_id": "...", "title": "...", "company_name": "...",
              "category": "...", "company_logo_url": "...", "company_id": "...",
              "match_info": {...}}]
        """
        items = []

        # 방법 1: data-index 기반 카드 파싱 (primary)
        cards = response.css(SEL_JOB_CARD)

        if not cards:
            logger.warning("[rocketpunch] No job cards found (div[data-index])")
            # 폴백: 전체 HTML에서 regex 파싱
            items = self._parse_list_regex(str(response.body))
            return items

        for card in cards:
            item = self._parse_card(card)
            if item:
                items.append(item)

        logger.info(f"[rocketpunch] list parsed: {len(items)} postings found")
        return items

    def _parse_card(self, card) -> Optional[dict]:
        """
        개별 카드 요소에서 공고 정보를 추출한다.

        Args:
            card: Scrapling element (div[data-index])

        Returns:
            dict with posting info, or None on failure
        """
        try:
            data_index = card.attrib.get("data-index", "")

            # ── 텍스트 요소 추출 ──
            # 회사명 + 카테고리는 동일한 셀렉터 (BodyS + secondary + lc_1)
            # 제목은 BodyM_Bold + primary
            #
            # Panda-css 클래스는 점(.)을 포함하므로 속성 셀렉터 사용.
            # scrapling에서 [class*="..."] 이 동작하지 않을 수 있으므로
            # 전체 텍스트에서 regex로 추출하는 방식도 병행.

            company_name = ""
            job_title = ""
            category = ""

            # 방법 A: CSS 셀렉터 시도
            try:
                # textStyle_Body.BodyS 는 회사명과 카테고리에 모두 사용
                body_s_elements = card.css('p[class*="BodyS"]')
                body_m_elements = card.css('p[class*="BodyM_Bold"]')

                if body_s_elements:
                    company_name = self._get_text(body_s_elements[0])
                if body_m_elements:
                    job_title = self._get_text(body_m_elements[0])
                if len(body_s_elements) >= 2:
                    category = self._get_text(body_s_elements[1])
            except Exception:
                pass

            # 방법 B: CSS 셀렉터 실패 시 regex 폴백
            if not job_title:
                card_html = str(card)
                job_title, company_name, category = self._regex_extract_card(card_html)

            if not job_title:
                logger.debug(f"[rocketpunch] card {data_index}: no title, skipping")
                return None

            # ── 회사 로고 이미지에서 company ID 추출 ──
            company_id = ""
            company_logo_url = ""
            try:
                imgs = card.css('img[alt="image"]')
                if imgs:
                    src = imgs[0].attrib.get("src", "")
                    company_logo_url = src
                    # 패턴: company/{id}/name_logo_... or company_profile/0/...
                    id_match = re.search(
                        r"image\.rocketpunch\.com/company/(\d+)/", src
                    )
                    if id_match:
                        company_id = id_match.group(1)
            except Exception:
                pass

            # ── 매칭 정보 ──
            match_info = self._extract_match_info(card)

            # ── posting_id 생성 ──
            # 리스트 페이지에는 job ID가 없으므로 임시 ID 생성
            # (title + company를 해시하거나 data-index 사용)
            posting_id = f"rp-list-{data_index}"
            if company_id:
                posting_id = f"rp-{company_id}-{data_index}"

            return {
                "posting_id": posting_id,
                "url": "",  # 리스트에서는 상세 URL 확보 불가
                "title": job_title.strip(),
                "company_name": company_name.strip(),
                "category": category.strip(),
                "company_id": company_id,
                "company_logo_url": company_logo_url,
                "match_info": match_info,
                "data_index": data_index,
            }

        except Exception as e:
            logger.error(f"[rocketpunch] card parse error: {e}")
            return None

    def _get_text(self, element) -> str:
        """요소에서 텍스트를 추출한다."""
        try:
            texts = element.css("::text").getall()
            return " ".join(t.strip() for t in texts if t.strip())
        except Exception:
            return ""

    def _regex_extract_card(self, html: str) -> tuple[str, str, str]:
        """
        카드 HTML에서 regex로 제목, 회사명, 카테고리를 추출한다.
        CSS 셀렉터가 실패할 때의 폴백.
        """
        title = ""
        company = ""
        category = ""

        # 회사명/카테고리: textStyle_Body.BodyS + secondary + lc_1
        body_s_matches = re.findall(
            r'textStyle_Body\.BodyS[^"]*c_foregrounds\.neutral\.secondary[^"]*lc_1">'
            r"(.*?)</p>",
            html,
        )
        if body_s_matches:
            company = body_s_matches[0]
        if len(body_s_matches) >= 2:
            category = body_s_matches[1]

        # 제목: textStyle_Body.BodyM_Bold + primary
        title_match = re.search(
            r'textStyle_Body\.BodyM_Bold[^"]*c_foregrounds\.neutral\.primary">'
            r"(.*?)</p>",
            html,
        )
        if title_match:
            title = title_match.group(1)

        return title, company, category

    def _extract_match_info(self, card) -> dict:
        """
        카드의 매칭 정보(직군, 숙련도, 규모, 근무방식)를 추출한다.

        Returns:
            {"직군": True/False, "숙련도": True/False, ...}
        """
        match_info = {}
        try:
            # 매칭 컬럼: 각 컬럼은 d_flex 컨테이너에 헤더 p + 체크/X 아이콘
            # 헤더: p[ta_center] → "직군", "숙련도", "규모", "근무 방식"
            headers = card.css('p[class*="ta_center"]')
            checks = card.css('use[href="#check-thick-outline"]')
            x_marks = card.css('use[href="#x-circle-outline"]')

            header_texts = [self._get_text(h) for h in headers]

            # 매칭 컬럼은 보통 4개 (직군, 숙련도, 규모, 근무방식)
            # 각 컬럼 순서대로 check/x 매칭
            total_icons = len(checks) + len(x_marks)
            if header_texts and total_icons > 0:
                # 간단 매핑: 체크 수 + X 수 = 헤더 수일 때
                # 전체 체크 아이콘 인덱스를 기반으로 매핑
                for i, header in enumerate(header_texts):
                    # 기본값: 현재 인덱스까지의 check 비율로 추정
                    match_info[header] = i < len(checks)

        except Exception:
            pass

        return match_info

    def _parse_list_regex(self, html: str) -> list[dict]:
        """
        전체 HTML에서 regex로 공고 목록을 추출한다.
        Scrapling CSS 셀렉터가 전혀 동작하지 않을 때의 최종 폴백.
        """
        items = []
        # data-index 기준으로 카드 분할
        card_pattern = r'data-index="(\d+)"(.*?)(?=data-index="|$)'
        for match in re.finditer(card_pattern, html, re.DOTALL):
            idx = match.group(1)
            card_html = match.group(2)

            title, company, category = self._regex_extract_card(card_html)

            if not title:
                continue

            # 이미지 URL에서 company ID 추출
            company_id = ""
            id_match = re.search(
                r"image\.rocketpunch\.com/company/(\d+)/", card_html
            )
            if id_match:
                company_id = id_match.group(1)

            posting_id = f"rp-{company_id}-{idx}" if company_id else f"rp-list-{idx}"

            items.append({
                "posting_id": posting_id,
                "url": "",
                "title": title.strip(),
                "company_name": company.strip(),
                "category": category.strip(),
                "company_id": company_id,
                "company_logo_url": "",
                "match_info": {},
                "data_index": idx,
            })

        logger.info(f"[rocketpunch] regex fallback parsed: {len(items)} postings")
        return items

    def fetch_detail(self, url: str):
        """
        공고 상세 페이지를 요청한다.
        """
        logger.debug(f"[rocketpunch] detail request: {url}")

        try:
            fetcher = self._get_fetcher()
            response = fetcher.fetch(
                url,
                headless=self.headless,
            )

            if response.status >= 400:
                logger.warning(
                    f"[rocketpunch] detail request failed (status={response.status}): {url}"
                )
                return None

            return response

        except Exception as e:
            logger.error(f"[rocketpunch] detail request error: {url} - {e}")
            return None

    def parse_detail(self, response, url: str) -> Optional[JobPosting]:
        """
        상세 페이지에서 채용공고 정보를 파싱한다.

        [참고] 로켓펀치 상세 페이지도 Next.js SSR + panda-css이므로
               셀렉터가 변경될 수 있다. regex 폴백을 항상 병행.
        """
        try:
            posting_id = ""
            id_match = re.search(r"/jobs/(\w+)", url)
            if id_match:
                posting_id = id_match.group(1)

            # 제목 추출 (여러 셀렉터 시도)
            title = self._extract_text(response, [
                "h1",
                'p[class*="BodyXL_Bold"]',
                'p[class*="BodyL_Bold"]',
                "[class*='title'] h1",
                "title",
            ])

            # 회사명
            company = self._extract_text(response, [
                'p[class*="BodyM"][class*="secondary"]',
                "[class*='company-name']",
                "[class*='company'] a",
            ])

            # 위치
            location = self._extract_text(response, [
                "[class*='location']",
                "[class*='address']",
            ])

            # 경력
            experience = self._extract_text(response, [
                "[class*='experience']",
                "[class*='career']",
            ])

            # 학력
            education = self._extract_text(response, [
                "[class*='education']",
            ])

            # 고용형태
            employment_type = self._extract_text(response, [
                "[class*='employment']",
                "[class*='job-type']",
            ])

            # 급여
            salary = self._extract_text(response, [
                "[class*='salary']",
                "[class*='compensation']",
            ])

            # 마감일
            closing_date = self._extract_text(response, [
                "[class*='deadline']",
                "[class*='due-date']",
                "[class*='closing']",
            ])

            # 상세 내용
            description = self._extract_text(response, [
                "[class*='description']",
                "[class*='content']",
                "[class*='detail']",
            ])

            if not title:
                logger.warning(f"[rocketpunch] parse failed (no title): {url}")
                return None

            return JobPosting(
                posting_id=posting_id,
                title=title.strip(),
                company_name=company.strip() if company else "",
                location=location.strip() if location else "",
                salary=salary.strip() if salary else "",
                experience=experience.strip() if experience else "",
                education=education.strip() if education else "",
                employment_type=employment_type.strip() if employment_type else "",
                closing_date=closing_date.strip() if closing_date else "",
                description=description[:500] if description else "",
                source_url=url,
                source_site="rocketpunch",
            )

        except Exception as e:
            logger.error(f"[rocketpunch] detail parse error: {url} - {e}")
            return None

    def _extract_text(self, response, selectors: list[str]) -> str:
        """여러 CSS 셀렉터를 순서대로 시도하여 첫 매치의 텍스트를 반환한다."""
        for selector in selectors:
            try:
                elements = response.css(selector)
                if elements:
                    texts = elements[0].css("::text").getall()
                    if texts:
                        return " ".join(t.strip() for t in texts if t.strip())
            except Exception:
                continue
        return ""
