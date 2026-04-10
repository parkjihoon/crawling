# 04. 개발 가이드

## 기술 스택

- **Python 3.10+**
- **Scrapling**: 크롤링 프레임워크 (anti-bot 우회 내장)
- **Patchright**: StealthyFetcher 브라우저 엔진
- **Playwright**: DynamicFetcher 폴백용

## Scrapling 기본 사용법

### Fetcher 종류

```python
from scrapling.fetchers import Fetcher, StealthyFetcher, DynamicFetcher

# 1. 정적 HTTP (가장 빠름, anti-bot 없는 사이트)
page = Fetcher.get("https://example.com", stealthy_headers=True)

# 2. StealthyFetcher (anti-bot 우회, 로켓펀치에서 사용)
page = StealthyFetcher.fetch("https://www.rocketpunch.com/jobs", headless=True)

# 3. DynamicFetcher (풀 브라우저, 복잡한 JS 사이트)
page = DynamicFetcher.fetch("https://example.com", headless=True, network_idle=True)
```

### 요소 선택

```python
# CSS 셀렉터
titles = page.css("h1::text").getall()
links = page.css("a[href*='/jobs/']")

# XPath
items = page.xpath("//div[@class='job-item']")

# BeautifulSoup 스타일
divs = page.find_all("div", class_="job-card")

# 텍스트 검색
elements = page.find_by_text("지원하기", tag="button")

# 속성 접근
for link in links:
    href = link.attrib.get("href", "")
    text = link.css("::text").getall()
```

### 세션 기반 (쿠키 유지)

```python
from scrapling.fetchers import StealthySession

with StealthySession(headless=True) as session:
    # 첫 요청으로 쿠키 확보
    page1 = session.fetch("https://www.rocketpunch.com/jobs?page=1")
    # 쿠키 유지된 상태로 다음 요청
    page2 = session.fetch("https://www.rocketpunch.com/jobs?page=2")
```

## 새 크롤러 추가 방법

### 1단계: 사이트 분석

```bash
# robots.txt 확인
curl https://target-site.com/robots.txt

# 또는 Python으로
from src.utils.robots import RobotsPolicy
policy = RobotsPolicy()
info = policy.get_policy_info("https://target-site.com/jobs")
print(info)
```

### 2단계: 크롤러 파일 생성

`src/crawlers/{site_name}.py` 파일을 생성하고 `BaseCrawler`를 상속한다.

```python
"""
{사이트명} 크롤러

대상 URL: https://target-site.com/jobs
Fetcher: StealthyFetcher (anti-bot 있는 경우) 또는 Fetcher (없는 경우)
"""

from typing import Optional
from src.crawlers.base import BaseCrawler
from src.models.job_posting import JobPosting


class NewSiteCrawler(BaseCrawler):

    def __init__(self, crawl_delay: float = 5.0, **kwargs):
        super().__init__(
            site_name="new_site",
            base_url="https://target-site.com",
            crawl_delay=crawl_delay,
        )

    def fetch_list(self, page: int):
        """목록 페이지 요청"""
        from scrapling.fetchers import StealthyFetcher
        url = f"https://target-site.com/jobs?page={page}"
        try:
            return StealthyFetcher.fetch(url, headless=True)
        except Exception as e:
            self.logger.error(f"fetch_list error: {e}")
            return None

    def parse_list(self, response) -> list[dict]:
        """목록에서 공고 정보 추출"""
        items = []
        job_cards = response.css(".job-card")
        for card in job_cards:
            items.append({
                "posting_id": card.attrib.get("data-id", ""),
                "url": card.css("a::attr(href)").get(""),
                "title": card.css(".title::text").get(""),
                "company_name": card.css(".company::text").get(""),
            })
        return items

    def fetch_detail(self, url: str):
        """상세 페이지 요청"""
        from scrapling.fetchers import StealthyFetcher
        try:
            return StealthyFetcher.fetch(url, headless=True)
        except Exception:
            return None

    def parse_detail(self, response, url: str) -> Optional[JobPosting]:
        """상세 페이지 파싱"""
        return JobPosting(
            posting_id="...",
            title=response.css("h1::text").get(""),
            company_name=response.css(".company::text").get(""),
            source_url=url,
            source_site="new_site",
        )
```

