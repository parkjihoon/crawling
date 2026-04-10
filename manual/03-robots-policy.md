# 03. robots.txt 정책

## 개요

이 프로젝트는 모든 대상 사이트의 robots.txt를 **의무적으로 준수**한다.
robots.txt가 없는 사이트에는 기본(보수적) 정책을 자동 생성하여 적용한다.

## 정책 모듈: `src/utils/robots.py`

### RobotsPolicy 클래스

```python
from src.utils.robots import RobotsPolicy

policy = RobotsPolicy(user_agent="FraudJobCrawler/1.0")

# 크롤링 가능 여부 확인
if policy.can_fetch("https://www.rocketpunch.com/jobs"):
    print("크롤링 가능")

# 크롤링 간격 확인
delay = policy.get_crawl_delay("https://www.rocketpunch.com")
print(f"간격: {delay}초")

# 정책 정보 전체 확인
info = policy.get_policy_info("https://www.rocketpunch.com")
print(info)
```

### 간격(delay) 결정 우선순위

1. robots.txt에 명시된 `Crawl-delay` 값
2. 정부/공공 사이트 (`.go.kr`, `.gov` 등) → 5초
3. 기본값 → 3초
4. CLI `--delay` 인자 (명시적 지정 시 최우선)

### robots.txt 없는 사이트 기본 정책

robots.txt가 없거나 로드 실패 시 아래 정책을 자동 적용한다:

```
User-Agent: *
Allow: /
Crawl-delay: 5
```

이 기본 정책은 `save_default_robots()` 메서드로 파일로 저장하여 감사 기록을 남길 수 있다.

## 사이트별 robots.txt 분석

### rocketpunch.com (Phase 1)

```
# 핵심 요약
- /jobs 경로: 허용 (disallow 목록에 없음)
- /jobs/{id}: 허용
- Crawl-delay: 미지정 → 자체 5초 적용
- Sitemap: https://www.rocketpunch.com/sitemap.xml
```

차단된 경로 (접근 금지):
- `/login`, `/logout`, `/auth`, `/oauth`
- `/api/auth/`, `/api/og`, `/api/presign`, `/api/upload`
- `/_next/static/` (일부 CSS/JS 예외 허용)
- `/tag/`, `/people/`
- `/_analytics`, `/tracking`

차단된 User-Agent (60+개):
- Wget, SemrushBot, AhrefsBot, DotBot, MJ12bot 등
- 이 프로젝트의 User-Agent(`FraudJobCrawler/1.0`)는 차단 목록에 없음
- StealthyFetcher 사용 시 일반 Chrome UA가 적용됨

### ainews.com (참고 사례)

```
# robots.txt 구조 참고용
User-Agent: *
Disallow: /login
Sitemap: https://www.ainews.com/sitemap.xml

# 특정 봇 완전 차단
User-agent: Amazonbot
Disallow: /

# 크롤링 속도 제한
User-agent: AhrefsBot
Crawl-delay: 10
```

## 새 사이트 추가 시 robots.txt 체크리스트

CLI에서 새 사이트를 추가할 때 아래 절차를 따른다:

1. **robots.txt 확인**: `https://{domain}/robots.txt` 접근
2. **대상 경로 확인**: 크롤링할 경로가 Disallow에 포함되어 있는지 확인
3. **Crawl-delay 확인**: 명시된 값이 있으면 반드시 준수
4. **User-Agent 차단 확인**: 프로젝트 UA가 차단 목록에 있는지 확인
5. **Sitemap 활용**: sitemap.xml이 있으면 URL 수집에 활용 가능
6. **기록**: `manual/` 에 분석 결과 문서화

### robots.txt가 없는 경우

```python
# 기본 정책이 자동 적용됨
policy = RobotsPolicy()
info = policy.get_policy_info("https://no-robots-site.com")
# info["has_robots_txt"] == False
# info["crawl_delay"] == 5.0 (기본값)

# 감사 기록용 파일 저장
policy.save_default_robots("config/", "no_robots_site")
```

## 법적 참고사항

- robots.txt는 법적 구속력이 있는 것은 아니나, 웹 크롤링의 표준 규약
- 대한민국 개인정보보호법에 따라 개인정보 수집에 주의
- 사이트 이용약관도 별도로 확인 필요
- 이 프로젝트는 공개된 채용공고 정보만을 수집 대상으로 함
