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

동적 기능 (Scrapling page_action / capture_xhr):
    - page_action: Playwright page 객체를 직접 조작 (클릭, 스크롤, URL 캡처)
    - capture_xhr: API 호출 자동 캡처 (regex 패턴 매칭)
    - wait_selector: 특정 요소 로딩 대기 (가상 스크롤 렌더링)

HTML 구조 (2026-04 기준):
    - 목록: div[data-index="N"] 기반 가상 스크롤 (tanstack/react-virtual)
    - 각 카드: 회사명, 제목, 카테고리, 매칭 정보 포함
    - 카드에 직접 href 없음 — React 클릭 핸들러로 네비게이션
    - page_action으로 카드 클릭 → page.url로 상세 URL 캡처
"""

import re
import json
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

# ────────────────────────────────────────────────────────
# Playwright 셀렉터 (page_action 내에서 사용)
# Scrapling CSS와 달리 Playwright 네이티브 셀렉터 문법 사용
# ────────────────────────────────────────────────────────
PW_JOB_CARD = "div[data-index]"
PW_SCROLL_CONTAINER = "#job-content"


class RocketPunchCrawler(BaseCrawler):
    """
    로켓펀치 크롤러.

    Scrapling StealthyFetcher를 사용하여 AWS WAF를 우회한다.
    Next.js SSR 페이지이므로 JavaScript 렌더링이 필요하다.

    Phase 1: 목록 파싱 (page_action 없이, 정적 HTML에서 추출)
    Phase 2: 상세 URL 캡처 (page_action으로 카드 클릭 + URL 캡처)
    Phase 3: 상세 데이터 수집 (fetch_detail + parse_detail)

    사용법:
        # 기본 (목록만 수집)
        crawler = RocketPunchCrawler()
        postings = crawler.run(start_page=1, end_page=3, fetch_details=False)

        # 상세 포함 (page_action으로 URL 캡처 후 상세 수집)
        postings = crawler.run(start_page=1, end_page=1, fetch_details=True)

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
        real_chrome: Optional[bool] = None,
    ):
        """
        Args:
            crawl_delay: 요청 간격 (초). 기본 5초.
            keywords: 검색 키워드 (빈 문자열이면 전체 조회)
            order: 정렬 기준 - "recent"(최신순), "score"(적합순)
            headless: 브라우저 숨김 모드 (True 권장)
            real_chrome: True이면 시스템에 설치된 Chrome을 사용(channel="chrome").
                         False이면 patchright 번들 chromium(channel="chromium").
                         None(기본)이면 환경변수 CRAWLER_REAL_CHROME 또는
                         시스템 Chrome 설치 여부로 자동 결정한다.
                         Windows + patchright 조합에서 번들 chromium이
                         `spawn UNKNOWN`으로 실패하는 환경에서는 True 권장.
        """
        super().__init__(
            site_name="rocketpunch",
            base_url=BASE_URL,
            crawl_delay=crawl_delay,
        )
        self.keywords = keywords
        self.order = order
        self.headless = headless
        self.real_chrome = self._resolve_real_chrome(real_chrome)
        self._session = None

    @staticmethod
    def _resolve_real_chrome(value: Optional[bool]) -> bool:
        """real_chrome 기본값을 결정한다.

        우선순위: 명시 인자 > 환경변수 > 시스템 Chrome 자동 감지.
        """
        if value is not None:
            return value

        import os as _os
        env = _os.environ.get("CRAWLER_REAL_CHROME", "").strip().lower()
        if env in ("1", "true", "yes", "on"):
            return True
        if env in ("0", "false", "no", "off"):
            return False

        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
        ]
        return any(_os.path.exists(p) for p in candidates)

    def _get_fetcher(self):
        """
        Scrapling StealthyFetcher를 반환한다.
        StealthyFetcher가 실패하면 DynamicFetcher로 폴백.
        """
        from scrapling.fetchers import StealthyFetcher
        return StealthyFetcher

    def _get_dynamic_fetcher(self):
        """DynamicFetcher를 반환한다 (page_action이 필요한 경우)."""
        from scrapling.fetchers import DynamicFetcher
        return DynamicFetcher

    def _build_list_url(self, page: int) -> str:
        """목록 페이지 URL을 구성한다."""
        params = [f"page={page}"]
        if self.keywords:
            params.append(f"keywords={self.keywords}")
        if self.order:
            params.append(f"order={self.order}")

        query = "&".join(params)
        return f"{JOBS_URL}?{query}"

    # ================================================================
    # fetch_list: 목록 페이지 요청
    # ================================================================

    def fetch_list(self, page: int):
        """
        채용공고 목록 페이지를 요청한다.

        wait_selector로 가상 스크롤 카드가 렌더링될 때까지 대기한다.
        """
        url = self._build_list_url(page)
        logger.info(f"[rocketpunch] list request: {url}")

        try:
            fetcher = self._get_fetcher()
            response = fetcher.fetch(
                url,
                headless=self.headless,
                real_chrome=self.real_chrome,
                wait_selector=PW_JOB_CARD,          # 카드 렌더링 대기
                wait_selector_state="attached",
                network_idle=True,                    # 네트워크 안정 대기
                wait=2000,                            # 추가 2초 대기 (가상 스크롤 안정화)
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

    # ================================================================
    # fetch_list_with_urls: page_action으로 카드 클릭하여 상세 URL 수집
    # ================================================================

    def fetch_list_with_urls(self, page: int) -> list[dict]:
        """
        목록 페이지를 로드한 뒤, page_action으로 각 카드를 클릭하여
        상세 페이지 URL을 캡처한다.

        Scrapling page_action 파라미터를 사용:
        - page_action은 Playwright Page 객체를 인자로 받는 콜백 함수
        - 카드를 순서대로 클릭 → page.url 변화 캡처 → 뒤로가기 → 다음 카드

        Returns:
            [{"data_index": "0", "detail_url": "https://..."}]
        """
        url = self._build_list_url(page)
        logger.info(f"[rocketpunch] list+urls request: {url}")

        # page_action에서 수집한 URL을 담을 컨테이너
        captured_urls: list[dict] = []

        def click_cards_and_capture(pw_page):
            """
            Playwright Page 객체로 카드를 클릭하고 URL을 캡처한다.
            이 함수는 Scrapling의 page_action 콜백으로 전달된다.
            """
            import time as _time

            try:
                # 카드 렌더링 대기
                pw_page.wait_for_selector(PW_JOB_CARD, state="attached", timeout=10000)
                _time.sleep(1)  # 가상 스크롤 안정화

                # 현재 보이는 카드 수 확인
                cards = pw_page.query_selector_all(PW_JOB_CARD)
                total = len(cards)
                logger.info(f"[rocketpunch] page_action: {total} cards visible")

                original_url = pw_page.url

                for i, card in enumerate(cards):
                    try:
                        data_index = card.get_attribute("data-index") or str(i)

                        # 카드 클릭 → SPA 네비게이션 발생
                        card.click()
                        pw_page.wait_for_load_state("networkidle", timeout=8000)
                        _time.sleep(0.5)

                        new_url = pw_page.url

                        # URL이 변경되었으면 상세 페이지로 이동한 것
                        if new_url != original_url and "/jobs/" in new_url:
                            captured_urls.append({
                                "data_index": data_index,
                                "detail_url": new_url,
                            })
                            logger.debug(
                                f"[rocketpunch] card {data_index} → {new_url}"
                            )

                            # 목록으로 돌아가기
                            pw_page.go_back()
                            pw_page.wait_for_load_state("networkidle", timeout=8000)
                            pw_page.wait_for_selector(PW_JOB_CARD, state="attached", timeout=5000)
                            _time.sleep(1)

                            # 가상 스크롤 때문에 카드 목록이 재렌더링될 수 있음
                            # 새로 쿼리
                            cards = pw_page.query_selector_all(PW_JOB_CARD)

                        else:
                            logger.debug(
                                f"[rocketpunch] card {data_index}: no navigation"
                            )

                    except Exception as card_err:
                        logger.warning(
                            f"[rocketpunch] card {i} click error: {card_err}"
                        )
                        # 에러 시 원래 페이지로 복귀 시도
                        try:
                            if pw_page.url != original_url:
                                pw_page.go_back()
                                pw_page.wait_for_load_state("networkidle", timeout=5000)
                                _time.sleep(1)
                        except Exception:
                            pass

            except Exception as action_err:
                logger.error(f"[rocketpunch] page_action error: {action_err}")

        try:
            fetcher = self._get_fetcher()
            response = fetcher.fetch(
                url,
                headless=self.headless,
                real_chrome=self.real_chrome,
                page_action=click_cards_and_capture,
                wait_selector=PW_JOB_CARD,
                wait_selector_state="attached",
                network_idle=True,
                wait=2000,
            )

            logger.info(
                f"[rocketpunch] captured {len(captured_urls)} detail URLs"
            )
            return captured_urls

        except Exception as e:
            logger.error(f"[rocketpunch] fetch_list_with_urls error: {e}")
            return []

    # ================================================================
    # fetch_list_with_xhr: capture_xhr로 API 엔드포인트 캡처
    # ================================================================

    def fetch_list_with_xhr(self, page: int, xhr_pattern: str = r"/api/.*job"):
        """
        목록 페이지 로드 시 발생하는 XHR/fetch 요청을 캡처한다.

        capture_xhr: regex 패턴에 매칭되는 XHR/fetch 응답을 자동 수집.
        API 엔드포인트를 발견하면 직접 API를 호출하는 방식으로 전환 가능.

        Returns:
            (response, xhr_responses): 페이지 응답과 캡처된 XHR 목록
        """
        url = self._build_list_url(page)
        logger.info(f"[rocketpunch] list+xhr request: {url} (pattern={xhr_pattern})")

        try:
            fetcher = self._get_fetcher()
            response = fetcher.fetch(
                url,
                headless=self.headless,
                real_chrome=self.real_chrome,
                capture_xhr=xhr_pattern,
                wait_selector=PW_JOB_CARD,
                wait_selector_state="attached",
                network_idle=True,
                wait=3000,
            )

            # 캡처된 XHR은 response 객체에 포함됨
            xhr_data = getattr(response, "xhr_captured", [])
            if xhr_data:
                logger.info(
                    f"[rocketpunch] captured {len(xhr_data)} XHR responses"
                )
                for xhr in xhr_data:
                    logger.debug(
                        f"[rocketpunch] XHR: {xhr.url} (status={xhr.status})"
                    )

            return response, xhr_data

        except Exception as e:
            logger.error(f"[rocketpunch] fetch_list_with_xhr error: {e}")
            return None, []

    # ================================================================
    # scroll_and_collect: 가상 스크롤 전체 카드 수집
    # ================================================================

    def fetch_all_cards_scrolling(self, page: int) -> Optional[object]:
        """
        page_action으로 가상 스크롤 컨테이너를 끝까지 스크롤하여
        모든 카드가 렌더링된 상태의 HTML을 가져온다.

        tanstack/react-virtual은 뷰포트에 보이는 카드만 렌더링하므로
        스크롤을 해야 전체 카드를 확인할 수 있다.

        Returns:
            Scrapling response (스크롤 완료 후 HTML)
        """
        url = self._build_list_url(page)
        logger.info(f"[rocketpunch] scroll-fetch request: {url}")

        all_indices_seen: set = set()

        def scroll_to_bottom(pw_page):
            """가상 스크롤 컨테이너를 끝까지 스크롤한다."""
            import time as _time

            try:
                pw_page.wait_for_selector(PW_JOB_CARD, state="attached", timeout=10000)
                _time.sleep(1)

                container = pw_page.query_selector(PW_SCROLL_CONTAINER)
                if not container:
                    logger.warning("[rocketpunch] scroll container not found")
                    return

                max_scrolls = 50
                scroll_step = 500  # pixels

                for scroll_num in range(max_scrolls):
                    # 현재 보이는 카드 인덱스 수집
                    cards = pw_page.query_selector_all(PW_JOB_CARD)
                    current_indices = set()
                    for c in cards:
                        idx = c.get_attribute("data-index")
                        if idx is not None:
                            current_indices.add(idx)
                            all_indices_seen.add(idx)

                    # 스크롤 실행
                    pw_page.evaluate(
                        f"""() => {{
                            const el = document.querySelector('{PW_SCROLL_CONTAINER}');
                            if (el) el.scrollTop += {scroll_step};
                        }}"""
                    )
                    _time.sleep(0.3)

                    # 새 카드가 안 나타나면 끝
                    cards_after = pw_page.query_selector_all(PW_JOB_CARD)
                    new_indices = set()
                    for c in cards_after:
                        idx = c.get_attribute("data-index")
                        if idx is not None:
                            new_indices.add(idx)
                            all_indices_seen.add(idx)

                    if new_indices == current_indices and scroll_num > 3:
                        logger.info(
                            f"[rocketpunch] scroll done at step {scroll_num}, "
                            f"total unique cards: {len(all_indices_seen)}"
                        )
                        break

                logger.info(
                    f"[rocketpunch] scroll complete: "
                    f"{len(all_indices_seen)} unique card indices seen"
                )

            except Exception as e:
                logger.error(f"[rocketpunch] scroll error: {e}")

        try:
            fetcher = self._get_fetcher()
            response = fetcher.fetch(
                url,
                headless=self.headless,
                real_chrome=self.real_chrome,
                page_action=scroll_to_bottom,
                wait_selector=PW_JOB_CARD,
                wait_selector_state="attached",
                network_idle=True,
                wait=1000,
            )

            return response

        except Exception as e:
            logger.error(f"[rocketpunch] fetch_all_cards_scrolling error: {e}")
            return None

    # ================================================================
    # parse_list: 목록 파싱 (정적 HTML)
    # ================================================================

    def parse_list(self, response) -> list[dict]:
        """
        목록 페이지에서 채용공고 정보를 추출한다.

        로켓펀치 HTML 구조 (2026-04):
        - div[data-index="N"] : 가상 스크롤 아이템 (각 공고 카드)
        - 카드 내부:
            - p[class*="BodyS"] (첫 번째) → 회사명
            - p[class*="BodyM_Bold"] → 공고 제목
            - p[class*="BodyS"] (두 번째) → 카테고리

        Returns:
            [{"posting_id": "...", "title": "...", "company_name": "...", ...}]
        """
        items = []

        # 방법 1: Scrapling CSS 셀렉터
        cards = response.css(SEL_JOB_CARD)

        if not cards:
            logger.warning("[rocketpunch] No job cards found (div[data-index])")
            # 폴백: regex 파싱
            items = self._parse_list_regex(str(response.body))
            return items

        for card in cards:
            item = self._parse_card(card)
            if item:
                items.append(item)

        logger.info(f"[rocketpunch] list parsed: {len(items)} postings found")
        return items

    def parse_list_with_urls(
        self, response, url_map: list[dict]
    ) -> list[dict]:
        """
        parse_list 결과에 fetch_list_with_urls로 캡처한 URL을 병합한다.

        Args:
            response: fetch_list의 응답
            url_map: fetch_list_with_urls의 반환값
                     [{"data_index": "0", "detail_url": "..."}]

        Returns:
            URL이 포함된 공고 리스트
        """
        items = self.parse_list(response)

        # data_index로 URL 매핑
        idx_to_url = {
            entry["data_index"]: entry["detail_url"]
            for entry in url_map
        }

        for item in items:
            data_idx = item.get("data_index", "")
            if data_idx in idx_to_url:
                detail_url = idx_to_url[data_idx]
                item["url"] = detail_url

                # URL에서 posting_id 추출
                id_match = re.search(r"/jobs/(\d+)", detail_url)
                if id_match:
                    item["posting_id"] = id_match.group(1)

        with_url = sum(1 for i in items if i.get("url"))
        logger.info(
            f"[rocketpunch] URL merged: {with_url}/{len(items)} have detail URL"
        )

        return items

    # ================================================================
    # run 오버라이드: 동적 크롤링 통합
    # ================================================================

    def run(
        self,
        start_page: int = 1,
        end_page: int = 1,
        fetch_details: bool = True,
        discover_api: bool = False,
    ) -> list[JobPosting]:
        """
        크롤링을 실행한다.

        동작 방식:
        1. fetch_list (wait_selector로 카드 렌더링 대기)
        2. parse_list (CSS 셀렉터 + regex 폴백)
        3. fetch_details=True일 때:
           a. fetch_list_with_urls (page_action으로 카드 클릭 → URL 캡처)
           b. fetch_detail + parse_detail (상세 페이지 수집)
        4. discover_api=True일 때:
           - capture_xhr로 API 엔드포인트 탐색 (개발용)

        Args:
            start_page: 시작 페이지
            end_page: 종료 페이지
            fetch_details: True이면 상세 페이지도 수집 (page_action 사용)
            discover_api: True이면 XHR 캡처로 API 탐색 (디버깅용)
        """
        postings: list[JobPosting] = []

        # robots.txt 확인
        if not self.check_robots(self.base_url):
            logger.error(
                f"[{self.site_name}] robots.txt blocked. aborting."
            )
            return postings

        logger.info(
            f"[{self.site_name}] crawl start "
            f"(pages {start_page}~{end_page}, details={fetch_details})"
        )

        for page_num in range(start_page, end_page + 1):
            logger.info(f"[{self.site_name}] page {page_num}")

            # ── API 탐색 모드 ──
            if discover_api:
                self.rate_limiter.wait()
                response, xhr_list = self.fetch_list_with_xhr(page_num)
                if xhr_list:
                    for xhr in xhr_list:
                        logger.info(
                            f"[API DISCOVERED] {xhr.url} "
                            f"(status={xhr.status})"
                        )
                        try:
                            body = xhr.text()
                            logger.info(f"[API BODY] {body[:500]}")
                        except Exception:
                            pass
                if response:
                    self.rate_limiter.on_success()
                else:
                    self.rate_limiter.on_error()
                continue

            # ── Phase 1: 목록 수집 ──
            self.rate_limiter.wait()
            response = self.fetch_list(page_num)

            if response is None:
                self.rate_limiter.on_error()
                logger.warning(f"[{self.site_name}] page {page_num} failed, skip")
                continue

            self.rate_limiter.on_success()
            items = self.parse_list(response)
            logger.info(f"[{self.site_name}] page {page_num}: {len(items)} found")

            if not fetch_details:
                # 목록 데이터만으로 JobPosting 생성
                for item in items:
                    postings.append(
                        JobPosting(
                            posting_id=item.get("posting_id", ""),
                            title=item.get("title", ""),
                            company_name=item.get("company_name", ""),
                            source_url=item.get("url", ""),
                            source_site=self.site_name,
                        )
                    )
                continue

            # ── Phase 2: 상세 URL 캡처 (page_action) ──
            logger.info(f"[{self.site_name}] capturing detail URLs via page_action...")
            self.rate_limiter.wait()
            url_map = self.fetch_list_with_urls(page_num)

            if url_map:
                # URL을 items에 병합
                idx_to_url = {
                    entry["data_index"]: entry["detail_url"]
                    for entry in url_map
                }
                for item in items:
                    data_idx = item.get("data_index", "")
                    if data_idx in idx_to_url:
                        item["url"] = idx_to_url[data_idx]
                        id_match = re.search(r"/jobs/(\d+)", item["url"])
                        if id_match:
                            item["posting_id"] = id_match.group(1)

            # ── Phase 3: 상세 페이지 수집 ──
            for item in items:
                detail_url = item.get("url", "")
                if not detail_url:
                    # URL 없는 건은 목록 데이터로만 생성
                    postings.append(
                        JobPosting(
                            posting_id=item.get("posting_id", ""),
                            title=item.get("title", ""),
                            company_name=item.get("company_name", ""),
                            source_url="",
                            source_site=self.site_name,
                        )
                    )
                    continue

                if not self.check_robots(detail_url):
                    logger.warning(f"robots.txt blocked: {detail_url}")
                    continue

                self.rate_limiter.wait()
                detail_response = self.fetch_detail(detail_url)

                if detail_response is None:
                    self.rate_limiter.on_error()
                    continue

                self.rate_limiter.on_success()
                posting = self.parse_detail(detail_response, detail_url)

                if posting:
                    postings.append(posting)
                    logger.debug(f"[{self.site_name}] collected: {posting.title}")

        logger.info(
            f"[{self.site_name}] crawl done: {len(postings)} total"
        )
        return postings

    # ================================================================
    # 카드 파싱 (정적)
    # ================================================================

    def _parse_card(self, card) -> Optional[dict]:
        """개별 카드 요소에서 공고 정보를 추출한다."""
        try:
            data_index = card.attrib.get("data-index", "")

            company_name = ""
            job_title = ""
            category = ""

            # 방법 A: CSS 셀렉터
            try:
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

            # 방법 B: regex 폴백
            if not job_title:
                card_html = str(card)
                job_title, company_name, category = self._regex_extract_card(card_html)

            if not job_title:
                logger.debug(f"[rocketpunch] card {data_index}: no title, skip")
                return None

            # 회사 로고 이미지에서 company ID 추출
            company_id = ""
            company_logo_url = ""
            try:
                imgs = card.css('img[alt="image"]')
                if imgs:
                    src = imgs[0].attrib.get("src", "")
                    company_logo_url = src
                    id_match = re.search(
                        r"image\.rocketpunch\.com/company/(\d+)/", src
                    )
                    if id_match:
                        company_id = id_match.group(1)
            except Exception:
                pass

            match_info = self._extract_match_info(card)

            posting_id = f"rp-list-{data_index}"
            if company_id:
                posting_id = f"rp-{company_id}-{data_index}"

            return {
                "posting_id": posting_id,
                "url": "",
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

    # ================================================================
    # fetch_detail / parse_detail
    # ================================================================

    def fetch_detail(self, url: str):
        """
        공고 상세 페이지를 요청한다.
        wait_selector로 주요 콘텐츠 렌더링을 대기한다.
        """
        logger.debug(f"[rocketpunch] detail request: {url}")

        try:
            fetcher = self._get_fetcher()
            response = fetcher.fetch(
                url,
                headless=self.headless,
                real_chrome=self.real_chrome,
                wait_selector="h1",                    # 제목 렌더링 대기
                wait_selector_state="visible",
                network_idle=True,
                wait=2000,
            )

            if response.status >= 400:
                logger.warning(
                    f"[rocketpunch] detail failed (status={response.status}): {url}"
                )
                return None

            return response

        except Exception as e:
            logger.error(f"[rocketpunch] detail error: {url} - {e}")
            return None

    def parse_detail(self, response, url: str) -> Optional[JobPosting]:
        """
        상세 페이지에서 채용공고 정보를 파싱한다.
        """
        try:
            posting_id = ""
            id_match = re.search(r"/jobs/(\w+)", url)
            if id_match:
                posting_id = id_match.group(1)

            title = self._extract_text(response, [
                "h1",
                'p[class*="BodyXL_Bold"]',
                'p[class*="BodyL_Bold"]',
                "[class*='title'] h1",
                "title",
            ])

            company = self._extract_text(response, [
                'p[class*="BodyM"][class*="secondary"]',
                "[class*='company-name']",
                "[class*='company'] a",
            ])

            location = self._extract_text(response, [
                "[class*='location']",
                "[class*='address']",
            ])

            experience = self._extract_text(response, [
                "[class*='experience']",
                "[class*='career']",
            ])

            education = self._extract_text(response, [
                "[class*='education']",
            ])

            employment_type = self._extract_text(response, [
                "[class*='employment']",
                "[class*='job-type']",
            ])

            salary = self._extract_text(response, [
                "[class*='salary']",
                "[class*='compensation']",
            ])

            closing_date = self._extract_text(response, [
                "[class*='deadline']",
                "[class*='due-date']",
                "[class*='closing']",
            ])

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

    # ================================================================
    # 유틸리티
    # ================================================================

    def _get_text(self, element) -> str:
        """요소에서 텍스트를 추출한다."""
        try:
            texts = element.css("::text").getall()
            return " ".join(t.strip() for t in texts if t.strip())
        except Exception:
            return ""

    def _extract_text(self, response, selectors: list[str]) -> str:
        """여러 CSS 셀렉터를 순서대로 시도하여 첫 매치 텍스트를 반환."""
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

    def _regex_extract_card(self, html: str) -> tuple[str, str, str]:
        """카드 HTML에서 regex로 제목, 회사명, 카테고리를 추출한다."""
        title = ""
        company = ""
        category = ""

        body_s_matches = re.findall(
            r'textStyle_Body\.BodyS[^"]*c_foregrounds\.neutral\.secondary[^"]*lc_1">'
            r"(.*?)</p>",
            html,
        )
        if body_s_matches:
            company = body_s_matches[0]
        if len(body_s_matches) >= 2:
            category = body_s_matches[1]

        title_match = re.search(
            r'textStyle_Body\.BodyM_Bold[^"]*c_foregrounds\.neutral\.primary">'
            r"(.*?)</p>",
            html,
        )
        if title_match:
            title = title_match.group(1)

        return title, company, category

    def _extract_match_info(self, card) -> dict:
        """카드의 매칭 정보(직군, 숙련도, 규모, 근무방식)를 추출한다."""
        match_info = {}
        try:
            headers = card.css('p[class*="ta_center"]')
            checks = card.css('use[href="#check-thick-outline"]')
            x_marks = card.css('use[href="#x-circle-outline"]')

            header_texts = [self._get_text(h) for h in headers]
            total_icons = len(checks) + len(x_marks)

            if header_texts and total_icons > 0:
                for i, header in enumerate(header_texts):
                    match_info[header] = i < len(checks)

        except Exception:
            pass

        return match_info

    def _parse_list_regex(self, html: str) -> list[dict]:
        """전체 HTML에서 regex로 공고 목록을 추출한다 (최종 폴백)."""
        items = []
        card_pattern = r'data-index="(\d+)"(.*?)(?=data-index="|$)'
        for match in re.finditer(card_pattern, html, re.DOTALL):
            idx = match.group(1)
            card_html = match.group(2)

            title, company, category = self._regex_extract_card(card_html)
            if not title:
                continue

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

        logger.info(f"[rocketpunch] regex fallback: {len(items)} postings")
        return items
