"""
채용공고 데이터 모델

수집한 채용공고 데이터의 구조를 정의한다.
"""

import json
import csv
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional
from pathlib import Path


@dataclass
class JobPosting:
    """채용공고 데이터 모델."""

    posting_id: str                         # 공고 고유 ID
    title: str                              # 공고 제목
    company_name: str                       # 회사명
    location: str = ""                      # 근무지
    salary: str = ""                        # 급여 정보
    experience: str = ""                    # 경력 조건
    education: str = ""                     # 학력 조건
    employment_type: str = ""               # 고용 형태
    posted_date: str = ""                   # 게시일
    closing_date: str = ""                  # 마감일
    description: str = ""                   # 공고 상세 내용
    source_url: str = ""                    # 원본 URL
    source_site: str = ""                   # 출처 사이트명
    crawled_at: str = field(
        default_factory=lambda: datetime.now().isoformat()
    )

    def to_dict(self) -> dict:
        """딕셔너리로 변환."""
        return asdict(self)


def save_to_json(postings: list[JobPosting], filepath: str) -> str:
    """채용공고 목록을 JSON 파일로 저장한다."""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = [p.to_dict() for p in postings]
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(path)


def save_to_csv(postings: list[JobPosting], filepath: str) -> str:
    """채용공고 목록을 CSV 파일로 저장한다."""
    if not postings:
        return filepath

    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)

    fields = list(asdict(postings[0]).keys())
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for p in postings:
            writer.writerow(p.to_dict())

    return str(path)
