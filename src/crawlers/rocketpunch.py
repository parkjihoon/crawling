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
"""

import re
import logging
import time
from typing import Optional

from src.crawlers.base import BaseCrawler
from src.models.job_posting import JobPosting

logger = logging.getLogger(__name__)

BASE_URL = "https://www.rocketpunch.com"
JOBS_URL = f"{BASE_URL}/jobs"

# 로켓펀치 /jobs 페이지 쿼리 파라미터
# 페이지네이션: ?page=1, ?page=2 ...
# 정렬: ?order=recent (최신순), ?order=score (적합순)
# 검색: ?keywords=키워드


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
        logger.info(f"[rocketpunch] 목록 요청: {url}")

        try:
            fetcher = self._get_fetcher()
            response = fetcher.fetch(
                url,
                headless=self.headless,
            )

            if response.status >= 400:
                logger.warning(
                    f"[rocketpunch] 목록 요청 실패 (status={response.status}): {url}"
                )
                return None

            return response

        except Exception as e:
            logger.error(f"[rocketpunch] 목록 요청 에러: {url} - {e}")
            return None

    def parse_list(self, response) -> list[dict]:
        """
        목록 페이지에서 채용공고 정보를 추출한다.

        로켓펀치 공고 URL 패턴: /jobs/{숫자ID} 또는 /jobs/{slug}
        """
        items = []

        # /jobs/{id} 패턴의 링크 추출
        job_links = response.css('a[href*="/jobs/"]')

        for link in job_links:
            href = link.attrib.get("href", "")

            # /jobs 자체, /jobs?page= 등 목록 URL은 제외
            if not href or href.rstrip("/") == "/jobs":
                continue
            if "?" in href and "/jobs/" not in href.split("?")[0]:
                continue

            # /jobs/{id_or_slug} 형태만 추출
            match = re.match(r"^(/jobs/[\w-]+)", href)
            if not match:
                continue

            job_path = match.group(1)
            full_url = BASE_URL + job_path if job_path.startswith("/") else job_path

            # 중복 제거
            if any(item["url"] == full_url for item in items):
                continue

            # 가능한 텍스트 정보 추출
            title = ""
            company = ""
            text_parts = link.css("::text").getall()
            if text_parts:
                title = " ".join(t.strip() for t in text_parts if t.strip())

            # posting_id 추출
            id_match = re.search(r"/jobs/(\d+)", job_path)
            posting_id = id_match.group(1) if id_match else job_path.split("/")[-1]

            items.append({
                "posting_id": posting_id,
                "url": full_url,
                "title": title,
                "company_name": company,
            })

        logger.info(f"[rocketpunch] 목록 파싱: {len(items)}건 추출")
        return items

    def fetch_detail(self, url: str):
        """
        공고 상세 페이지를 요청한다.
        """
        logger.debug(f"[rocketpunch] 상세 요청: {url}")

        try:
            fetcher = self._get_fetcher()
            response = fetcher.fetch(
                url,
                headless=self.headless,
            )

            if response.status >= 400:
                logger.warning(
                    f"[rocketpunch] 상세 요청 실패 (status={response.status}): {url}"
                )
                return None

            return response

        except Exception as e:
            logger.error(f"[rocketpunch] 상세 요청 에러: {url} - {e}")
            return None

    def parse_detail(self, response, url: str) -> Optional[JobPosting]:
        """
        상세 페이지에서 채용공고 정보를 파싱한다.

        로켓펀치 상세 페이지 구조 (CSS 셀렉터):
        - 공고 제목: h1, [class*="title"], .job-title 등
        - 회사명: [class*="company"], .company-name 등
        - 위치, 경력, 학력, 고용형태 등은 상세 정보 섹션에 포함

        [참고] 로켓펀치가 Next.js 기반이라 클래스명이 변경될 수 있다.
               Scrapling의 adaptive 기능을 활용하면 변경에 대응 가능.
        """
        try:
            posting_id = ""
            id_match = re.search(r"/jobs/(\w+)", url)
            if id_match:
                posting_id = id_match.group(1)

            # 제목 추출 (여러 셀렉터 시도)
            title = self._extract_text(response, [
                "h1",
                "[class*='title'] h1",
                "[class*='job-title']",
                "title",
            ])

            # 회사명
            company = self._extract_text(response, [
                "[class*='company-name']",
                "[class*='company'] a",
                "[class*='CompanyName']",
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
                logger.warning(f"[rocketpunch] 파싱 실패 (제목 없음): {url}")
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
            logger.error(f"[rocketpunch] 파싱 에러: {url} - {e}")
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
