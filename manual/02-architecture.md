# 02. 아키텍처

## 시스템 구조

```
crawling/
├── main.py                    # CLI 엔트리포인트
├── server.py                  # Flask 웹 대시보드 서버
├── viewer.py                  # 수집 결과 시각화 (독립 HTML 생성)
├── test_parse_local.py        # 로컬 HTML 파싱 테스트
├── start_server.cmd           # Windows 서버 실행 스크립트
├── view_results.cmd           # Windows 뷰어 실행 스크립트
├── requirements.txt           # Python 의존성
├── .gitignore
├── config/
│   └── sites/                 # (Phase 2) 사이트별 YAML 설정
├── src/
│   ├── crawlers/
│   │   ├── __init__.py
│   │   ├── base.py            # BaseCrawler 추상 클래스
│   │   └── rocketpunch.py     # 로켓펀치 크롤러 (Scrapling StealthyFetcher)
│   ├── parsers/
│   │   └── __init__.py        # (Phase 2) 별도 파서 분리 시 사용
│   ├── scheduler.py           # 증분 수집 스케줄러 (APScheduler / cronjob)
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── robots.py          # robots.txt 정책 관리
│   │   ├── rate_limiter.py    # 요청 속도 제한 + 백오프
│   │   ├── fault_detector.py  # 자동 장애 탐지 + 자가 복구
│   │   ├── llm_refiner.py     # LLM 파이프라인 (허위 공고 판별/정제)
│   │   ├── session.py         # HTTP 세션 관리 (정적 사이트 폴백용)
│   │   └── logger.py          # 로깅 유틸리티
│   └── models/
│       ├── __init__.py
│       └── job_posting.py     # 채용공고 데이터 모델 + JSON/CSV 저장
├── data/                      # 수집 데이터 (git 제외)
│   └── .dedup/                # 증분 수집 중복 제거 데이터
│       ├── {site}_seen.jsonl  #   수집 해시 기록
│       └── {site}_history.jsonl  # 실행 이력
│   └── .faults/               # 장애 탐지 로그
│       ├── {site}_faults.jsonl  # 장애 이벤트 기록
│       └── snapshots/         #   장애 시 HTML 스냅샷
├── logs/                      # 로그 파일 (git 제외)
├── tests/                     # 테스트 코드
└── manual/                    # 매뉴얼 문서
```

## Scrapling Fetcher 선택 기준

| Fetcher | 용도 | AWS WAF | JS 렌더링 | 속도 |
|---------|------|---------|-----------|------|
| `Fetcher` | 정적 HTML 사이트 | X | X | 빠름 |
| `StealthyFetcher` | anti-bot 사이트 (현재 사용) | O | O | 보통 |
| `DynamicFetcher` | 풀 브라우저 필요 시 | △ | O | 느림 |

로켓펀치는 **AWS WAF + Next.js**이므로 `StealthyFetcher`를 기본으로 사용한다.
StealthyFetcher 실패 시 DynamicFetcher로 폴백 가능.

## 모듈 설명

### main.py - CLI 엔트리포인트

```bash
# 기본 사용
python main.py --site rocketpunch --pages 1-5

# 상세 수집 + 키워드 검색
python main.py --site rocketpunch --pages 1-3 --detail --keywords "백엔드"

# CSV 출력, 딜레이 8초
python main.py --site rocketpunch --pages 1-10 --delay 8 --output csv
```

CLI 인자:
- `--site`: 대상 사이트 (필수)
- `--pages`: 페이지 범위 (`1-5` 또는 `3`)
- `--detail`: 상세 페이지 수집 여부
- `--keywords`: 검색 키워드
- `--delay`: 요청 간격 (초)
- `--output`: 출력 형식 (`json`, `csv`, `both`)
- `--no-headless`: 브라우저 표시 (디버깅용)

### src/crawlers/base.py - BaseCrawler

모든 크롤러가 상속하는 추상 클래스. Scrapling 기반.

핵심 인터페이스:
- `check_robots(url)`: robots.txt 확인
- `fetch_list(page)`: 목록 페이지 요청 → Scrapling 응답 반환
- `parse_list(response)`: 목록에서 공고 URL 추출
- `fetch_detail(url)`: 상세 페이지 요청
- `parse_detail(response, url)`: 상세 데이터 → JobPosting 반환
- `run(start_page, end_page)`: 전체 크롤링 실행

### src/crawlers/rocketpunch.py - 로켓펀치 크롤러

StealthyFetcher 기반. AWS WAF 챌린지 자동 처리.

특이사항:
- URL 쿼리 파라미터로 페이지네이션 (`?page=N`)
- Next.js SSR이므로 JS 렌더링 필수
- 클라우드 IP 차단 → 일반 IP 환경에서만 실행 가능

### src/utils/robots.py - robots.txt 정책 관리

핵심 기능:
- robots.txt 파싱 및 1시간 캐싱
- 경로별 접근 허용/차단 판단
- robots.txt 없는 사이트: 기본 정책 자동 생성 (Allow: /, Crawl-delay: 5)
- 정부/공공 사이트 자동 감지 → 보수적 딜레이 적용

