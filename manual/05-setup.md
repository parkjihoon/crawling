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

```bash
python3 -c "
from scrapling.fetchers import StealthyFetcher
print('Scrapling 설치 확인 OK')
from src.utils.robots import RobotsPolicy
print('프로젝트 모듈 로드 OK')
"
```

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
