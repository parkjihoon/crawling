"""
증분 수집 스케줄러

매일 자동으로 새로운 공고를 증분(incremental) 수집한다.
기존 수집 데이터와 비교하여 신규 공고만 추가 저장.

지원 모드:
    1. APScheduler (Python 내장 스케줄러, 프로세스 상시 구동)
    2. cronjob/Task Scheduler 연동 (외부 스케줄러, 1회 실행 후 종료)

사용법:
    # APScheduler 데몬 모드 (상시 구동)
    python -m src.scheduler --mode daemon

    # 1회 증분 수집 (cronjob에서 호출)
    python -m src.scheduler --mode once --site rocketpunch

    # 커스텀 스케줄
    python -m src.scheduler --mode daemon --cron "0 6 * * *"

환경변수:
    CRAWL_SCHEDULE    - cron 표현식 (기본: "0 6 * * *" = 매일 06:00)
    CRAWL_SITES       - 수집 대상 사이트 (콤마 구분, 기본: "rocketpunch")
    CRAWL_PAGES       - 수집 페이지 범위 (기본: "1-10")
    CRAWL_DELAY       - 요청 간격 초 (기본: 5)
"""

import argparse
import json
import hashlib
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.logger import setup_logger
from src.models.job_posting import save_to_json
from src.utils.fault_detector import FaultDetector

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────
# 증분 수집 관리
# ────────────────────────────────────────────

DEDUP_DIR = Path("data/.dedup")