### 3단계: main.py에 등록

```python
# main.py의 get_crawler() 함수에 추가
def get_crawler(site: str, **kwargs):
    if site == "rocketpunch":
        from src.crawlers.rocketpunch import RocketPunchCrawler
        return RocketPunchCrawler(...)
    elif site == "new_site":
        from src.crawlers.new_site import NewSiteCrawler
        return NewSiteCrawler(...)
```

### 4단계: 테스트 작성

```python
# tests/test_new_site.py
def test_parse_list():
    """목록 파싱 테스트 (HTML 고정값 사용)"""
    ...

def test_parse_detail():
    """상세 파싱 테스트"""
    ...
```

## 로켓펀치 크롤러 상세

### HTML 구조 (2026-04 기준)

로켓펀치는 Next.js SSR + panda-css 기반이다. 주요 구조:

- **가상 스크롤**: `div.List#job-content` 안에 `div[data-index="N"]` 카드들
- **CSS-in-JS**: 클래스명이 CSS 속성 그대로 (e.g., `textStyle_Body.BodyS`)
- **SPA 네비게이션**: 카드에 `<a href>` 없음, React 클릭 핸들러 사용
- **보호**: AWS WAF + CloudFront CDN (클라우드 IP 차단)

### 목록 파싱 셀렉터 (2026-04 검증됨)

| 대상 | CSS 셀렉터 | 설명 |
|------|-----------|------|
| 카드 컨테이너 | `div[data-index]` | 가상 스크롤 아이템 (각 공고) |
| 회사명 | `p[class*="BodyS"]` (첫 번째) | `textStyle_Body.BodyS` + `secondary` + `lc_1` |
| 공고 제목 | `p[class*="BodyM_Bold"]` | `textStyle_Body.BodyM_Bold` + `primary` |
| 카테고리 | `p[class*="BodyS"]` (두 번째) | 회사명과 동일 셀렉터, 순서로 구분 |
| 회사 로고 | `img[alt="image"]` | Next.js image 컴포넌트 |
| 매칭 체크 | `use[href="#check-thick-outline"]` | SVG 체크 아이콘 |
| 매칭 X | `use[href="#x-circle-outline"]` | SVG X 아이콘 |
| 매칭 헤더 | `p[class*="ta_center"]` | "직군", "숙련도", "규모", "근무 방식" |

### 상세 URL 확보 방법

리스트 페이지 HTML에는 개별 공고 URL이 포함되지 않는다 (React SPA).
상세 URL을 확보하려면:

1. **API 인터셉트**: 브라우저 네트워크 탭에서 카드 클릭 시 호출되는 API 확인
2. **브라우저 자동화**: StealthyFetcher로 카드를 클릭하고 `window.location` 캡처
3. **회사 페이지 경유**: `rocketpunch.com/companies/{slug}` 에서 공고 목록 확인

현재 구현: 리스트에서 추출 가능한 데이터만 수집 (phase 1), URL은 향후 보완.

### Regex 폴백

Scrapling CSS 셀렉터가 실패할 때를 대비한 regex 폴백이 내장되어 있다:

```python
# textStyle_Body.BodyS + secondary + lc_1 → 회사명
r'textStyle_Body\.BodyS[^"]*c_foregrounds\.neutral\.secondary[^"]*lc_1">(.*?)</p>'

# textStyle_Body.BodyM_Bold + primary → 제목
r'textStyle_Body\.BodyM_Bold[^"]*c_foregrounds\.neutral\.primary">(.*?)</p>'

# 이미지 URL에서 company ID
r'image\.rocketpunch\.com/company/(\d+)/'
```

### 로컬 테스트 (HTML 파일)

실제 사이트에 반복 요청하지 않고 로컬 HTML로 테스트:

```bash
# 1. HTML 저장 (한 번만)
debug_rocketpunch.cmd

# 2. 파싱 테스트 (반복 실행 가능)
venv\Scripts\python.exe test_parse_local.py debug_page.html

# 출력: JSON 파싱 결과 + Scrapling 셀렉터 검증
```

