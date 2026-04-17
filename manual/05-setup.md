# 05. 설치 및 환경 설정

## 요구사항

- Python 3.10 이상
- 일반 ISP IP 환경 (클라우드 IP에서는 CloudFront 차단)
- 디스크 500MB+ (브라우저 엔진 설치)

## 설치 절차

### 1. 저장소 클론

```bash
git clone https://github.com/parkjihoon/crawling.git
cd crawling
```

### 2. 가상환경 생성 (권장)

```bash
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# 또는
venv\Scripts\activate     # Windows
```

### 3. 의존성 설치

```bash
pip install -r requirements.txt
```

### 4. 브라우저 엔진 설치

Scrapling StealthyFetcher는 Patchright(Chromium)를 사용한다.

```bash
# Patchright 브라우저 설치
python -m patchright install chromium

# (선택) DynamicFetcher용 Playwright 브라우저
python -m playwright install chromium
```

### 5. 디렉토리 생성

```bash
mkdir -p data logs
```

### 6. 설치 확인

모든 외부 의존성이 import 되는지 먼저 확인한다.

```bash
python -c "import scrapling, flask, apscheduler, yaml, patchright, curl_cffi, browserforge, msgspec, playwright; print('all imports ok')"
```

이어서 프로젝트 모듈이 import 되는지 확인한다.

```bash
python -c "
from scrapling.fetchers import StealthyFetcher
from src.crawlers.rocketpunch import RocketPunchCrawler
from src.utils.robots import RobotsPolicy
print('project modules OK')
"
```

### 7. 스모크 테스트 (Windows)

실제 브라우저가 정상적으로 뜨는지 1페이지로 빠르게 검증한다.

```bash
# (필요 시) 프로필 경로에 한글이 들어가는 환경에서는 TEMP 고정
set TEMP=C:\tmp\crawl-playwright
set TMP=C:\tmp\crawl-playwright
mkdir C:\tmp\crawl-playwright

# Chrome 자동 감지 + 1페이지 수집
python main.py --site rocketpunch --pages 1 --delay 5 --verbose
```

성공 시 로그 마지막에 `크롤링 완료: 총 N건 수집` 과 `data/rocketpunch_*.json` 파일이 생성된다.
실패 시 "Windows — `spawn UNKNOWN`" 트러블슈팅 절로 이동.

## 빠른 실행 테스트

```bash
# 로켓펀치 1페이지만 테스트 (목록만 수집, 상세X)
python main.py --site rocketpunch --pages 1 --delay 5

# 결과 확인
ls data/
cat data/rocketpunch_*.json | python -m json.tool | head -50
```

## 환경별 설정

### 로컬 PC (개발/디버깅)

```bash
# 브라우저 표시, 상세 로그
python main.py --site rocketpunch --pages 1 --no-headless --verbose
```

### 서버 (운영)

```bash
# headless 모드, 넉넉한 딜레이
python main.py --site rocketpunch --pages 1-20 --delay 8 --output both
```

### Docker (Phase 2)

Scrapling은 Docker 이미지를 제공한다. 브라우저가 사전 설치되어 있어 환경 구성이 간편하다.

```dockerfile
# Phase 2에서 Dockerfile 구성 예정
FROM python:3.10-slim
# Scrapling Docker 이미지 활용 또는 직접 구성
```

## 트러블슈팅

### CloudFront 403 에러

```
ERROR: The request could not be satisfied
```

원인: 클라우드 IP (AWS, GCP 등) 에서 실행한 경우
해결: 일반 ISP IP 환경에서 실행

### Patchright 브라우저 에러

```
Error: Browser not found
```

해결:
```bash
python -m patchright install chromium
```

### 모듈 import 에러

```
ModuleNotFoundError: No module named 'scrapling'
```

해결:
```bash
pip install -r requirements.txt
```

### robots.txt 로드 실패

네트워크 문제일 수 있다. 기본 정책이 자동 적용되며 로그에 경고가 출력된다.
```
WARNING - robots.txt 없음: ... → 기본 정책 적용
```

### `start_server.cmd` 실행 시 `No module named 'curl_cffi'`

증상:
```
[ERROR] [rocketpunch] list request error: ... - No module named 'curl_cffi'
```

원인: 과거 버전의 `start_server.cmd`는 `flask scrapling patchright`만 venv에 설치했고
`curl_cffi`, `browserforge`, `msgspec`, `apscheduler` 등은 설치하지 않았다.
Scrapling StealthyFetcher는 런타임에 `curl_cffi`를 import 하므로 크롤 시 실패.

