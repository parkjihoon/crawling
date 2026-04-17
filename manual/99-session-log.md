# 99. 세션 로그 (2026-04-17 Claude Code 이어받기)

이 문서는 2026-04-17 Claude Code 세션에서 "이전 히스토리를 이어받아 작동 여부를 확인해 달라"는 요청을 처리한 기록이다. 코드에 남지 않는 **결정의 근거**와 **재현 가능한 진단 흐름**을 남겨 두기 위해 작성한다.

## TL;DR

- 환경: Windows 10, Python 3.13.7, 사용자 프로필 경로에 한글 포함 (`C:\Users\박지훈`).
- 증상: `python main.py --site rocketpunch --pages 1` 실행 시 `BrowserType.launch_persistent_context: spawn UNKNOWN`.
- 원인: Scrapling 0.4.6이 StealthyFetcher 세션을 시작할 때 `channel="chromium"`을 넘기는데, 이 환경의 patchright 1.58.2는 해당 채널로 `launch_persistent_context`를 띄우지 못함.
- 해결: `RocketPunchCrawler`에 `real_chrome` 옵션 신설 → `channel="chrome"` 으로 시스템 Chrome 사용. CLI `--real-chrome`, 환경변수 `CRAWLER_REAL_CHROME`, Chrome 자동 감지 3단 fallback.
- 결과: 로켓펀치 1페이지 16건 정상 수집. viewer, Flask 대시보드도 smoke 통과.

## 배경 — 왜 이 세션인가

사용자는 이전 Claude Code 세션에서 이 크롤링 프로젝트(Phase 1, 로켓펀치) 구현까지 진행했다. 최근 해당 세션을 "history resume"으로 이어가려 했을 때 응답이 돌아오지 않는 증상이 있어, 대신 현재 상태를 GitHub(`https://github.com/parkjihoon/crawling`)에 커밋해두고 신규 세션에서 이어 받기를 원했다.

요청 요지: "이전 대화는 잊고 매뉴얼 기반으로 계속 작업해라, clone 해서 지금 정상 동작하는지부터 검증해 달라." 이후 추가로 "대화 내용 자체도 md로 남겨 달라"는 요청이 들어와 본 문서가 만들어졌다.

## 작업 타임라인

### 1) 저장소 클론 & 의존성 설치

```bash
git clone https://github.com/parkjihoon/crawling.git
cd crawling
pip install -r requirements.txt
python -m patchright install chromium
```

- `scrapling 0.4.6`, `patchright 1.58.2`, `playwright 1.58.0` 등 설치.
- patchright로 `chromium-1208`, `chromium_headless_shell-1208`, `winldd-1007` 다운로드 완료.

### 2) import 검증

```bash
python -c "import scrapling, flask, apscheduler, yaml, patchright, curl_cffi, browserforge, msgspec, playwright; print('all imports ok')"
python -c "from src.crawlers.rocketpunch import RocketPunchCrawler; print('project modules ok')"
python main.py --help
python server.py --help
python viewer.py --help
```

전부 통과. `main.py --help` 출력이 cmd 기본 인코딩(cp949) 때문에 깨져 보였지만 기능은 정상. 실행 시 `PYTHONIOENCODING=utf-8`로 해결.

### 3) 실제 크롤 → 첫 실패

```bash
python main.py --site rocketpunch --pages 1 --delay 5 --verbose
```

```
[rocketpunch] list request error: https://www.rocketpunch.com/jobs?page=1&order=recent
 - BrowserType.launch_persistent_context: spawn UNKNOWN
Call log:
  - <launching> C:\Users\박지훈\AppData\Local\ms-playwright\chromium-1208\chrome-win64\chrome.exe ...
요청 에러 - 백오프 적용 (연속 에러: 1회, 다음 대기: 10.0초)
[rocketpunch] page 1 failed, skip
수집된 공고가 없습니다.
```

첫 추정: 사용자 프로필 경로에 한글(`박지훈`)이 있어 `%TEMP%` 아래에 생성되는 임시 프로필이 문제일 수 있음.

### 4) 분리 실험 — 원인 압축