### 디버깅

```bash
# 브라우저 표시하여 실행
python main.py --site rocketpunch --pages 1 --no-headless --verbose

# 특정 상세 페이지 테스트
python3 -c "
from src.crawlers.rocketpunch import RocketPunchCrawler
c = RocketPunchCrawler(headless=False)
resp = c.fetch_detail('https://www.rocketpunch.com/jobs/12345')
print(resp.css('h1::text').getall())
"
```

## LLM 파이프라인

### 개요

크롤링 데이터를 LLM으로 정제/분류하는 파이프라인이 `src/utils/llm_refiner.py`에 구현되어 있다.

### 모드

| 모드 | 용도 | 사용 시점 |
|------|------|----------|
| CLASSIFY | 허위 공고 판별 | 수집 후 분류 단계 |
| REFINE | 파싱 보완/수정 | 누락 필드가 많을 때 |
| EXTRACT | HTML에서 직접 추출 | CSS 셀렉터 완전 실패 시 |

### 사용법

```python
from src.utils.llm_refiner import LLMRefiner

# OpenAI 사용
refiner = LLMRefiner(provider="openai", model="gpt-4o-mini")

# Anthropic 사용
refiner = LLMRefiner(provider="anthropic", model="claude-sonnet-4-20250514")

# 허위 공고 판별
result = refiner.classify_posting({"title": "...", "company_name": "..."})
print(result.is_suspicious, result.confidence, result.reasons)

# 일괄 판별
results = refiner.batch_classify(postings_list)

# 데이터 보완
refined = refiner.refine_posting(posting_dict, html_context="<div>...</div>")

# HTML 직접 추출 (폴백)
data = refiner.extract_from_html(html_chunk)
```

### 환경변수

```
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
LLM_PROVIDER=openai          # 기본 프로바이더
LLM_MODEL=gpt-4o-mini        # 기본 모델
```

### 허위 공고 판별 기준

LLM이 체크하는 주요 지표:

- 비현실적 급여 대비 모호한 업무 설명
- 선금 또는 개인 금융 정보 요구
- 회사 정보 불명확/조작 의심
- "투자 파트너", "사업 인수" 등 채용과 무관한 내용
- MLM/다단계 패턴
- 해외 근무 조건 의심
- 면접 전 여권/신분증 요구

## 시각화 대시보드

### 실행

```bash
# 기본 (data/ 폴더 최신 파일)
python viewer.py

# 특정 파일 지정
python viewer.py --file data/rocketpunch_20260410.json

# 포트 변경
python viewer.py --port 8080

# HTML 파일로 저장 (서버 없이)
python viewer.py --save dashboard.html
```

또는 Windows에서:
```
view_results.cmd
```

### 기능

- 수집 결과 테이블 (정렬/검색/필터)
- 허위 공고 의심 하이라이트 (빨간색)
- 통계 요약 카드 (총 건수, 정상, 의심, 회사 수)
- 매칭 정보 시각화 (초록/빨강 도트)
- 다크 테마 UI

## 증분 수집 스케줄러

### 개요

`src/scheduler.py`에 구현된 증분 수집 시스템. 매일 자동으로 신규 공고만 수집한다.

### 사용법

```bash
# 1회 증분 수집 (cronjob에서 호출)
python -m src.scheduler --mode once --site rocketpunch --pages 1-10

# APScheduler 데몬 모드 (상시 구동)
python -m src.scheduler --mode daemon --cron "0 6 * * *"

# 커스텀 설정
python -m src.scheduler --mode once --site rocketpunch --pages 1-5 --delay 8 --keywords "재택"
```

### 중복 제거 메커니즘

1. `_posting_hash(title, company)`: 제목 + 회사명을 소문자 정규화 후 SHA-256 해시 생성 (앞 16자)
2. `load_seen_hashes(site)`: `data/.dedup/{site}_seen.jsonl`에서 기존 해시 로드
3. `filter_new_items(items, seen)`: 해시 비교로 신규만 필터
4. `save_seen_hashes(site, new_items)`: 신규 해시를 JSONL에 append

