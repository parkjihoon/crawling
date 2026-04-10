# 06. 운영 가이드

## 웹 대시보드 (권장)

### 서버 실행

```bash
# Windows
start_server.cmd

# 직접 실행
venv\Scripts\python.exe server.py
venv\Scripts\python.exe server.py --port 8080

# Linux/Mac
python server.py
```

브라우저에서 `http://localhost:5000` 접속.

### 대시보드 기능

| 메뉴 | 기능 |
|------|------|
| Dashboard | 실시간 통계, 진행 상황, 최근 로그 |
| Crawl | 크롤링 실행/중지, 파라미터 설정 |
| Results | 수집 결과 테이블 (검색/필터/정렬) |
| Logs | 실시간 로그 스트리밍 (SSE) |
| Files | 저장된 데이터 파일 관리/로드 |

### 대시보드 API 엔드포인트

```
GET  /                  - 대시보드 UI
GET  /api/status        - 크롤링 상태
POST /api/crawl/start   - 크롤링 시작 (JSON body)
POST /api/crawl/stop    - 크롤링 중지
GET  /api/results       - 수집 결과 (검색: ?q=keyword, 필터: ?filter=suspicious)
POST /api/results/load  - 파일에서 결과 로드
GET  /api/logs/stream   - SSE 로그 스트리밍
GET  /api/files         - data/ 폴더 파일 목록
POST /api/classify      - LLM 허위 공고 분류
```

### 크롤링 시작 요청 예시

```json
POST /api/crawl/start
{
    "site": "rocketpunch",
    "start_page": 1,
    "end_page": 5,
    "delay": 5.0,
    "keywords": "",
    "fetch_details": true,
    "headless": true,
    "discover_api": false
}
```

## CLI 실행

### 기본 수집

```bash
# 최신 공고 10페이지 수집
python main.py --site rocketpunch --pages 1-10 --delay 5 --output both

# 키워드 검색
python main.py --site rocketpunch --pages 1-5 --keywords "재택" --output json

# 상세 포함 (page_action으로 카드 클릭, URL 캡처)
python main.py --site rocketpunch --pages 1-3 --detail --delay 8

# API 탐색 (capture_xhr로 XHR 캡처)
python main.py --site rocketpunch --pages 1 --discover-api
```

## 동적 크롤링 옵션

### page_action (상세 URL 캡처)

`--detail` 옵션 사용 시:
1. 목록 페이지 로드 (wait_selector로 카드 렌더링 대기)
2. page_action 콜백으로 각 카드 클릭
3. SPA 네비게이션으로 변경된 URL 캡처 (`page.url`)
4. 뒤로가기 → 다음 카드 반복
5. 캡처된 URL로 상세 페이지 별도 요청

### capture_xhr (API 발견)

`--discover-api` 옵션 사용 시:
- 페이지 로드 중 발생하는 XHR/fetch 요청을 자동 캡처
- regex 패턴 매칭 (기본: `/api/.*job`)
- 캡처된 API URL + 응답 바디 로깅
- 발견된 API로 직접 호출 전환 가능 (향후)

### wait_selector (렌더링 대기)

모든 목록 요청에 적용:
- `div[data-index]` 셀렉터 대기 (가상 스크롤 카드)
- `network_idle` 대기 (API 호출 완료)
- 추가 2초 대기 (가상 스크롤 안정화)

## 스케줄링 (cron)

### Linux crontab

```bash
0 6 * * * cd /path/to/crawling && /path/to/venv/bin/python main.py --site rocketpunch --pages 1-10 --output both >> logs/cron.log 2>&1
```

### Windows Task Scheduler

1. 작업 스케줄러 → 기본 작업 만들기
2. 프로그램: `C:\path\to\crawling\venv\Scripts\python.exe`
3. 인수: `main.py --site rocketpunch --pages 1-10 --output both`
4. 시작 위치: `C:\path\to\crawling`

## 로그 관리

### 로그 위치
- `logs/crawl_YYYY-MM-DD.log`: 일별 로그 파일
- 대시보드 `/api/logs/stream`: 실시간 SSE 스트리밍
- 콘솔 출력 동시 지원

### 로그 정리

```bash
find logs/ -name "crawl_*.log" -mtime +30 -delete
find data/ -name "*.json" -mtime +7 -delete
```

## 에러 대응

### 연속 에러 시
rate_limiter 자동 백오프: 10s → 20s → 40s → 최대 60s (성공 시 5s 복귀)

### CloudFront 차단 시
1. IP 확인 (`curl ifconfig.me`)
2. 클라우드 IP면 일반 ISP 환경으로 이동
3. 1~2시간 대기 후 재시도

### 사이트 구조 변경 시
1. `--no-headless --verbose`로 실행하여 DOM 확인
2. HTML 저장: `debug_rocketpunch.cmd`
3. 로컬 테스트: `test_parse_local.py debug_page.html`
4. 셀렉터 업데이트 → `manual/04-development.md` 동시 수정

## 모니터링 체크리스트

일별:
- [ ] 대시보드에서 에러 수 확인
- [ ] 수집 건수가 정상 범위인지
- [ ] data/ 파일 생성 확인

주별:
- [ ] robots.txt 변경 여부
- [ ] 사이트 구조 변경 여부
- [ ] 디스크 용량
