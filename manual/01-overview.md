# 01. 프로젝트 개요

## 목적

허위 고용정보(취업사기, 범죄 관련 공고)를 자동으로 수집하여 탐지 및 분석할 수 있는 데이터 파이프라인을 구축한다.

## 수집 대상

### Phase 1 (현재)

| 항목 | 내용 |
|------|------|
| 사이트 | 로켓펀치 (rocketpunch.com) |
| 대상 페이지 | `/jobs` (채용공고 목록) |
| 개별 공고 | `/jobs/{id}` (상세 페이지) |
| 데이터 포인트 | 공고 제목, 회사명, 근무조건, 급여, 경력, 학력, 마감일 등 |
| 목표 | 허위/사기성 공고 패턴 수집 |

### Phase 2 (예정)

- 다건의 채용 사이트로 확대
- 사이트별 robots.txt 정책에 따른 크롤링 규정 자동 적용
- robots.txt가 없는 사이트에는 기본(보수적) 정책 생성하여 적용

## 기술 스택

| 구분 | 기술 |
|------|------|
| 언어 | Python 3.10+ |
| 크롤링 프레임워크 | [Scrapling](https://github.com/D4Vinci/Scrapling) |
| 브라우저 엔진 | Patchright (StealthyFetcher), Playwright (DynamicFetcher) |
| Anti-bot 우회 | Scrapling StealthyFetcher (AWS WAF, Cloudflare 대응) |
| 데이터 저장 | JSON, CSV (Phase 2에서 DB 연동 예정) |

## 핵심 원칙

1. **robots.txt 준수**: 모든 대상 사이트의 robots.txt를 확인하고 준수한다.
2. **보수적 요청**: 요청 간격 최소 5초. 서버 부하 최소화.
3. **단계적 확장**: 사이트 추가 시 크롤러 모듈을 독립적으로 추가할 수 있는 구조.
4. **CLI 연속성**: 이 매뉴얼을 기반으로 다른 개발자 또는 CLI(Claude Code 등)가 이어서 작업 가능.

## rocketpunch.com 사이트 분석 결과

### robots.txt (2026-04-10 확인)

- `/jobs` 경로는 disallow 목록에 **포함되지 않음** → 수집 가능
- `/jobs/{id}` 개별 공고도 차단되지 않음
- Crawl-delay 미지정 → 자체 보수적 정책 적용 (5초 간격)
- 60+ 악성 봇 UA 차단 목록 존재 → 커스텀 UA 사용 시 주의
- Sitemap: `https://www.rocketpunch.com/sitemap.xml`

차단된 경로:
- `/login`, `/logout`, `/auth`, `/oauth` (인증 관련)
- `/api/auth/`, `/api/og`, `/api/presign` 등 (API 내부)
- `/_next/static/` (Next.js 빌드 자원, 일부 허용)
- `/tag/`, `/people/` (삭제된 콘텐츠)

### 사이트 기술 특성

- **프레임워크**: Next.js (React SSR)
- **보안**: AWS WAF (Web Application Firewall) + CloudFront CDN
- **렌더링**: 클라이언트 사이드 렌더링 의존 (JS 실행 필수)
- **페이지네이션**: URL 쿼리 파라미터 `?page=N`
- **정렬**: `?order=recent` (최신순), `?order=score` (적합순)

### 실행 환경 제약

- **클라우드 IP 차단**: AWS, GCP 등 클라우드 IP 대역에서 CloudFront 403 차단
- **일반 IP 필수**: 로컬 PC, 사무실 서버 등 일반 ISP IP에서 실행해야 함
- **브라우저 엔진 필수**: 단순 HTTP 요청으로는 WAF 챌린지 통과 불가

## 다음 단계

- [02-architecture.md](02-architecture.md): 시스템 아키텍처 및 모듈 구조
- [03-robots-policy.md](03-robots-policy.md): robots.txt 정책 모듈 상세
- [04-development.md](04-development.md): 개발 가이드 (크롤러 추가 방법)
- [05-setup.md](05-setup.md): 설치 및 환경 설정
- [06-operations.md](06-operations.md): 운영 가이드
