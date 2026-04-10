"""
크롤러 베이스 클래스 (Scrapling 기반)

모든 사이트별 크롤러가 상속하는 추상 클래스.
새 사이트 추가 시 이 클래스를 상속하여 구현한다.

의존성: scrapling, patchright (StealthyFetcher용)
"""

from abc import ABC, abstractmethod
from typing import Optional
import logging
import time

from src.utils.robots import RobotsPolicy
from src.utils.rate_limiter import RateLimiter
from src.models.job_posting import JobPosting

logger = logging.getLogger(__name__)


class BaseCrawler(ABC):
    """
    Scrapling 기반 크롤러 추상 클래스.

    새 사이트를 추가하려면 이 클래스를 상속하고 다음 메서드를 구현한다:
    - fetch_list(page): 목록 페이지 HTML 가져오기
    - parse_list(page_response): 목록에서 공고 정보 추출
    - fetch_detail(url): 상세 페이지 HTML 가져오기
    - parse_detail(page_response, url): 상세 HTML에서 JobPosting 파싱

    Scrapling의 Fetcher 타입:
    - Fetcher: 단순 HTTP (정적 페이지용)
    - StealthyFetcher: anti-bot 우회 (AWS WAF, Cloudflare 등)
    - DynamicFetcher: 풀 브라우저 (JS 렌더링 필수 사이트)

    참고: [manual/04-development.md] 에 새 크롤러 추가 방법 상세 기술
    """

    def __init__(
        self,
        site_name: str,
        base_url: str,
        crawl_delay: Optional[float] = None,
    ):
        self.site_name = site_name
        self.base_url = base_url
        self.robots = RobotsPolicy()

        # crawl_delay: 명시적 지정 > robots.txt > 기본값
        delay = crawl_delay or self.robots.get_crawl_delay(base_url)
        self.rate_limiter = RateLimiter(base_delay=delay)

        logger.info(
            f"[{site_name}] 크롤러 초기화 (base_url={base_url}, delay={delay}초)"
        )

    def check_robots(self, url: str) -> bool:
        """robots.txt 정책에 따라 접근 가능 여부를 확인한다."""
        return self.robots.can_fetch(url)

    @abstractmethod
    def fetch_list(self, page: int):
        """
        목록 페이지를 요청한다.

        Args:
            page: 페이지 번호 (1부터 시작)

        Returns:
            Scrapling 응답 객체 또는 None
        """
        pass

    @abstractmethod
    def parse_list(self, response) -> list[dict]:
        """
        목록 응답에서 개별 공고 정보를 추출한다.

        Args:
            response: Scrapling 응답 객체

        Returns:
            공고 기본 정보 리스트 [{"url": ..., "posting_id": ..., ...}]
        """
        pass

    @abstractmethod
    def fetch_detail(self, url: str):
        """
        개별 공고 상세 페이지를 요청한다.

        Returns:
            Scrapling 응답 객체 또는 None
        """
        pass

    @abstractmethod
    def parse_detail(self, response, url: str) -> Optional[JobPosting]:
        """
        상세 응답에서 채용공고 데이터를 파싱한다.

        Returns:
            JobPosting 인스턴스, 파싱 실패 시 None
        """
        pass

    def run(
        self,
        start_page: int = 1,
        end_page: int = 1,
        fetch_details: bool = True,
    ) -> list[JobPosting]:
        """
        크롤링을 실행한다.

        Args:
            start_page: 시작 페이지
            end_page: 종료 페이지
            fetch_details: True이면 상세 페이지도 수집

        Returns:
            수집된 JobPosting 목록
        """
        postings: list[JobPosting] = []

        # robots.txt 확인
        if not self.check_robots(self.base_url):
            logger.error(
                f"[{self.site_name}] robots.txt에 의해 차단됨. 크롤링 중단."
            )
            return postings

        logger.info(
            f"[{self.site_name}] 크롤링 시작 "
            f"(page {start_page}~{end_page})"
        )

        for page_num in range(start_page, end_page + 1):
            logger.info(f"[{self.site_name}] 목록 페이지 {page_num} 요청")

            self.rate_limiter.wait()
            response = self.fetch_list(page_num)

            if response is None:
                self.rate_limiter.on_error()
                logger.warning(
                    f"[{self.site_name}] 페이지 {page_num} 실패, 건너뜀"
                )
                continue

            self.rate_limiter.on_success()
            items = self.parse_list(response)
            logger.info(
                f"[{self.site_name}] 페이지 {page_num}: {len(items)}건 발견"
            )

            if not fetch_details:
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

            # 상세 페이지 수집
            for item in items:
                detail_url = item.get("url", "")
                if not detail_url:
                    continue

                if not self.check_robots(detail_url):
                    logger.warning(f"robots.txt 차단: {detail_url}")
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
                    logger.debug(
                        f"[{self.site_name}] 수집 완료: {posting.title}"
                    )

        logger.info(
            f"[{self.site_name}] 크롤링 완료: 총 {len(postings)}건 수집"
        )
        return postings
