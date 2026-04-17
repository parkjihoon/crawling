# Crawling

허위 고용정보(취업사기) 수집을 위한 채용공고 크롤링 시스템.
[Scrapling](https://github.com/D4Vinci/Scrapling) 기반으로 anti-bot 보호가 적용된 사이트도 수집 가능.

## 빠른 시작

```bash
git clone https://github.com/parkjihoon/crawling.git
cd crawling
pip install -r requirements.txt
python -m patchright install chromium
```

```bash
# 로켓펀치 최신 공고 수집 (1~3페이지)
python main.py --site rocketpunch --pages 1-3 --delay 5

# Windows에서 'spawn UNKNOWN' 에러가 나면 시스템 Chrome 사용
python main.py --site rocketpunch --pages 1-3 --real-chrome
```

## 지원 사이트

| 사이트 | 상태 | Fetcher |
|--------|------|---------|
| [로켓펀치](https://www.rocketpunch.com/jobs) | Phase 1 | StealthyFetcher |
| (추가 예정) | Phase 2 | - |

## 프로젝트 구조

```
crawling/
├── main.py                 # CLI 엔트리포인트
├── src/
│   ├── crawlers/           # 사이트별 크롤러
│   ├── utils/              # robots.txt, 속도제한, 세션
│   └── models/             # 데이터 모델
├── manual/                 # 상세 매뉴얼
├── data/                   # 수집 데이터 (git 제외)
└── logs/                   # 로그 (git 제외)
```

## 매뉴얼

| 문서 | 설명 |
|------|------|
| [01-overview.md](manual/01-overview.md) | 프로젝트 개요, 요건, 사이트 분석 |
| [02-architecture.md](manual/02-architecture.md) | 아키텍처, 모듈 구조, 데이터 흐름 |
| [03-robots-policy.md](manual/03-robots-policy.md) | robots.txt 정책, 사이트별 분석 |
| [04-development.md](manual/04-development.md) | 개발 가이드, 새 크롤러 추가 방법 |
| [05-setup.md](manual/05-setup.md) | 설치, 환경 설정, 트러블슈팅 |
| [06-operations.md](manual/06-operations.md) | 운영, 스케줄링, 모니터링 |
| [99-session-log.md](manual/99-session-log.md) | 세션 로그 (재개 진단 기록, 트러블슈팅 근거) |

## 주의사항

- 일반 IP 환경에서 실행 (클라우드 IP는 CloudFront 차단)
- 요청 간격 최소 5초 유지 (robots.txt 준수)
- 개인정보 수집에 주의
