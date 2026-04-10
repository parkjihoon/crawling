# 02. 아키텍처

## 시스템 구조

```
crawling/
├── main.py                    # CLI 엔트리포인트
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
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── robots.py          # robots.txt 정책 관리
│   │   ├── rate_limiter.py    # 요청 속도 제한 + 백오프
│   │   ├── session.py         # HTTP 세션 관리 (정적 사이트 폴백용)
│   │   └── logger.py          # 로깅 유틸리티
│   └── models/
│       ├── __init__.py
│       └── job_posting.py     # 채용공고 데이터 모델 + JSON/CSV 저장
├── data/                      # 수집 데이터 (git 제외)
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

### src/models/job_posting.py - 데이터 모델

JobPosting 필드:
- `posting_id`, `title`, `company_name`
- `location`, `salary`, `experience`, `education`, `employment_type`
- `posted_date`, `closing_date`, `description`
- `source_url`, `source_site`, `crawled_at`

저장: `save_to_json()`, `save_to_csv()`

## 데이터 흐름

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
