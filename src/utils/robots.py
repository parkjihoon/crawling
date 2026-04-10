"""
robots.txt 정책 관리 모듈

대상 사이트의 robots.txt를 파싱하고 크롤링 가능 여부를 판단한다.
robots.txt가 없는 사이트에는 기본(보수적) 정책을 생성하여 적용한다.
"""

import urllib.robotparser
import urllib.request
import urllib.error
from urllib.parse import urlparse
import logging
import time
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)

# robots.txt가 없는 사이트에 적용할 기본 정책
DEFAULT_ROBOTS_TXT = """
User-Agent: *
Allow: /
Crawl-delay: 5
""".strip()

# 기본 크롤링 간격 (초)
DEFAULT_CRAWL_DELAY = 3
# 정부/공공 사이트 크롤링 간격 (초)
GOVERNMENT_CRAWL_DELAY = 5
# robots.txt 캐시 유효 시간 (초)
ROBOTS_CACHE_TTL = 3600  # 1시간


class RobotsPolicy:
    """
    사이트별 robots.txt 정책을 관리하는 클래스.

    사용법:
        policy = RobotsPolicy()

        # 크롤링 가능 여부 확인
        if policy.can_fetch("https://www.work24.go.kr/wk/a/b/1200/some_page.do"):
            # 크롤링 진행
            delay = policy.get_crawl_delay("https://www.work24.go.kr")
            time.sleep(delay)

    새 사이트 추가 시:
        - robots.txt가 있으면 자동으로 파싱하여 사용
        - robots.txt가 없으면 DEFAULT_ROBOTS_TXT 기본 정책 적용
        - .go.kr 등 정부 사이트는 GOVERNMENT_CRAWL_DELAY(5초) 적용
    """

    def __init__(self, user_agent: str = "FraudJobCrawler/1.0"):
        self.user_agent = user_agent
        self._cache: dict[str, dict] = {}

    def _get_base_url(self, url: str) -> str:
        """URL에서 base URL (scheme + netloc)을 추출한다."""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def _is_government_site(self, url: str) -> bool:
        """정부/공공 사이트 여부를 판단한다."""
        parsed = urlparse(url)
        gov_domains = [".go.kr", ".gov", ".or.kr", ".ac.kr"]
        return any(parsed.netloc.endswith(d) for d in gov_domains)

    def _fetch_robots_txt(self, base_url: str) -> Optional[str]:
        """
        사이트의 robots.txt를 가져온다.
        없거나 에러 시 None을 반환한다.
        """
        robots_url = f"{base_url}/robots.txt"
        try:
            req = urllib.request.Request(
                robots_url,
                headers={"User-Agent": self.user_agent}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                content = response.read().decode("utf-8", errors="ignore")
                logger.info(f"robots.txt 로드 성공: {robots_url}")
                return content
        except urllib.error.HTTPError as e:
            if e.code == 404:
                logger.warning(f"robots.txt 없음: {robots_url} → 기본 정책 적용")
            else:
                logger.warning(f"robots.txt 로드 실패 ({e.code}): {robots_url}")
            return None
        except Exception as e:
            logger.warning(f"robots.txt 로드 에러: {robots_url} - {e}")
            return None

    def _load_policy(self, url: str) -> dict:
        """
        URL의 robots.txt 정책을 로드하고 캐시한다.

        반환 구조:
        {
            "parser": RobotFileParser 인스턴스,
            "crawl_delay": float,
            "has_robots_txt": bool,
            "loaded_at": float (timestamp),
            "raw_content": str
        }
        """
        base_url = self._get_base_url(url)

        # 캐시 확인
        if base_url in self._cache:
            cached = self._cache[base_url]
            if time.time() - cached["loaded_at"] < ROBOTS_CACHE_TTL:
                return cached

        # robots.txt 가져오기
        raw_content = self._fetch_robots_txt(base_url)
        has_robots_txt = raw_content is not None

        if not has_robots_txt:
            raw_content = DEFAULT_ROBOTS_TXT
            logger.info(f"기본 robots.txt 정책 적용: {base_url}")

        # 파서 생성
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(raw_content.splitlines())

        # crawl-delay 결정
        crawl_delay = self._determine_crawl_delay(
            parser, base_url, has_robots_txt
        )

        policy = {
            "parser": parser,
            "crawl_delay": crawl_delay,
            "has_robots_txt": has_robots_txt,
            "loaded_at": time.time(),
            "raw_content": raw_content,
        }
        self._cache[base_url] = policy

        logger.info(
            f"정책 로드 완료: {base_url} "
            f"(robots.txt: {'있음' if has_robots_txt else '없음(기본정책)'}, "
            f"crawl-delay: {crawl_delay}초)"
        )
        return policy

    def _determine_crawl_delay(
        self,
        parser: urllib.robotparser.RobotFileParser,
        base_url: str,
        has_robots_txt: bool,
    ) -> float:
        """
        crawl-delay를 결정한다.

        우선순위:
        1. robots.txt에 명시된 crawl-delay
        2. 정부 사이트이면 GOVERNMENT_CRAWL_DELAY (5초)
        3. 기본값 DEFAULT_CRAWL_DELAY (3초)
        """
        # robots.txt에서 crawl-delay 추출 시도
        try:
            delay = parser.crawl_delay(self.user_agent)
            if delay is not None:
                return float(delay)
        except AttributeError:
            pass

        # 정부 사이트 판별
        if self._is_government_site(base_url):
            return float(GOVERNMENT_CRAWL_DELAY)

        return float(DEFAULT_CRAWL_DELAY)

    def can_fetch(self, url: str) -> bool:
        """
        주어진 URL에 대해 크롤링이 허용되는지 확인한다.

        Args:
            url: 크롤링 대상 URL

        Returns:
            True이면 크롤링 가능, False이면 차단됨
        """
        policy = self._load_policy(url)
        allowed = policy["parser"].can_fetch(self.user_agent, url)

        if not allowed:
            logger.warning(f"robots.txt에 의해 차단됨: {url}")

        return allowed

    def get_crawl_delay(self, url: str) -> float:
        """
        해당 사이트의 크롤링 간격(초)을 반환한다.

        Args:
            url: 대상 사이트 URL

        Returns:
            크롤링 간격 (초)
        """
        policy = self._load_policy(url)
        return policy["crawl_delay"]

    def get_policy_info(self, url: str) -> dict:
        """
        사이트의 robots.txt 정책 정보를 반환한다.
        디버깅 및 로깅 용도.

        Returns:
            {
                "base_url": str,
                "has_robots_txt": bool,
                "crawl_delay": float,
                "raw_content": str
            }
        """
        base_url = self._get_base_url(url)
        policy = self._load_policy(url)
        return {
            "base_url": base_url,
            "has_robots_txt": policy["has_robots_txt"],
            "crawl_delay": policy["crawl_delay"],
            "raw_content": policy["raw_content"],
        }

    def save_default_robots(self, output_dir: str, site_name: str) -> str:
        """
        robots.txt가 없는 사이트를 위해 기본 정책 파일을 저장한다.
        기록 및 감사 용도.

        Args:
            output_dir: 저장 디렉토리
            site_name: 사이트 이름

        Returns:
            저장된 파일 경로
        """
        path = Path(output_dir) / f"{site_name}_default_robots.txt"
        path.write_text(DEFAULT_ROBOTS_TXT, encoding="utf-8")
        logger.info(f"기본 robots.txt 저장: {path}")
        return str(path)