### src/utils/rate_limiter.py - 요청 속도 제한

정책:
- 기본 간격: robots.txt crawl-delay 또는 5초
- 연속 에러 시 백오프: 간격 2배 증가, 최대 60초
- 성공 시 원래 간격으로 복귀

### src/scheduler.py - 증분 수집 스케줄러

매일 자동 수집을 위한 스케줄러. 두 가지 모드 지원:

- **APScheduler 데몬**: `start_daemon(cron_expr="0 6 * * *")` — 프로세스 상시 구동
- **cronjob 1회 실행**: `run_incremental(site, pages)` — OS 스케줄러에서 호출

핵심 기능:
- `_posting_hash(title, company)`: SHA-256 해시로 중복 판별
- `load_seen_hashes()` / `save_seen_hashes()`: JSONL 기반 영속 dedup 저장소
- `filter_new_items()`: 기존 해시와 비교하여 신규만 추출
- 조기 종료: 특정 페이지의 모든 공고가 중복이면 뒤 페이지 스킵
- `get_run_history()`: 최근 실행 이력 조회

### src/utils/fault_detector.py - 자동 장애 탐지

크롤링 장애를 자동으로 탐지하고 가능한 범위에서 자가 복구를 시도한다.

탐지 유형:
- **셀렉터 파손** (`selector_break`): HTML은 받았으나 파싱 0건 → regex fallback 권고, HTML 스냅샷 저장
- **네트워크 차단** (`network_block`): HTTP 403/503/429 연속 발생 → 대기 후 재시도
- **빈 응답** (`empty_response`): HTML이 없거나 극히 짧음 → 지연 후 재시도
- **연속 에러** (`consecutive_error`): N회 연속 실패 → 크롤링 중단 권고
- **데이터 이상** (`data_anomaly`): 과거 평균 대비 30% 이하 또는 300% 이상 → 구조 변경 의심

건강 점수: `get_health_summary(site)` → 0~100 점수, healthy/degraded/unhealthy 상태

### src/utils/llm_refiner.py - LLM 파이프라인

크롤링 데이터를 LLM으로 분류/정제:
- `classify_posting()`: 허위 공고 판별 (is_suspicious, confidence, reasons)
- `refine_posting()`: 누락 필드 보완
- `extract_from_html()`: HTML에서 직접 구조화 데이터 추출

### src/models/job_posting.py - 데이터 모델

JobPosting 필드:
- `posting_id`, `title`, `company_name`
- `location`, `salary`, `experience`, `education`, `employment_type`
- `posted_date`, `closing_date`, `description`
- `source_url`, `source_site`, `crawled_at`

저장: `save_to_json()`, `save_to_csv()`

## 데이터 흐름

### 수동 실행 (CLI)

```
1. main.py 실행 (CLI 인자 파싱)
   ↓
2. robots.py → rocketpunch.com/robots.txt 확인
   ↓ (접근 허용 확인)
3. rocketpunch.py → StealthyFetcher로 /jobs?page=N 요청
   ↓ (AWS WAF 챌린지 자동 처리)
   ↓ (rate_limiter: 5초 간격)
4. rocketpunch.py → parse_list() → 공고 URL 추출
   ↓
5. (--detail 옵션 시) 각 /jobs/{id} 상세 요청
   ↓ (rate_limiter: 5초 간격)
6. rocketpunch.py → parse_detail() → JobPosting 생성
   ↓
7. job_posting.py → data/ 에 JSON/CSV 저장
```

### 증분 수집 (스케줄러)

```
1. scheduler.py 실행 (APScheduler 데몬 또는 cronjob)
   ↓
2. load_seen_hashes() → data/.dedup/{site}_seen.jsonl 로드
   ↓
3. rocketpunch.py → 목록 수집 (fetch_list + parse_list)
   ↓ (FaultDetector: HTTP 응답 / 파싱 결과 검증)
   ↓ (조기 종료: 모든 공고 중복 시 스킵)
4. filter_new_items() → 해시 비교하여 신규만 추출
   ↓
5. check_data_quality() → 과거 평균 대비 이상 여부
   ↓
6. save_seen_hashes() → 신규 해시 기록
   ↓
7. data/{site}_incr_{timestamp}.json 저장
   ↓
8. _log_run_history() → 실행 결과 기록
```

## 새 사이트 추가 체크리스트

Phase 2에서 새 사이트를 추가할 때:

1. 대상 사이트의 robots.txt 확인 → `manual/` 에 분석 내용 기록
2. `src/crawlers/{site_name}.py` → BaseCrawler 상속하여 구현
3. `main.py`의 `get_crawler()` 에 사이트 등록
4. `tests/test_{site_name}.py` → 테스트 작성
5. `requirements.txt` → 추가 의존성 있으면 추가
6. `manual/` → 해당 사이트 관련 매뉴얼 추가

Fetcher 선택 기준:
- 정적 HTML → `Fetcher`
- anti-bot 보호 → `StealthyFetcher`
- 복잡한 JS 렌더링 → `DynamicFetcher`

상세한 구현 방법은 [04-development.md](04-development.md) 참고.