임시 디렉터리를 ASCII 경로로 고정해 재시도.

```bash
mkdir C:\tmp\crawl-playwright
set TEMP=C:\tmp\crawl-playwright
set TMP=C:\tmp\crawl-playwright
python main.py --site rocketpunch --pages 1 --delay 5 --verbose
```

여전히 동일 에러. 범인은 경로가 아님.

이어서 `patchright` 직접 호출 4단계로 격리:

| 실험 | 결과 |
|-----|------|
| `p.chromium.launch(headless=True)` → example.com | OK |
| `p.chromium.launch_persistent_context(..., headless=True)` | OK |
| `p.chromium.launch_persistent_context(..., headless=True, channel="chromium")` | **FAIL: spawn UNKNOWN** |
| Scrapling `StealthyFetcher.fetch(..., real_chrome=True)` | OK (Chrome 사용) |

핵심 차이는 딱 하나 — **`channel="chromium"` 파라미터**.

### 5) Scrapling 내부 확인

`scrapling/engines/_browsers/_base.py`:

```python
self._browser_options.update({
    "args": flags,
    "headless": config.headless,
    "channel": "chrome" if config.real_chrome else "chromium",
})
```

`launch_persistent_context(channel="chromium", ...)` 가 호출됨. 이 환경의 patchright에서는 해당 채널로 persistent context를 띄우지 못함. Playwright의 `channel="chromium"`은 stable Chromium 채널을 의미하는데, 번들 바이너리(`chromium-headless-shell`/`chromium-1208`)와 결합 시 Windows에서 Node 쪽 `spawn` 호출이 `UNKNOWN` 에러로 실패하는 알려진 패턴.

시스템 Chrome은 `C:\Program Files\Google\Chrome\Application\chrome.exe`에 설치되어 있음을 확인. `real_chrome=True`로 넘기면 `channel="chrome"` 경로로 들어가 시스템 Chrome을 쓴다.

### 6) 코드 변경

**`src/crawlers/rocketpunch.py`**

- `RocketPunchCrawler.__init__`에 `real_chrome: Optional[bool] = None` 추가.
- `_resolve_real_chrome(value)` 분류 규칙:
  1. 명시 인자 우선
  2. `CRAWLER_REAL_CHROME` 환경변수 (`1/true/yes/on` ↔ `0/false/no/off`)
  3. Windows/macOS/Linux 기본 경로에서 Chrome 자동 감지
- `fetch_list`, `fetch_list_with_urls`, `fetch_list_with_xhr`, `fetch_all_cards_scrolling`, `fetch_detail` 의 `StealthyFetcher.fetch(...)` 호출에 `real_chrome=self.real_chrome` 전달.

**`main.py`**

- `--real-chrome` / `--no-real-chrome` 플래그 추가 (argparse `store_true`/`store_false`, default=`None`).
- `get_crawler()`에서 `real_chrome` 인자 전달.

**매뉴얼**

- `manual/05-setup.md` 에 두 가지 Windows 트러블슈팅 절 추가:
  - "Windows — `BrowserType.launch_persistent_context: spawn UNKNOWN`"
  - "Windows — 사용자 프로필 경로에 비ASCII 문자가 포함된 경우"
- 설치 확인/스모크 테스트 절을 현재 실제 동작하는 형태로 업데이트.
- `manual/04-development.md` 에 "브라우저 채널 (`real_chrome`) 옵션" 절 추가.
- `README.md` 빠른 시작에 `--real-chrome` 예시 한 줄 추가.

### 7) 재검증

```bash
set TEMP=C:\tmp\crawl-playwright
set TMP=C:\tmp\crawl-playwright
python main.py --site rocketpunch --pages 1 --delay 5 --verbose
```

결과:

```
[2026-04-17 14:16:32] INFO: Fetched (200) <GET https://www.rocketpunch.com/jobs?page=1&order=recent>
INFO     crawling - JSON 저장: data\rocketpunch_20260417_141632.json (16건)
INFO     crawling - 크롤링 완료: 총 16건 수집
```

추가 smoke:

