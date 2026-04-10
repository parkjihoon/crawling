"""
요청 속도 제한 모듈

서버 부하를 최소화하기 위한 요청 간격 관리.
연속 에러 시 백오프(간격 2배 증가)를 적용한다.
"""

import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 백오프 설정
MAX_BACKOFF_DELAY = 60  # 최대 대기 시간 (초)
BACKOFF_MULTIPLIER = 2  # 에러 시 대기 시간 배수


class RateLimiter:
    """
    요청 간격을 관리하는 클래스.

    사용법:
        limiter = RateLimiter(base_delay=5.0)

        for url in urls:
            limiter.wait()          # 적절한 간격 대기
            response = fetch(url)
            if response.ok:
                limiter.on_success()
            else:
                limiter.on_error()  # 백오프 적용
    """

    def __init__(self, base_delay: float = 3.0):
        """
        Args:
            base_delay: 기본 요청 간격 (초). robots.txt crawl-delay가 있으면 그 값을 사용.
        """
        self.base_delay = base_delay
        self.current_delay = base_delay
        self.consecutive_errors = 0
        self._last_request_time: Optional[float] = None

    def wait(self) -> float:
        """
        다음 요청 전에 적절한 시간만큼 대기한다.

        Returns:
            실제 대기한 시간 (초)
        """
        if self._last_request_time is not None:
            elapsed = time.time() - self._last_request_time
            remaining = self.current_delay - elapsed
            if remaining > 0:
                logger.debug(f"요청 대기: {remaining:.1f}초")
                time.sleep(remaining)

        self._last_request_time = time.time()
        return self.current_delay

    def on_success(self):
        """요청 성공 시 호출. 백오프 상태를 초기화한다."""
        if self.consecutive_errors > 0:
            logger.info(
                f"요청 성공 - 백오프 해제 "
                f"(이전 연속 에러: {self.consecutive_errors}회)"
            )
        self.consecutive_errors = 0
        self.current_delay = self.base_delay

    def on_error(self):
        """
        요청 실패 시 호출. 백오프를 적용한다.
        연속 에러마다 대기 시간이 2배씩 증가하며, 최대 60초.
        """
        self.consecutive_errors += 1
        self.current_delay = min(
            self.base_delay * (BACKOFF_MULTIPLIER ** self.consecutive_errors),
            MAX_BACKOFF_DELAY,
        )
        logger.warning(
            f"요청 에러 - 백오프 적용 "
            f"(연속 에러: {self.consecutive_errors}회, "
            f"다음 대기: {self.current_delay:.1f}초)"
        )

    def reset(self):
        """상태를 초기화한다."""
        self.current_delay = self.base_delay
        self.consecutive_errors = 0
        self._last_request_time = None

    @property
    def stats(self) -> dict:
        """현재 상태를 반환한다."""
        return {
            "base_delay": self.base_delay,
            "current_delay": self.current_delay,
            "consecutive_errors": self.consecutive_errors,
        }
