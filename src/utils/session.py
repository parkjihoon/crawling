"""
HTTP 세션 관리 모듈

쿠키 유지, User-Agent 설정, 재시도 로직을 포함한 세션을 관리한다.
"""

import urllib.request
import urllib.error
import urllib.parse
import http.cookiejar
import logging
import gzip
import io
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 30  # 초
MAX_RETRIES = 3


class HttpSession:
    """
    쿠키 유지 및 재시도를 지원하는 HTTP 세션.

    사용법:
        session = HttpSession()

        # GET 요청
        html = session.get("https://example.com/page")

        # POST 요청
        html = session.post("https://example.com/search", data={"q": "test"})
    """

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.timeout = timeout
        self.headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }
        # 쿠키 핸들러 설정
        self.cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookie_jar)
        )

    def _build_request(
        self, url: str, data: Optional[dict] = None
    ) -> urllib.request.Request:
        """Request 객체를 생성한다."""
        encoded_data = None
        if data is not None:
            encoded_data = urllib.parse.urlencode(data).encode("utf-8")

        req = urllib.request.Request(url, data=encoded_data)
        for key, value in self.headers.items():
            req.add_header(key, value)

        return req

    def _decode_response(self, response) -> str:
        """응답을 디코딩한다 (gzip 지원)."""
        raw = response.read()
        if response.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)

        # 인코딩 감지
        charset = response.headers.get_content_charset()
        if charset:
            return raw.decode(charset, errors="ignore")

        # 기본 인코딩 순서
        for encoding in ["utf-8", "euc-kr", "cp949"]:
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue

        return raw.decode("utf-8", errors="ignore")

    def get(self, url: str, retries: int = MAX_RETRIES) -> Optional[str]:
        """
        GET 요청을 보낸다.

        Args:
            url: 요청 URL
            retries: 최대 재시도 횟수

        Returns:
            HTML 문자열, 실패 시 None
        """
        req = self._build_request(url)

        for attempt in range(1, retries + 1):
            try:
                response = self.opener.open(req, timeout=self.timeout)
                html = self._decode_response(response)
                logger.debug(f"GET 성공 ({response.status}): {url}")
                return html
            except urllib.error.HTTPError as e:
                logger.warning(
                    f"GET 실패 ({e.code}) [{attempt}/{retries}]: {url}"
                )
                if e.code in (403, 404, 410):
                    return None  # 재시도 불필요
            except Exception as e:
                logger.warning(
                    f"GET 에러 [{attempt}/{retries}]: {url} - {e}"
                )

        logger.error(f"GET 최종 실패: {url}")
        return None

    def post(
        self, url: str, data: dict, retries: int = MAX_RETRIES
    ) -> Optional[str]:
        """
        POST 요청을 보낸다.

        Args:
            url: 요청 URL
            data: POST 폼 데이터
            retries: 최대 재시도 횟수

        Returns:
            HTML 문자열, 실패 시 None
        """
        req = self._build_request(url, data)

        for attempt in range(1, retries + 1):
            try:
                response = self.opener.open(req, timeout=self.timeout)
                html = self._decode_response(response)
                logger.debug(f"POST 성공 ({response.status}): {url}")
                return html
            except urllib.error.HTTPError as e:
                logger.warning(
                    f"POST 실패 ({e.code}) [{attempt}/{retries}]: {url}"
                )
                if e.code in (403, 404, 410):
                    return None
            except Exception as e:
                logger.warning(
                    f"POST 에러 [{attempt}/{retries}]: {url} - {e}"
                )

        logger.error(f"POST 최종 실패: {url}")
        return None