JSONL 포맷 (한 줄 = 한 공고):
```json
{"hash": "a1b2c3d4e5f67890", "title": "백엔드 개발자", "company": "테크컴퍼니", "seen_at": "2026-04-10T06:00:00"}
```

### 조기 종료

특정 페이지의 모든 공고가 이미 수집된 경우, 뒤 페이지는 스킵한다.
(최신순 정렬 기준으로, 이전 페이지에서 신규가 없으면 이후에도 없을 가능성 높음)

### 실행 이력

`data/.dedup/{site}_history.jsonl`에 매 실행 결과 기록:
```json
{"total_found": 80, "new_items": 12, "duplicates": 68, "errors": 0, "output_file": "data/rocketpunch_incr_20260410_060000.json", "timestamp": "2026-04-10T06:00:05", "health": {"faults": 0, "criticals": 0, "warnings": 0}}
```

## 자동 장애 탐지 (FaultDetector)

### 개요

`src/utils/fault_detector.py`에 구현된 자동 장애 탐지 및 자가 복구 모듈.
스케줄러(`run_incremental`)에 통합되어 매 수집 시 자동으로 장애를 검사한다.

### 장애 유형

| 유형 | 탐지 조건 | 자동 복구 |
|------|----------|----------|
| `selector_break` | HTML 있으나 파싱 0건, 3회 연속 시 critical | HTML 스냅샷 저장, regex fallback 권고 |
| `network_block` | HTTP 403/503/429, CloudFront 헤더 확인 | 대기 후 재시도 (최대 30분) |
| `empty_response` | HTML 500자 미만 | 지연 후 재시도 |
| `consecutive_error` | 5회 연속 실패 | 크롤링 중단 권고 |
| `data_anomaly` | 과거 평균 대비 30% 이하 또는 300% 이상 | 수동 확인 권고 |

### 사용법

```python
from src.utils.fault_detector import FaultDetector

detector = FaultDetector(site="rocketpunch")

# 파싱 결과 검증
check = detector.check_parse_result(items, html_content=html, page_num=1)
if not check["ok"]:
    recovery = detector.attempt_recovery(check["fault"])
    print(recovery["action"])  # "use_regex_fallback"

# HTTP 응답 검증
check = detector.check_http_response(status_code=403, url="...")
if check["wait_seconds"] > 0:
    time.sleep(check["wait_seconds"])

# 데이터 품질 검증
check = detector.check_data_quality(new_count=5, historical_avg=80.0)

# 건강 상태 요약
health = FaultDetector.get_health_summary("rocketpunch")
print(health)  # {"health_score": 85, "status": "healthy", ...}

# 장애 이력 조회
history = FaultDetector.load_fault_history("rocketpunch", limit=50)
```

### 장애 로그

장애 이벤트는 `data/.faults/{site}_faults.jsonl`에 기록된다:
```json
{"fault_type": "selector_break", "severity": "warning", "message": "...", "site": "rocketpunch", "timestamp": "2026-04-10T06:01:23", "details": {"page_num": 1, "html_length": 45000, "snapshot": "data/.faults/snapshots/rocketpunch_p1_20260410_060123.html"}, "auto_recovered": true, "recovery_action": "HTML snapshot saved"}
```

### HTML 스냅샷

셀렉터 파손 시 자동으로 HTML 스냅샷이 저장된다 (최대 10개):
- 경로: `data/.faults/snapshots/{site}_p{page}_{timestamp}.html`
- 활용: `python test_parse_local.py data/.faults/snapshots/{file}` 로 오프라인 디버깅

### 건강 점수

`get_health_summary(site)` → 최근 24시간 장애 이력 기반:
- critical 1건 = -20점, warning 1건 = -5점
- 80+ = healthy, 50~79 = degraded, 0~49 = unhealthy

## 코딩 컨벤션

- 타입 힌트 사용
- docstring은 한국어로 작성
- 로거 메시지: `[site_name]` 접두어 포함
- 에러 처리: 개별 공고 실패 시 건너뛰고 계속 진행
- 모든 외부 요청은 rate_limiter를 통해 실행
