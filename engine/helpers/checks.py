"""LLM 글 검사에 여러 곳이 같이 쓰는 패턴과 확인."""
import re

from engine.schemas.aggregate import AggregationResult

# [63] 후보 번호가 할 일·이유에 새어 나옴("R1 미니 인턴십"), 학기와 방학이 안 맞음("1학기 겨울방학")
ROW_REF = re.compile(r"\bR\d+\b")
TERM_MISMATCH = re.compile(r"1\s*학기\s*(중\s*)?겨울\s*방학|2\s*학기\s*(중\s*)?여름\s*방학")
NUMBER = re.compile(r"\d+")
HANJA = re.compile(r"[\u4e00-\u9fff]")  # 9B 모델이 한국어 글에 한자를 섞는 경우가 있음
PHONE = re.compile(r"(?<!\d)\d{2,4}-\d{3,4}(-\d{4})?(?!\d)|(?<![\d-])1\d{2}(?![\d-])(?=\s*(번|으로|로|에|\(|\)))")


def table_companies(agg: AggregationResult) -> set[str]:
    """집계 표 전체에 나온 기업 이름 (지어낸 기업 확인용)."""
    return {c for r in agg.skills for c in r.companies if len(c) >= 2}


def clean_phones(text: str) -> str:
    """지어낸 전화번호를 지운다. 번호는 코드의 고정 위기 안내에만 있다."""
    return PHONE.sub("", text)
