"""
LLM 기반 데이터 정제 모듈

크롤링으로 수집한 채용공고 데이터를 LLM으로 정제/분류한다.

지원 모드:
    1. REFINE  - 파싱 보완 (누락 필드 추출, 오류 수정)
    2. CLASSIFY - 허위 공고 판별 (의심 점수 + 근거)
    3. EXTRACT  - HTML 청크에서 구조화 데이터 직접 추출 (파서 폴백)

사용법:
    from src.utils.llm_refiner import LLMRefiner

    refiner = LLMRefiner(provider="openai", model="gpt-4o-mini")
    result = refiner.classify_posting(posting_dict)
    result = refiner.refine_posting(posting_dict)
    result = refiner.extract_from_html(html_chunk)

환경변수:
    OPENAI_API_KEY   - OpenAI API 키
    ANTHROPIC_API_KEY - Anthropic API 키
    LLM_PROVIDER     - 기본 프로바이더 ("openai" | "anthropic")
    LLM_MODEL        - 기본 모델명
"""

import os
import json
import logging
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ClassificationResult:
    """허위 공고 판별 결과."""
    posting_id: str
    is_suspicious: bool            # True = 의심
    confidence: float              # 0.0 ~ 1.0
    reasons: list[str]             # 의심 근거
    category: str = ""             # "normal" | "suspicious" | "scam" | "unclear"
    raw_response: str = ""         # LLM 원본 응답


@dataclass
class RefinedPosting:
    """정제된 공고 데이터."""
    posting_id: str
    refined_fields: dict           # 보완/수정된 필드
    corrections: list[str]         # 수정 사항 설명
    raw_response: str = ""


# ─────────────────────────────────────────────
# 프롬프트 템플릿
# ─────────────────────────────────────────────

CLASSIFY_PROMPT = """You are a job posting fraud detector for Korean job listings.

Analyze the following job posting and determine if it's potentially fraudulent or suspicious.

Common fraud indicators:
- Vague job descriptions with unrealistically high pay
- Requesting money or personal financial info upfront
- No clear company information or fake company details
- "Investment partner" or "business acquisition" disguised as job postings
- MLM/pyramid scheme patterns
- Overseas work with suspicious conditions
- Requesting personal documents (passport, ID) before interview

Job Posting:
{posting_json}

Respond in JSON format:
{{
    "is_suspicious": true/false,
    "confidence": 0.0-1.0,
    "category": "normal" | "suspicious" | "scam" | "unclear",
    "reasons": ["reason1", "reason2", ...]
}}
"""

REFINE_PROMPT = """You are a data extraction specialist for Korean job postings.

The following job posting data was extracted by a web crawler and may have missing or incorrect fields.
Please review and fill in any missing information that can be inferred from the available data.

Current data:
{posting_json}

If HTML context is available:
{html_context}

Respond in JSON format with only the fields that need updating:
{{
    "refined_fields": {{
        "field_name": "corrected_value",
        ...
    }},
    "corrections": ["what was changed and why", ...]
}}
"""

EXTRACT_PROMPT = """You are a structured data extractor for Korean job posting websites.

Extract job posting information from the following HTML chunk.
This is from rocketpunch.com job listings page.

HTML:
{html_chunk}

Extract and respond in JSON format:
{{
    "title": "job title",
    "company_name": "company name",
    "category": "job category/tags",
    "location": "work location if mentioned",
    "experience": "experience requirement if mentioned",
    "salary": "salary info if mentioned",
    "employment_type": "employment type if mentioned"
}}
"""


