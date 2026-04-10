# 06. 운영 가이드

## 일상 운영

### 기본 수집 실행

```bash
# 매일 실행: 최신 공고 10페이지 수집
python main.py --site rocketpunch --pages 1-10 --delay 5 --output both

# 결과 확인
ls -la data/
cat logs/crawl_$(date +%Y-%m-%d).log | tail -20
```

### 키워드 기반 수집

```bash
# 특정 키워드로 검색하여 수집
python main.py --site rocketpunch --pages 1-5 --keywords "재택" --output json
python main.py --site rocketpunch --pages 1-5 --keywords "고수익" --output json
```

### 상세 정보 포함 수집

```bash
# 목록 + 상세 페이지 (느리지만 더 많은 정보)
python main.py --site rocketpunch --pages 1-3 --detail --delay 8
```

## 스케줄링 (cron)

### Linux crontab

```bash
# 매일 오전 6시에 실행
crontab -e
0 6 * * * cd /path/to/crawling && /path/to/venv/bin/python main.py --site rocketpunch --pages 1-10 --output both >> logs/cron.log 2>&1
```

### 스크립트 방식

```bash
#!/bin/bash
# run_daily.sh
cd "$(dirname "$0")"
source venv/bin/activate

DATE=$(date +%Y-%m-%d)
echo "[$DATE] 크롤링 시작" >> logs/cron.log

python main.py \
    --site rocketpunch \
    --pages 1-10 \
    --delay 5 \
    --output both \
    >> logs/cron.log 2>&1

echo "[$DATE] 크롤링 완료" >> logs/cron.log
```

## 로그 관리

### 로그 위치

- `logs/crawl_YYYY-MM-DD.log`: 일별 크롤링 로그
- 콘솔 출력과 파일 출력 동시 지원

### 로그 레벨

```bash
# 기본 (INFO)
python main.py --site rocketpunch --pages 1

# 상세 (DEBUG) - 요청/파싱 세부 정보 포함
python main.py --site rocketpunch --pages 1 --verbose
```

### 로그 형식

```
[2026-04-10 14:30:00] INFO     crawling - [rocketpunch] 크롤링 시작 (page 1~10)
[2026-04-10 14:30:05] INFO     crawling - [rocketpunch] 목록 페이지 1 요청
[2026-04-10 14:30:08] INFO     crawling - [rocketpunch] 페이지 1: 20건 발견
[2026-04-10 14:30:13] WARNING  crawling - 요청 에러 - 백오프 적용 (연속 에러: 1회)
```

### 로그 정리

```bash
# 30일 이상 된 로그 삭제
find logs/ -name "crawl_*.log" -mtime +30 -delete
```

## 데이터 관리

### 출력 파일

| 형식 | 경로 | 용도 |
|------|------|------|
| JSON | `data/{site}_{timestamp}.json` | 프로그래밍, API 연동 |
| CSV | `data/{site}_{timestamp}.csv` | 엑셀, 분석 도구 |

### 데이터 정리

```bash
# 7일 이상 된 수집 데이터 삭제
find data/ -name "*.json" -mtime +7 -delete
find data/ -name "*.csv" -mtime +7 -delete
```

## 에러 대응

### 연속 에러 발생 시

rate_limiter가 자동으로 백오프를 적용한다:
- 1회 에러: 10초 대기
- 2회 연속: 20초 대기
- 3회 연속: 40초 대기
- 최대 60초까지

성공하면 원래 간격(5초)으로 복귀.

### CloudFront 차단 시

IP가 차단된 경우:
1. 실행 환경의 IP 확인 (`curl ifconfig.me`)
2. 클라우드 IP인지 확인
3. 일반 ISP 환경으로 이동
4. 1~2시간 대기 후 재시도

### 사이트 구조 변경 시

로켓펀치가 HTML 구조를 변경하면:
1. `--no-headless --verbose` 로 실행하여 실제 DOM 확인
2. `src/crawlers/rocketpunch.py`의 CSS 셀렉터 업데이트
3. Scrapling의 `adaptive=True` 기능 활용 검토
4. `manual/04-development.md`의 셀렉터 테이블 업데이트

## 모니터링 체크리스트

일별 확인:
- [ ] 크롤링 로그에 에러가 없는지
- [ ] 수집 건수가 정상 범위인지
- [ ] data/ 디렉토리에 파일이 생성되었는지

주별 확인:
- [ ] robots.txt 변경 여부
- [ ] 사이트 구조 변경 여부
- [ ] 디스크 용량

## Phase 2 확장 시 운영 변경사항

- 사이트별 개별 스케줄 설정
- DB 저장으로 전환 (JSON/CSV → PostgreSQL 등)
- 중복 공고 필터링
- 알림 시스템 (수집 실패, 구조 변경 감지)