```bash
python viewer.py --file data/rocketpunch_20260417_141632.json --save data/preview.html --no-browser
# → "Dashboard saved to: data/preview.html"

python server.py --port 5555
# → http://127.0.0.1:5555 에 curl 시 200
```

## 후속: `start_server.cmd` + venv 의존성 누락

같은 세션 후반에 "웹 버전에서도 수정본이 동작하느냐"는 확인 요청이 들어와 `server.py`와 `src/scheduler.py`에 `real_chrome` 파라미터를 pass-through 하고 UI에 `Browser` 드롭다운(Auto-detect / System Chrome / Bundled Chromium)을 추가했다.

이후 `start_server.cmd`로 실행하자 다음 에러가 나왔다:

```
[ERROR] [rocketpunch] list request error: ... - No module named 'curl_cffi'
```

원인은 real_chrome이나 Scrapling 내부가 아니라 **의존성 설치 범위 불일치**였다:

- 프로젝트 루트에 `venv/` 가 존재함 (Python 3.13.7)
- 기존 `start_server.cmd`는 `pip install flask scrapling patchright --quiet` 만 실행
- `curl_cffi`, `browserforge`, `msgspec`, `apscheduler` 등은 설치되지 않은 상태
- 내가 초반에 `pip install -r requirements.txt` 를 실행한 곳은 **시스템 Python**이었고 venv에는 반영되지 않음
- Scrapling StealthyFetcher는 런타임에 `from curl_cffi.requests import ...` 를 수행하므로 venv에서 실패

조치:

1. `venv\Scripts\python.exe -m pip install -r requirements.txt` 로 venv에 전체 의존성 설치
2. `start_server.cmd` 를 수정해 앞으로는 `-r requirements.txt` 로 설치하도록 변경 + patchright chromium 미설치 시 자동 설치
3. `manual/05-setup.md` 트러블슈팅에 두 절 추가 (`No module named 'curl_cffi'`, "venv와 시스템 Python 혼용")

재검증 (start_server.cmd로 실행):

- `GET /` → 200, UI에 `Browser` 드롭다운(`id="c-chrome"`)이 포함된 것 확인
- `POST /api/crawl/start` (real_chrome=null auto) → 약 60초 후 `items_found: 16`, `is_running: false`
- `POST /api/schedule/start` (real_chrome=null auto) → `total_found: 16, new_items: 16`, `data/rocketpunch_incr_*.json` 생성

## 후속: 100 사이트 대응 증분 전략 probe (로켓펀치 사례)

세션 말미에 "100+ 사이트로 확장 시 사이트마다 규칙을 따로 관리하기 어렵다 → 통일된 업데이트 판정 룰이 필요하다"는 논의로 이어졌다. 범용 전략의 1차 후보는 **`schema.org/JobPosting` JSON-LD + `sitemap.xml lastmod`** 조합. 로켓펀치가 실제로 이 전략에 부합하는지 probe로 검증했다.

### probe 결과 (2026-04-17 기준)

| 경로 | 결과 |
|------|------|
| `robots.txt` | 200, 일반 룰 허용 |
| `/sitemap.xml` | 200, sitemapindex 406 entries |
| `/sitemap_index.xml` | 404 |
| `/rss`, `/feed`, `/jobs.rss` | 200이지만 전부 Next.js HTML (실제 피드 아님) |
| `image.rocketpunch.com/sitemap/jobs-0.xml.gz` | **724 job URLs + per-URL `lastmod`** |
| 리스트 페이지 JSON-LD | **없음** (og:type=job 메타만) |
| 상세 페이지 JSON-LD | **`@type: JobPosting` 있음** (샘플 3건 모두) |

### 상세 JSON-LD 필드 (샘플 3건 공통)

- 보유: `@context`, `@type`, `title`, `description`, `identifier`, `datePosted`, `employmentType`, `experienceRequirements`, `hiringOrganization.name`, `jobLocation`, `url`
- 누락: `dateModified`, `validThrough`, `baseSalary`
- `identifier` 형식: `{"@type": "PropertyValue", "propertyID": "<company_id>", "value": "<job_id>"}`

### 전략적 함의