class LLMRefiner:
    """
    LLM 기반 데이터 정제기.

    Args:
        provider: "openai" 또는 "anthropic"
        model: 모델명 (기본: provider에 따라 자동 선택)
        api_key: API 키 (미지정 시 환경변수에서 읽음)
    """

    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.provider = provider or os.getenv("LLM_PROVIDER", "openai")
        self.api_key = api_key

        if self.provider == "openai":
            self.model = model or os.getenv("LLM_MODEL", "gpt-4o-mini")
            self.api_key = self.api_key or os.getenv("OPENAI_API_KEY", "")
        elif self.provider == "anthropic":
            self.model = model or os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")
            self.api_key = self.api_key or os.getenv("ANTHROPIC_API_KEY", "")
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")

        if not self.api_key:
            logger.warning(
                f"[llm_refiner] No API key for {self.provider}. "
                f"Set {'OPENAI_API_KEY' if self.provider == 'openai' else 'ANTHROPIC_API_KEY'}"
            )

    def _call_llm(self, prompt: str) -> str:
        """LLM API를 호출한다."""
        if not self.api_key:
            raise RuntimeError(f"No API key configured for {self.provider}")

        if self.provider == "openai":
            return self._call_openai(prompt)
        elif self.provider == "anthropic":
            return self._call_anthropic(prompt)
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")

    def _call_openai(self, prompt: str) -> str:
        """OpenAI API 호출."""
        try:
            import openai
            client = openai.OpenAI(api_key=self.api_key)
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that responds only in JSON."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            return response.choices[0].message.content
        except ImportError:
            raise RuntimeError("openai package not installed. Run: pip install openai")

    def _call_anthropic(self, prompt: str) -> str:
        """Anthropic API 호출."""
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self.api_key)
            response = client.messages.create(
                model=self.model,
                max_tokens=1024,
                messages=[
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
            )
            return response.content[0].text
        except ImportError:
            raise RuntimeError("anthropic package not installed. Run: pip install anthropic")

    def classify_posting(self, posting: dict) -> ClassificationResult:
        """
        채용공고의 허위 여부를 판별한다.

        Args:
            posting: 공고 데이터 딕셔너리

        Returns:
            ClassificationResult
        """
        posting_json = json.dumps(posting, ensure_ascii=False, indent=2)
        prompt = CLASSIFY_PROMPT.format(posting_json=posting_json)

        try:
            raw = self._call_llm(prompt)
            data = json.loads(raw)

            return ClassificationResult(
                posting_id=posting.get("posting_id", ""),
                is_suspicious=data.get("is_suspicious", False),
                confidence=float(data.get("confidence", 0.0)),
                reasons=data.get("reasons", []),
                category=data.get("category", "unclear"),
                raw_response=raw,
            )
        except Exception as e:
            logger.error(f"[llm_refiner] classify error: {e}")
            return ClassificationResult(
                posting_id=posting.get("posting_id", ""),
                is_suspicious=False,
                confidence=0.0,
                reasons=[f"LLM error: {str(e)}"],
                category="error",
                raw_response="",
            )

    def refine_posting(
        self, posting: dict, html_context: str = ""
    ) -> RefinedPosting:
        """
        공고 데이터를 LLM으로 보완/정제한다.

        Args:
            posting: 기존 파싱된 공고 데이터
            html_context: (선택) 원본 HTML 청크

        Returns:
            RefinedPosting
        """
        posting_json = json.dumps(posting, ensure_ascii=False, indent=2)
        prompt = REFINE_PROMPT.format(
            posting_json=posting_json,
            html_context=html_context[:2000] if html_context else "N/A",
        )

        try:
            raw = self._call_llm(prompt)
            data = json.loads(raw)

            return RefinedPosting(
                posting_id=posting.get("posting_id", ""),
                refined_fields=data.get("refined_fields", {}),
                corrections=data.get("corrections", []),
                raw_response=raw,
            )
        except Exception as e:
            logger.error(f"[llm_refiner] refine error: {e}")
            return RefinedPosting(
                posting_id=posting.get("posting_id", ""),
                refined_fields={},
                corrections=[f"LLM error: {str(e)}"],
                raw_response="",
            )

    def extract_from_html(self, html_chunk: str) -> dict:
        """
        HTML 청크에서 직접 구조화 데이터를 추출한다.
        CSS 셀렉터가 완전히 실패했을 때의 최종 폴백.

        Args:
            html_chunk: 개별 공고 카드의 HTML

        Returns:
            추출된 데이터 딕셔너리
        """
        prompt = EXTRACT_PROMPT.format(html_chunk=html_chunk[:3000])

        try:
            raw = self._call_llm(prompt)
            return json.loads(raw)
        except Exception as e:
            logger.error(f"[llm_refiner] extract error: {e}")
            return {}

    def batch_classify(
        self, postings: list[dict], batch_size: int = 5
    ) -> list[ClassificationResult]:
        """
        여러 공고를 일괄 판별한다.

        Args:
            postings: 공고 데이터 리스트
            batch_size: 한 번에 처리할 건수 (미사용, 향후 배치 API용)

        Returns:
            ClassificationResult 리스트
        """
        results = []
        for i, posting in enumerate(postings):
            logger.info(f"[llm_refiner] classifying {i+1}/{len(postings)}")
            result = self.classify_posting(posting)
            results.append(result)

        suspicious_count = sum(1 for r in results if r.is_suspicious)
        logger.info(
            f"[llm_refiner] batch classify done: "
            f"{len(results)} total, {suspicious_count} suspicious"
        )
        return results
