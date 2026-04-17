"""
크롤링 CLI 엔트리포인트

사용법:
    # 로켓펀치 크롤링 (1~3페이지, 목록만)
    python main.py --site rocketpunch --pages 1-3

    # 상세 페이지까지 수집
    python main.py --site rocketpunch --pages 1-2 --detail

    # 키워드 검색
    python main.py --site rocketpunch --pages 1 --keywords "백엔드"

    # 커스텀 딜레이
    python main.py --site rocketpunch --pages 1-5 --delay 8

    # 출력 형식 지정
    python main.py --site rocketpunch --pages 1 --output json
    python main.py --site rocketpunch --pages 1 --output csv
"""

import argparse
import sys
import os
from datetime import datetime

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.utils.logger import setup_logger
from src.models.job_posting import save_to_json, save_to_csv


def parse_pages(pages_str: str) -> tuple[int, int]:
    """페이지 범위를 파싱한다. '1-5' → (1, 5), '3' → (3, 3)"""
    if "-" in pages_str:
        start, end = pages_str.split("-", 1)
        return int(start), int(end)
    page = int(pages_str)
    return page, page


def get_crawler(site: str, **kwargs):
    """사이트명에 해당하는 크롤러 인스턴스를 반환한다."""
    if site == "rocketpunch":
        from src.crawlers.rocketpunch import RocketPunchCrawler
        return RocketPunchCrawler(
            crawl_delay=kwargs.get("delay", 5.0),
            keywords=kwargs.get("keywords", ""),
            order=kwargs.get("order", "recent"),
            headless=kwargs.get("headless", True),
            real_chrome=kwargs.get("real_chrome", None),
        )
    else:
        raise ValueError(
            f"지원하지 않는 사이트: {site}\n"
            f"지원 사이트: rocketpunch"
        )


def main():
    parser = argparse.ArgumentParser(
        description="채용공고 크롤러 - 허위 고용정보 수집",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python main.py --site rocketpunch --pages 1-3
  python main.py --site rocketpunch --pages 1 --keywords "백엔드" --detail
  python main.py --site rocketpunch --pages 1-5 --delay 8 --output csv
        """,
    )

    parser.add_argument(
        "--site", required=True,
        choices=["rocketpunch"],
        help="크롤링 대상 사이트",
    )
    parser.add_argument(
        "--pages", default="1",
        help="페이지 범위 (예: '1-5' 또는 '3'). 기본값: 1",
    )
    parser.add_argument(
        "--detail", action="store_true",
        help="상세 페이지까지 수집 (느림, 더 많은 정보)",
    )
    parser.add_argument(
        "--keywords", default="",
        help="검색 키워드",
    )
    parser.add_argument(
        "--order", default="recent",
        choices=["recent", "score"],
        help="정렬 기준: recent(최신순), score(적합순). 기본값: recent",
    )
    parser.add_argument(
        "--delay", type=float, default=5.0,
        help="요청 간격 (초). 기본값: 5.0",
    )
    parser.add_argument(
        "--output", default="json",
        choices=["json", "csv", "both"],
        help="출력 형식. 기본값: json",
    )
    parser.add_argument(
        "--output-dir", default="data",
        help="출력 디렉토리. 기본값: data/",
    )
    parser.add_argument(
        "--no-headless", action="store_true",
        help="브라우저 창을 표시 (디버깅용)",
    )
    parser.add_argument(
        "--real-chrome", dest="real_chrome", action="store_true", default=None,
        help="시스템 Chrome 사용 (Windows+patchright에서 'spawn UNKNOWN' 회피). "
             "미지정 시 환경변수 CRAWLER_REAL_CHROME 또는 Chrome 자동 감지.",
    )
    parser.add_argument(
        "--no-real-chrome", dest="real_chrome", action="store_false",
        help="patchright 번들 chromium 강제 사용 (자동 감지 무시)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="상세 로그 출력",
    )

    args = parser.parse_args()

    # 로거 설정
    import logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logger = setup_logger(level=level)

    logger.info("=" * 60)
    logger.info("채용공고 크롤러 시작")
    logger.info(f"대상: {args.site}")
    logger.info(f"페이지: {args.pages}")
    logger.info(f"키워드: {args.keywords or '(전체)'}")
    logger.info(f"요청 간격: {args.delay}초")
    logger.info("=" * 60)

    # 페이지 범위 파싱
    start_page, end_page = parse_pages(args.pages)

    # 크롤러 생성
    crawler = get_crawler(
        args.site,
        delay=args.delay,
        keywords=args.keywords,
        order=args.order,
        headless=not args.no_headless,
        real_chrome=args.real_chrome,
    )

    # 크롤링 실행
    postings = crawler.run(
        start_page=start_page,
        end_page=end_page,
        fetch_details=args.detail,
    )

    if not postings:
        logger.warning("수집된 공고가 없습니다.")
        return

    # 결과 저장
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"{args.site}_{timestamp}"

    if args.output in ("json", "both"):
        path = save_to_json(postings, f"{args.output_dir}/{base_name}.json")
        logger.info(f"JSON 저장: {path} ({len(postings)}건)")

    if args.output in ("csv", "both"):
        path = save_to_csv(postings, f"{args.output_dir}/{base_name}.csv")
        logger.info(f"CSV 저장: {path} ({len(postings)}건)")

    logger.info(f"크롤링 완료: 총 {len(postings)}건 수집")


if __name__ == "__main__":
    main()
