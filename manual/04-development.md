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
        from scrapling.fetchers import StealthyFetcher  # 또는 Fetcher
        url = f"https://target-site.com/jobs?page={page}"
        try:
            return StealthyFetcher.fetch(url, headless=True)
        except Exception as e:
            self.logger.error(f"fetch_list 에러: {e}")
            return None

    def parse_list(self, response) -> list[dict]:
        """목록에서 공고 정보 추출"""
        items = []
        job_cards = response.css(".job-card")  # 사이트에 맞게 수정
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

### 파싱 셀렉터 (업데이트 필요 시 여기 수정)

로켓펀치는 Next.js 기반이라 클래스명이 빌드마다 변경될 수 있다.
현재 사용 중인 셀렉터 목록:

| 필드 | 우선 셀렉터 | 폴백 셀렉터 |
|------|------------|------------|
| 제목 | `h1` | `[class*='title'] h1` |
| 회사명 | `[class*='company-name']` | `[class*='CompanyName']` |
| 위치 | `[class*='location']` | `[class*='address']` |
| 급여 | `[class*='salary']` | `[class*='compensation']` |
| 경력 | `[class*='experience']` | `[class*='career']` |

셀렉터가 작동하지 않으면:
1. `--no-headless` 옵션으로 브라우저를 열어 실제 DOM 확인
2. 셀렉터 업데이트
3. Scrapling의 `adaptive=True` 기능 활용 검토

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

## 코딩 컨벤션

- 타입 힌트 사용
- docstring은 한국어로 작성
- 로거 메시지: `[site_name]` 접두어 포함
- 에러 처리: 개별 공고 실패 시 건너뛰고 계속 진행
- 모든 외부 요청은 rate_limiter를 통해 실행