해결:
1. 현재 버전의 `start_server.cmd`는 `pip install -r requirements.txt`로 전체 의존성을 설치한다. 최신 코드를 pull 후 재실행.
2. 수동으로도 복구 가능:
   ```cmd
   venv\Scripts\python.exe -m pip install -r requirements.txt
   venv\Scripts\python.exe -m patchright install chromium
   ```
3. venv가 아닌 시스템 Python을 쓰고 싶다면 `venv\` 디렉터리를 제거하거나
   `start_server.cmd` 대신 `python server.py` 로 직접 실행.

### venv와 시스템 Python을 혼용하지 말 것

`start_server.cmd`는 프로젝트 루트의 `venv\Scripts\python.exe` 를 사용한다.
`pip install`을 시스템 Python에서 실행하면 venv에는 반영되지 않으므로
venv 쪽에서 import 실패가 난다. 의존성 변경 시 반드시 `venv\Scripts\python.exe -m pip install ...`
로 설치할 것.

### Windows — `BrowserType.launch_persistent_context: spawn UNKNOWN`

증상:
```
[rocketpunch] list request error: ... - BrowserType.launch_persistent_context: spawn UNKNOWN
Call log:
  - <launching> C:\Users\...\ms-playwright\chromium-1208\chrome-win64\chrome.exe ...
```

원인: Scrapling 0.4.x + patchright 1.58.x + Windows 조합에서 `channel="chromium"`
(패치라이트 번들 Chromium 채널)로 `launch_persistent_context` 호출 시 Node 측
child_process 생성이 실패한다. 동일한 환경에서 `channel` 없이 호출하면 정상 동작.

해결: StealthyFetcher에 `real_chrome=True`를 넘겨 시스템 Chrome(`channel="chrome"`)을 사용한다.

```bash
# CLI로 명시
python main.py --site rocketpunch --pages 1 --real-chrome

# 환경변수로 고정
set CRAWLER_REAL_CHROME=1
python main.py --site rocketpunch --pages 1

# 혹은 환경변수로 강제 비활성 (기본은 시스템 Chrome 자동 감지)
set CRAWLER_REAL_CHROME=0
```

`RocketPunchCrawler`는 생성자에서 다음 순서로 `real_chrome`을 결정한다:
1. 명시 인자 (`real_chrome=True/False`)
2. 환경변수 `CRAWLER_REAL_CHROME` (1/true/yes/on vs 0/false/no/off)
3. 시스템 Chrome 설치 자동 감지 (Windows/macOS/Linux 기본 경로)

Chrome이 없으면 `https://www.google.com/chrome/` 에서 설치 후 재시도한다.

### Windows — 사용자 프로필 경로에 비ASCII 문자가 포함된 경우

증상: Chromium 프로필 생성 실패, `spawn UNKNOWN`, 또는 랜덤 I/O 에러.

원인: patchright가 임시 프로필을 `%TEMP%` 아래에 생성하는데, 경로에
한글/특수문자가 있으면 일부 네이티브 서브프로세스가 실패할 수 있다.

해결: 실행 전 임시 디렉토리를 ASCII 경로로 지정.

```bash
# cmd
set TEMP=C:\tmp\crawl-playwright
set TMP=C:\tmp\crawl-playwright
mkdir C:\tmp\crawl-playwright
python main.py --site rocketpunch --pages 1

# PowerShell
$env:TEMP = "C:\tmp\crawl-playwright"
$env:TMP  = "C:\tmp\crawl-playwright"
```

## Claude Code에서 이어받기

서버의 Claude Code에서 이 프로젝트를 이어받아 작업할 때:

```bash
# 1. 저장소 클론
git clone https://github.com/parkjihoon/crawling.git
cd crawling

# 2. 의존성 설치
pip install -r requirements.txt
python -m patchright install chromium

# 3. 매뉴얼 확인
cat manual/01-overview.md    # 프로젝트 개요
cat manual/02-architecture.md # 아키텍처
cat manual/04-development.md  # 개발 가이드

# 4. 테스트 실행
python main.py --site rocketpunch --pages 1 --verbose

# 5. 개발 이어서 진행
# - 파서 셀렉터 조정 (실제 DOM 확인 후)
# - 새 사이트 크롤러 추가
# - 테스트 코드 작성
```