def _posting_hash(title: str, company: str) -> str:
    """공고의 고유 해시를 생성한다 (제목 + 회사명 기반)."""
    raw = f"{title.strip().lower()}|{company.strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def load_seen_hashes(site: str) -> set[str]:
    """이전에 수집한 공고의 해시 목록을 로드한다."""
    DEDUP_DIR.mkdir(parents=True, exist_ok=True)
    hash_file = DEDUP_DIR / f"{site}_seen.jsonl"

    if not hash_file.exists():
        return set()

    hashes = set()
    with open(hash_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entry = json.loads(line)
                    hashes.add(entry.get("hash", ""))
                except json.JSONDecodeError:
                    continue

    logger.info(f"[scheduler] Loaded {len(hashes)} seen hashes for {site}")
    return hashes


def save_seen_hashes(site: str, new_items: list[dict]):
    """새로 수집한 공고의 해시를 기록한다."""
    DEDUP_DIR.mkdir(parents=True, exist_ok=True)
    hash_file = DEDUP_DIR / f"{site}_seen.jsonl"

    with open(hash_file, "a", encoding="utf-8") as f:
        for item in new_items:
            h = _posting_hash(
                item.get("title", ""),
                item.get("company_name", ""),
            )
            entry = {
                "hash": h,
                "title": item.get("title", "")[:60],
                "company": item.get("company_name", "")[:30],
                "seen_at": datetime.now().isoformat(),
            }
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    logger.info(f"[scheduler] Saved {len(new_items)} new hashes for {site}")


def filter_new_items(items: list[dict], seen: set[str]) -> list[dict]:
    """이미 수집한 공고를 제외하고 신규 공고만 반환한다."""
    new_items = []
    for item in items:
        h = _posting_hash(
            item.get("title", ""),
            item.get("company_name", ""),
        )
        if h not in seen:
            new_items.append(item)
            seen.add(h)

    logger.info(
        f"[scheduler] Dedup: {len(items)} total → {len(new_items)} new "
        f"({len(items) - len(new_items)} duplicates)"
    )
    return new_items


# ────────────────────────────────────────────
# 증분 수집 실행
# ────────────────────────────────────────────

def run_incremental(
    site: str = "rocketpunch",
    pages: str = "1-10",
    delay: float = 5.0,
    keywords: str = "",
    fetch_details: bool = False,
    headless: bool = True,
    real_chrome: Optional[bool] = None,
) -> dict:
    """
    증분 수집을 실행한다.

    기존 데이터와 비교하여 신규 공고만 저장.

    Returns:
        {"total_found": N, "new_items": N, "duplicates": N, "errors": N,
         "output_file": "path/to/file.json"}
    """
    from src.crawlers.rocketpunch import RocketPunchCrawler

    logger.info(f"[scheduler] Incremental crawl: site={site} pages={pages}")

    # 장애 탐지기 초기화
    detector = FaultDetector(site=site)

    # 기존 해시 로드
    seen = load_seen_hashes(site)

    # 크롤러 실행
    if "-" in pages:
        start, end = pages.split("-", 1)
        start_page, end_page = int(start), int(end)
    else:
        start_page = end_page = int(pages)

    if site == "rocketpunch":
        crawler = RocketPunchCrawler(
            crawl_delay=delay,
            keywords=keywords,
            headless=headless,
            real_chrome=real_chrome,
        )
    else:
        logger.error(f"Unknown site: {site}")
        return {"error": f"Unknown site: {site}"}

    # 목록 수집
    all_items = []
    errors = 0
    should_abort = False

    for page_num in range(start_page, end_page + 1):
        if should_abort:
            break

        crawler.rate_limiter.wait()
        response = crawler.fetch_list(page_num)

        if response is None:
            errors += 1
            crawler.rate_limiter.on_error()

            # 장애 탐지: HTTP 에러 확인
            http_check = detector.check_http_response(
                status_code=0, url=f"{site}/page/{page_num}"
            )
            if http_check.get("fault") and http_check["fault"].severity == "critical":
                logger.error(
                    f"[scheduler] Critical fault detected, aborting crawl"
                )
                should_abort = True
            continue

        # 장애 탐지: HTTP 응답 확인
        status_code = getattr(response, "status", 200)
        http_check = detector.check_http_response(status_code=status_code)

        if not http_check["ok"]:
            wait_secs = http_check.get("wait_seconds", 0)
            if wait_secs > 0:
                logger.warning(
                    f"[scheduler] Network block detected, waiting {wait_secs}s"
                )
                # 장시간 대기는 스킵하고 결과에 기록
                if wait_secs > 60:
                    should_abort = True
                    continue
                time.sleep(min(wait_secs, 60))

        crawler.rate_limiter.on_success()
        items = crawler.parse_list(response)

        # 장애 탐지: 파싱 결과 확인
        html_content = getattr(response, "text", "") or ""
        parse_check = detector.check_parse_result(
            items, html_content=html_content, page_num=page_num
        )

        if not parse_check["ok"] and parse_check.get("suggestion") == "regex_fallback":
            logger.warning(
                f"[scheduler] Selector may be broken on page {page_num}. "
                f"HTML snapshot saved for debugging."
            )
            # regex fallback은 크롤러 내부에서 이미 시도함
            # critical이면 중단
            if parse_check.get("fault") and parse_check["fault"].severity == "critical":
                should_abort = True
                continue

        all_items.extend(items)
        logger.info(f"[scheduler] Page {page_num}: {len(items)} items")

        # 조기 종료: 이 페이지의 모든 공고가 이미 수집된 경우
        page_new = [
            i for i in items
            if _posting_hash(i.get("title", ""), i.get("company_name", ""))
            not in seen
        ]
        if items and not page_new:
            logger.info(
                f"[scheduler] Page {page_num}: all duplicates, "
                f"stopping early (no new postings)"
            )
            break

    # 증분 필터
    new_items = filter_new_items(all_items, seen)

    # 장애 탐지: 데이터 품질 확인 (과거 평균 대비)
    history = get_run_history(site, limit=10)
    if history:
        avg_found = sum(
            h.get("total_found", 0) for h in history
        ) / len(history)
        detector.check_data_quality(
            new_count=len(all_items),
            historical_avg=avg_found,
            page_count=end_page - start_page + 1,
        )

    # 장애 리포트 생성
    fault_report = detector.get_report()

    # 저장
    result = {
        "total_found": len(all_items),
        "new_items": len(new_items),
        "duplicates": len(all_items) - len(new_items),
        "errors": errors,
        "output_file": "",
        "timestamp": datetime.now().isoformat(),
        "health": {
            "faults": fault_report["total_faults"],
            "criticals": fault_report["criticals"],
            "warnings": fault_report["warnings"],
        },
    }

    if new_items:
        # 신규 해시 기록
        save_seen_hashes(site, new_items)

        # JSON 저장
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = Path("data")
        output_dir.mkdir(exist_ok=True)
        output_file = output_dir / f"{site}_incr_{ts}.json"

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(new_items, f, ensure_ascii=False, indent=2)

        result["output_file"] = str(output_file)
        logger.info(f"[scheduler] Saved {len(new_items)} new items → {output_file}")
    else:
        logger.info("[scheduler] No new items to save")

    # 실행 기록
    _log_run_history(site, result)

    # 장애 요약 로그
    if fault_report["total_faults"] > 0:
        logger.warning(
            f"[scheduler] Fault summary: "
            f"{fault_report['criticals']} critical, "
            f"{fault_report['warnings']} warning"
        )

    return result


def _log_run_history(site: str, result: dict):
    """수집 실행 기록을 저장한다."""
    history_file = Path("data/.dedup") / f"{site}_history.jsonl"
    history_file.parent.mkdir(parents=True, exist_ok=True)

    with open(history_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")


def get_run_history(site: str, limit: int = 30) -> list[dict]:
    """최근 수집 실행 기록을 반환한다."""
    history_file = Path("data/.dedup") / f"{site}_history.jsonl"
    if not history_file.exists():
        return []

    entries = []
    with open(history_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    return entries[-limit:]


# ────────────────────────────────────────────
# APScheduler 데몬
# ────────────────────────────────────────────

def start_daemon(
    cron_expr: str = "0 6 * * *",
    sites: str = "rocketpunch",
    pages: str = "1-10",
    delay: float = 5.0,
):
    """
    APScheduler 데몬 모드로 상시 구동한다.

    cron_expr: "분 시 일 월 요일" (기본: 매일 06:00)
    """
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.error(
            "APScheduler not installed. Run: pip install apscheduler\n"
            "Or use cronjob mode: python -m src.scheduler --mode once"
        )
        sys.exit(1)

    # cron 파싱
    parts = cron_expr.split()
    if len(parts) != 5:
        logger.error(f"Invalid cron expression: {cron_expr}")
        sys.exit(1)

    minute, hour, day, month, day_of_week = parts

    scheduler = BlockingScheduler()

    def job():
        for site in sites.split(","):
            site = site.strip()
            if site:
                logger.info(f"[daemon] Scheduled run: {site}")
                try:
                    result = run_incremental(
                        site=site, pages=pages, delay=delay
                    )
                    logger.info(
                        f"[daemon] {site}: {result.get('new_items', 0)} new, "
                        f"{result.get('duplicates', 0)} dup"
                    )
                except Exception as e:
                    logger.error(f"[daemon] {site} error: {e}")

    trigger = CronTrigger(
        minute=minute, hour=hour, day=day,
        month=month, day_of_week=day_of_week,
    )
    scheduler.add_job(job, trigger, id="crawl_job", name="Incremental Crawl")

    # 시작 시 즉시 1회 실행
    logger.info(f"[daemon] Running initial crawl...")
    job()

    logger.info(
        f"[daemon] Scheduler started (cron: {cron_expr})"
    )
    logger.info("[daemon] Press Ctrl+C to stop")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("[daemon] Scheduler stopped")


# ────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Incremental crawl scheduler"
    )
    parser.add_argument(
        "--mode", choices=["once", "daemon"], default="once",
        help="once: single run (for cronjob), daemon: APScheduler loop",
    )
    parser.add_argument("--site", default="rocketpunch")
    parser.add_argument("--pages", default="1-10")
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--keywords", default="")
    parser.add_argument(
        "--cron", default="0 6 * * *",
        help="Cron expression for daemon mode (default: daily 06:00)",
    )
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    setup_logger(level=level)

    if args.mode == "once":
        result = run_incremental(
            site=args.site,
            pages=args.pages,
            delay=args.delay,
            keywords=args.keywords,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.mode == "daemon":
        start_daemon(
            cron_expr=args.cron,
            sites=args.site,
            pages=args.pages,
            delay=args.delay,
        )


if __name__ == "__main__":
    main()
