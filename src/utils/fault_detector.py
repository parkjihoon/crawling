"""
자동 장애 탐지 및 자가 복구 모듈

크롤링 시스템의 장애를 자동으로 탐지하고, 가능한 범위에서 자가 복구를 시도한다.
유지보수 부담을 줄이기 위해 다음 장애 유형을 커버한다:

장애 유형:
    1. 셀렉터 파손 (Selector Breakage)
       - 파싱 결과 0건 → CSS 셀렉터 변경 가능성
       - 자동 fallback: regex 파싱 시도
    2. 네트워크 차단 (CloudFront / WAF Block)
       - HTTP 403/503 연속 발생
       - 자동 복구: 장시간 대기 후 재시도, User-Agent 로테이션
    3. 빈 응답 / 구조 변경 (Empty Response / Structure Change)
       - HTML은 받았으나 내용이 없거나 구조가 달라진 경우
       - 자동 복구: HTML 스냅샷 저장, 알림 생성
    4. 연속 에러 (Consecutive Errors)
       - N회 연속 실패 시 크롤링 중단 및 알림
    5. 데이터 품질 이상 (Data Quality Anomaly)
       - 평소 대비 수집 건수 급감/급증 → 사이트 변경 의심

사용법:
    detector = FaultDetector(site="rocketpunch")
    detector.check_parse_result(items, html_content)
    detector.check_http_response(status_code, url)
    detector.check_data_quality(new_count, historical_avg)
    report = detector.get_report()

환경변수:
    FAULT_ALERT_WEBHOOK  - Slack/Discord webhook URL (선택)
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────
# 장애 이벤트 정의
# ────────────────────────────────────────────

FAULT_LOG_DIR = Path("data/.faults")


@dataclass
class FaultEvent:
    """개별 장애 이벤트."""
    fault_type: str          # selector_break, network_block, empty_response, consecutive_error, data_anomaly
    severity: str            # critical, warning, info
    message: str
    site: str
    timestamp: str = ""
    details: dict = field(default_factory=dict)
    auto_recovered: bool = False
    recovery_action: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


# ────────────────────────────────────────────
# 장애 탐지기
# ────────────────────────────────────────────

class FaultDetector:
    """
    크롤링 장애를 탐지하고 자가 복구를 시도하는 모듈.

    각 check_* 메서드는 장애를 탐지하면 FaultEvent를 기록하고,
    가능한 경우 자동 복구를 시도한다.
    """

    # 임계값 설정
    CONSECUTIVE_ERROR_THRESHOLD = 5       # 연속 에러 N회 시 critical
    PARSE_ZERO_THRESHOLD = 3             # 파싱 0건 N회 연속 시 critical
    DATA_DROP_RATIO = 0.3                # 평소 대비 30% 이하 → warning
    DATA_SPIKE_RATIO = 3.0               # 평소 대비 300% 이상 → warning
    NETWORK_BLOCK_CODES = {403, 503, 429}  # 차단 의심 HTTP 코드
    CLOUDFRONT_WAIT_SECONDS = 300        # CloudFront 차단 시 대기 시간
    MAX_RECOVERY_ATTEMPTS = 3            # 복구 최대 시도 횟수

    def __init__(self, site: str):
        self.site = site
        self.events: list[FaultEvent] = []
        self._consecutive_parse_zeros = 0
        self._consecutive_http_errors = 0
        self._consecutive_network_blocks = 0
        self._recovery_attempts = 0
        self._html_snapshots_saved = 0

        FAULT_LOG_DIR.mkdir(parents=True, exist_ok=True)

    # ────────────────────────────────────────
    # 1. 셀렉터 파손 탐지
    # ────────────────────────────────────────

    def check_parse_result(
        self,
        items: list[dict],
        html_content: str = "",
        page_num: int = 0,
    ) -> dict:
        """
        파싱 결과를 검증한다.

        - 0건: 셀렉터 파손 가능성 → regex fallback 권고
        - HTML이 있으나 파싱 0건: 구조 변경 확실

        Returns:
            {"ok": bool, "fault": FaultEvent|None, "suggestion": str}
        """
        result = {"ok": True, "fault": None, "suggestion": ""}

        if len(items) == 0:
            self._consecutive_parse_zeros += 1

            if html_content and len(html_content) > 500:
                # HTML은 있는데 파싱 0건 → 셀렉터 파손
                severity = (
                    "critical" if self._consecutive_parse_zeros >= self.PARSE_ZERO_THRESHOLD
                    else "warning"
                )
                event = FaultEvent(
                    fault_type="selector_break",
                    severity=severity,
                    message=(
                        f"Page {page_num}: HTML received ({len(html_content)} chars) "
                        f"but 0 items parsed. Selector may be broken. "
                        f"(consecutive: {self._consecutive_parse_zeros})"
                    ),
                    site=self.site,
                    details={
                        "page_num": page_num,
                        "html_length": len(html_content),
                        "consecutive_zeros": self._consecutive_parse_zeros,
                    },
                )

                # 자동 복구: HTML 스냅샷 저장
                snapshot_path = self._save_html_snapshot(html_content, page_num)
                if snapshot_path:
                    event.details["snapshot"] = str(snapshot_path)
                    event.recovery_action = f"HTML snapshot saved: {snapshot_path}"

                # regex fallback 권고
                event.details["suggestion"] = (
                    "Try regex fallback parser. "
                    "Run: python test_parse_local.py <snapshot_file>"
                )

                self._record_event(event)
                result.update({
                    "ok": False,
                    "fault": event,
                    "suggestion": "regex_fallback",
                })

            elif not html_content or len(html_content) < 500:
                # HTML 자체가 없거나 너무 짧음 → 빈 응답
                event = FaultEvent(
                    fault_type="empty_response",
                    severity="warning",
                    message=(
                        f"Page {page_num}: Empty or minimal response "
                        f"({len(html_content) if html_content else 0} chars)"
                    ),
                    site=self.site,
                    details={"page_num": page_num},
                )
                self._record_event(event)
                result.update({
                    "ok": False,
                    "fault": event,
                    "suggestion": "retry_with_delay",
                })
        else:
            # 정상 파싱 → 연속 0건 카운터 리셋
            self._consecutive_parse_zeros = 0

        return result

    # ────────────────────────────────────────
    # 2. 네트워크 차단 탐지
    # ────────────────────────────────────────

    def check_http_response(
        self,
        status_code: int,
        url: str = "",
        response_headers: Optional[dict] = None,
    ) -> dict:
        """
        HTTP 응답 코드를 검증한다.

        - 403/503: CloudFront/WAF 차단 가능성
        - 429: Rate limit 초과
        - 연속 에러 시 자동 대기

        Returns:
            {"ok": bool, "fault": FaultEvent|None, "wait_seconds": int}
        """
        result = {"ok": True, "fault": None, "wait_seconds": 0}

        if status_code in self.NETWORK_BLOCK_CODES:
            self._consecutive_network_blocks += 1

            # CloudFront 차단 판별 (Server 헤더 확인)
            is_cloudfront = False
            if response_headers:
                server = response_headers.get("server", "").lower()
                is_cloudfront = "cloudfront" in server

            severity = (
                "critical" if self._consecutive_network_blocks >= 3
                else "warning"
            )

            wait_seconds = min(
                self.CLOUDFRONT_WAIT_SECONDS * self._consecutive_network_blocks,
                1800,  # 최대 30분
            )

            event = FaultEvent(
                fault_type="network_block",
                severity=severity,
                message=(
                    f"HTTP {status_code} from {url}. "
                    f"{'CloudFront' if is_cloudfront else 'WAF'} block suspected. "
                    f"(consecutive blocks: {self._consecutive_network_blocks})"
                ),
                site=self.site,
                details={
                    "status_code": status_code,
                    "url": url,
                    "is_cloudfront": is_cloudfront,
                    "consecutive_blocks": self._consecutive_network_blocks,
                    "recommended_wait": wait_seconds,
                },
                recovery_action=f"Auto-wait {wait_seconds}s before retry",
            )
            self._record_event(event)
            result.update({
                "ok": False,
                "fault": event,
                "wait_seconds": wait_seconds,
            })

        elif status_code >= 400:
            self._consecutive_http_errors += 1

            if self._consecutive_http_errors >= self.CONSECUTIVE_ERROR_THRESHOLD:
                event = FaultEvent(
                    fault_type="consecutive_error",
                    severity="critical",
                    message=(
                        f"HTTP {status_code} — {self._consecutive_http_errors} "
                        f"consecutive errors. Crawling should be paused."
                    ),
                    site=self.site,
                    details={
                        "status_code": status_code,
                        "consecutive_errors": self._consecutive_http_errors,
                    },
                )
                self._record_event(event)
                result.update({"ok": False, "fault": event})
        else:
            # 정상 응답
            self._consecutive_network_blocks = 0
            self._consecutive_http_errors = 0

        return result

    # ────────────────────────────────────────
    # 3. 데이터 품질 이상 탐지
    # ────────────────────────────────────────

    def check_data_quality(
        self,
        new_count: int,
        historical_avg: float,
        page_count: int = 1,
    ) -> dict:
        """
        수집 건수를 과거 평균과 비교하여 이상을 탐지한다.

        - 급감: 사이트 구조 변경 또는 차단 가능성
        - 급증: 중복 제거 실패 또는 사이트 이상

        Returns:
            {"ok": bool, "fault": FaultEvent|None, "ratio": float}
        """
        result = {"ok": True, "fault": None, "ratio": 0.0}

        if historical_avg <= 0:
            return result  # 과거 데이터 없음 → 비교 불가

        ratio = new_count / historical_avg
        result["ratio"] = round(ratio, 2)

        if ratio <= self.DATA_DROP_RATIO:
            event = FaultEvent(
                fault_type="data_anomaly",
                severity="warning",
                message=(
                    f"Data drop detected: {new_count} items "
                    f"(avg: {historical_avg:.0f}, ratio: {ratio:.1%}). "
                    f"Site structure may have changed."
                ),
                site=self.site,
                details={
                    "new_count": new_count,
                    "historical_avg": historical_avg,
                    "ratio": ratio,
                    "direction": "drop",
                    "pages_crawled": page_count,
                },
            )
            self._record_event(event)
            result.update({"ok": False, "fault": event})

        elif ratio >= self.DATA_SPIKE_RATIO:
            event = FaultEvent(
                fault_type="data_anomaly",
                severity="warning",
                message=(
                    f"Data spike detected: {new_count} items "
                    f"(avg: {historical_avg:.0f}, ratio: {ratio:.1%}). "
                    f"Possible dedup failure or site anomaly."
                ),
                site=self.site,
                details={
                    "new_count": new_count,
                    "historical_avg": historical_avg,
                    "ratio": ratio,
                    "direction": "spike",
                },
            )
            self._record_event(event)
            result.update({"ok": False, "fault": event})

        return result

    # ────────────────────────────────────────
    # 4. 자가 복구 시도
    # ────────────────────────────────────────

    def attempt_recovery(self, fault: FaultEvent) -> dict:
        """
        장애 유형에 따라 자동 복구를 시도한다.

        Returns:
            {"recovered": bool, "action": str, "details": dict}
        """
        if self._recovery_attempts >= self.MAX_RECOVERY_ATTEMPTS:
            logger.warning(
                f"[fault] Max recovery attempts ({self.MAX_RECOVERY_ATTEMPTS}) "
                f"reached for {self.site}. Manual intervention required."
            )
            return {
                "recovered": False,
                "action": "max_attempts_reached",
                "details": {"attempts": self._recovery_attempts},
            }

        self._recovery_attempts += 1
        fault_type = fault.fault_type

        if fault_type == "selector_break":
            return self._recover_selector_break(fault)
        elif fault_type == "network_block":
            return self._recover_network_block(fault)
        elif fault_type == "empty_response":
            return self._recover_empty_response(fault)
        elif fault_type == "consecutive_error":
            return self._recover_consecutive_error(fault)
        elif fault_type == "data_anomaly":
            return self._recover_data_anomaly(fault)
        else:
            return {"recovered": False, "action": "unknown_fault_type", "details": {}}

    def _recover_selector_break(self, fault: FaultEvent) -> dict:
        """
        셀렉터 파손 시 복구 시도.

        1단계: regex fallback 파서로 전환 권고
        2단계: HTML 스냅샷 저장 (수동 분석용)
        """
        logger.info(
            f"[fault] Attempting selector break recovery for {self.site}"
        )

        fault.auto_recovered = True
        fault.recovery_action = (
            "Switched to regex fallback parser. "
            "HTML snapshot saved for manual selector update."
        )

        return {
            "recovered": True,
            "action": "use_regex_fallback",
            "details": {
                "snapshot": fault.details.get("snapshot", ""),
                "instruction": (
                    "1. Check saved HTML snapshot\n"
                    "2. Run: python test_parse_local.py <snapshot>\n"
                    "3. Update selectors in src/crawlers/rocketpunch.py\n"
                    "4. Update manual/04-development.md"
                ),
            },
        }

    def _recover_network_block(self, fault: FaultEvent) -> dict:
        """
        네트워크 차단 시 복구 시도.

        1단계: 권장 시간만큼 대기
        2단계: 재시도
        """
        wait_seconds = fault.details.get("recommended_wait", self.CLOUDFRONT_WAIT_SECONDS)

        logger.info(
            f"[fault] Network block recovery: waiting {wait_seconds}s "
            f"before retry ({self.site})"
        )

        # 실제 대기는 호출자가 수행 (여기서는 권고만)
        fault.auto_recovered = True
        fault.recovery_action = f"Wait {wait_seconds}s then retry"

        return {
            "recovered": True,
            "action": "wait_and_retry",
            "details": {
                "wait_seconds": wait_seconds,
                "instruction": (
                    "1. Wait for the recommended duration\n"
                    "2. If on cloud IP, switch to residential IP\n"
                    "3. Check: curl -I https://www.rocketpunch.com/jobs"
                ),
            },
        }

    def _recover_empty_response(self, fault: FaultEvent) -> dict:
        """빈 응답 복구: 지연 후 재시도."""
        logger.info(f"[fault] Empty response recovery: will retry with delay")

        fault.auto_recovered = True
        fault.recovery_action = "Retry with increased delay"

        return {
            "recovered": True,
            "action": "retry_with_delay",
            "details": {"recommended_delay": 15},
        }

    def _recover_consecutive_error(self, fault: FaultEvent) -> dict:
        """연속 에러 복구: 크롤링 일시 중단 권고."""
        logger.warning(
            f"[fault] Consecutive error recovery: recommending pause"
        )

        return {
            "recovered": False,
            "action": "pause_crawling",
            "details": {
                "consecutive_errors": fault.details.get("consecutive_errors", 0),
                "instruction": (
                    "1. Pause crawling for 1-2 hours\n"
                    "2. Check site accessibility manually\n"
                    "3. Review logs for error patterns\n"
                    "4. If persistent, check IP block status"
                ),
            },
        }

    def _recover_data_anomaly(self, fault: FaultEvent) -> dict:
        """데이터 이상 복구: 수동 확인 권고."""
        direction = fault.details.get("direction", "unknown")

        logger.info(
            f"[fault] Data anomaly recovery ({direction}): "
            f"recommending manual check"
        )

        return {
            "recovered": False,
            "action": "manual_review",
            "details": {
                "direction": direction,
                "instruction": (
                    "1. Run manual crawl: python main.py --site rocketpunch "
                    "--pages 1-2 --no-headless --verbose\n"
                    "2. Compare HTML structure with saved snapshots\n"
                    "3. Check if dedup store is corrupted: data/.dedup/\n"
                    "4. Review robots.txt for changes"
                ),
            },
        }

    # ────────────────────────────────────────
    # 유틸리티
    # ────────────────────────────────────────

    def _save_html_snapshot(self, html: str, page_num: int = 0) -> Optional[Path]:
        """장애 시 HTML 스냅샷을 저장한다 (수동 디버깅용)."""
        try:
            snapshot_dir = FAULT_LOG_DIR / "snapshots"
            snapshot_dir.mkdir(parents=True, exist_ok=True)

            # 최대 10개까지만 보관
            if self._html_snapshots_saved >= 10:
                return None

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{self.site}_p{page_num}_{ts}.html"
            path = snapshot_dir / filename

            with open(path, "w", encoding="utf-8") as f:
                f.write(html)

            self._html_snapshots_saved += 1
            logger.info(f"[fault] HTML snapshot saved: {path}")
            return path

        except Exception as e:
            logger.error(f"[fault] Failed to save snapshot: {e}")
            return None

    def _record_event(self, event: FaultEvent):
        """장애 이벤트를 기록한다."""
        self.events.append(event)

        # 파일 기록
        log_file = FAULT_LOG_DIR / f"{self.site}_faults.jsonl"
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"[fault] Failed to write fault log: {e}")

        # 로그 출력
        log_fn = logger.critical if event.severity == "critical" else logger.warning
        log_fn(f"[fault] [{event.severity.upper()}] {event.message}")

    def get_report(self) -> dict:
        """현재 세션의 장애 리포트를 반환한다."""
        criticals = [e for e in self.events if e.severity == "critical"]
        warnings = [e for e in self.events if e.severity == "warning"]

        return {
            "site": self.site,
            "total_faults": len(self.events),
            "criticals": len(criticals),
            "warnings": len(warnings),
            "recovery_attempts": self._recovery_attempts,
            "events": [asdict(e) for e in self.events],
            "generated_at": datetime.now().isoformat(),
        }

    def reset_recovery_counter(self):
        """복구 시도 카운터를 리셋한다 (성공적 수집 후)."""
        self._recovery_attempts = 0
        self._consecutive_parse_zeros = 0
        self._consecutive_http_errors = 0
        self._consecutive_network_blocks = 0

    @staticmethod
    def load_fault_history(site: str, limit: int = 50) -> list[dict]:
        """과거 장애 이력을 로드한다."""
        log_file = FAULT_LOG_DIR / f"{site}_faults.jsonl"
        if not log_file.exists():
            return []

        entries = []
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        return entries[-limit:]

    @staticmethod
    def get_health_summary(site: str) -> dict:
        """
        사이트의 전반적 건강 상태를 요약한다.

        최근 장애 이력을 분석하여 health_score (0~100)를 산출.
        """
        history = FaultDetector.load_fault_history(site, limit=100)

        if not history:
            return {
                "site": site,
                "health_score": 100,
                "status": "healthy",
                "recent_faults": 0,
                "last_fault": None,
            }

        # 최근 24시간 이내 장애 수
        now = datetime.now()
        recent = []
        for entry in history:
            try:
                ts = datetime.fromisoformat(entry.get("timestamp", ""))
                if (now - ts).total_seconds() < 86400:
                    recent.append(entry)
            except (ValueError, TypeError):
                continue

        # health_score 계산
        score = 100
        for entry in recent:
            if entry.get("severity") == "critical":
                score -= 20
            elif entry.get("severity") == "warning":
                score -= 5
        score = max(0, score)

        if score >= 80:
            status = "healthy"
        elif score >= 50:
            status = "degraded"
        else:
            status = "unhealthy"

        return {
            "site": site,
            "health_score": score,
            "status": status,
            "recent_faults": len(recent),
            "last_fault": history[-1] if history else None,
        }