1. **page_action 불필요**: sitemap이 `/jobs/{N}` stable URL을 전부 공급함 → 현재 `fetch_list_with_urls()`(카드 클릭) 경로가 증분 수집에서는 불필요해질 수 있음. 초기 전수 수집에만 썼다가 이후에는 sitemap-driven.
2. **업데이트 신호는 sitemap lastmod**: JSON-LD `dateModified` 없음. 대신 sitemap 각 URL의 `<lastmod>`가 마지막 변경 시각 역할 → `lastmod > last_run_at` 인 URL만 재수집하는 증분 룰이 바로 성립.
3. **만료 감지는 "사라짐" 기반**: `validThrough` 없음. 다음 sitemap에서 URL이 빠지면 closed로 추정하는 N회 미발견 규칙 필요.
4. **급여 필드 없음**: `baseSalary` 누락 → HTML CSS 파싱 또는 `llm_refiner`로 보완.

### 100 사이트 분류 체크리스트 (제안)

| 체크 | 통과 시 | 실패 시 |
|---|---|---|
| robots.txt 허용 | 다음 단계 | 제외 |
| sitemap.xml + jobs sitemap | URL+lastmod 공짜 → 증분 트리거 | 리스트 페이지 크롤 폴백 |
| 상세 JSON-LD JobPosting | 표준 필드 자동 매핑 | CSS 셀렉터 YAML |
| JSON-LD dateModified | 업데이트 판정 직접 신호 | sitemap lastmod 대체 |
| JSON-LD validThrough | 만료 감지 | N회 미발견 규칙 |

로켓펀치는 이 기준에서 **"B급 하이브리드"** — sitemap 있음 + 상세 JSON-LD 있음 + `dateModified`/`validThrough` 없음. 다음 세션에서 `scripts/probe_site.py` 로 함수화하고 사이트 등급 분류 → 등급별 범용 크롤러를 설계할 계획.

## 남은 항목 / 후속 과제 (권장)

- **robots.txt 403**: 로켓펀치 `robots.txt`가 403으로 내려오는 상태라 `check_robots`가 기본 정책으로 폴백하고 있다. 응답 본문을 캐싱/로깅하여 운영 중 변경을 감지할 수 있도록 보강 고려.
- **상세(detail) 수집 end-to-end 미검증**: 이번 세션에서는 `--detail` 플래그로 page_action 클릭 시나리오를 검증하지 않았다. 다음 세션에서 적어도 1페이지/1건 대상 end-to-end 실행 권장.
- **`source_url` 비어 있음**: 목록만 수집한 결과에서는 `source_url`이 `""`이다. 상세 모드 또는 `fetch_list_with_urls` 경로를 거칠 때만 채워진다는 전제가 맞는지, 목록 응답에서 직접 URL을 추출할 수 있는지는 추후 재확인.
- **Phase 2 사이트 추가**: README에는 "추가 예정"으로만 표기. 후보, 우선순위, robots 정책이 결정되는 즉시 `03-robots-policy.md`와 `04-development.md`에 반영.
- **Linux/Mac 환경 검증**: 이번 세션은 Windows + 한글 사용자 경로 환경에만 해당. `channel="chromium"` 이슈가 다른 OS에서 재현되는지는 미확인.

## 재현 스크립트 (요약)

```bash
# 1. 환경 세팅
git clone https://github.com/parkjihoon/crawling.git
cd crawling
pip install -r requirements.txt
python -m patchright install chromium

# 2. 임시 디렉토리 (Windows 사용자 경로에 한글이 있는 경우 권장)
mkdir C:\tmp\crawl-playwright
set TEMP=C:\tmp\crawl-playwright
set TMP=C:\tmp\crawl-playwright

# 3. 스모크
python main.py --site rocketpunch --pages 1 --delay 5 --verbose
# 실패 시 → --real-chrome 추가
python main.py --site rocketpunch --pages 1 --real-chrome

# 4. 대시보드
python server.py --port 5000
# 또는 단일 결과 프리뷰
python viewer.py --file data/rocketpunch_YYYYMMDD_HHMMSS.json
```
