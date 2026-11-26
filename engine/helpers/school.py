"""건양대 자료 읽기: 건양대 문서 판정, 교과목 코드·개설 학년·교양/전공."""
import re

from engine.helpers.text import norm_quote
from engine.schemas import EvidenceCard, EvidenceDoc

SCHOOL = "건양대학교"
SCHOOL_MARKS = ("건양", "konyang")  # 건양대 문서 판정: 제목·요약·도메인·출처·본문에 이 말이 있으면
COURSE_CODE = re.compile(r"\b\d{5}[A-Z]\b")  # 건양대 교과목 코드 (61922A, 00076K). 과목 중복 판단에 씀


def is_school_doc(doc: EvidenceDoc, text: str | None) -> bool:
    """[33] 건양대 문서인지: 제목·요약·도메인·출처·본문에 SCHOOL_MARKS가 있는지."""
    hay = " ".join([doc.title, doc.snippet or "", doc.domain or "", doc.source_name or "", text or ""]).lower()
    return any(m in hay for m in SCHOOL_MARKS)


GRADE_TEXT = re.compile(r"([1-6])\s*학년")
TERM = re.compile(r"^([1-6])\s*[-−]\s*[12]$")  # 이수시기 칸 "3-1"
# 표 제목: "교양필수교육과정일람표", "Q 전공 교육과정 일람표", "현장실습교과목일람표"
TABLE_TITLE = re.compile(r"(교양|전공|현장실습).{0,12}(일람표|교육과정)")


def course_info(text: str, quote: str) -> tuple[int | None, str | None]:
    """[54] 교과목 카드의 (개설 학년, 교양/전공)을 원문에서 코드로 읽는다. 못 읽으면 None.

    9B 모델은 표 행에서 학점 칸을 학년으로 읽는 경우가 많아(4학년 과목 2개를 3학년으로) LLM 값보다 이것을 먼저 쓴다.
    - 인용이 든 줄을 찾고, 위로 올라가며 칸 수가 같고 "학년" 칸(또는 "이수 시기" 칸)이 있는 머리글 줄을 찾아 그 칸 값을 읽는다
      (engine/rag/table.py가 빈 칸 자리까지 남겨 칸 위치가 맞음)
    - 표가 아니면 그 줄의 "3학년"
    - 교양/전공: 위쪽에서 가장 가까운 표 제목(교양… 일람표 → 교양, 전공·현장실습 → 전공)
    """
    lines = text.split("\n")
    q = norm_quote(quote)
    code = COURSE_CODE.search(quote)
    at = next((i for i, line in enumerate(lines) if q and q in norm_quote(line)), None)
    if at is None and code:
        at = next((i for i, line in enumerate(lines) if code.group() in line), None)
    if at is None:
        return None, None
    row = [c.strip() for c in lines[at].split("|")]
    grade = None
    for i in range(at - 1, max(at - 80, -1), -1):
        head = [c.strip().replace(" ", "") for c in lines[i].split("|")]
        if len(head) != len(row) or len(row) < 3:
            continue
        if "학년" in head:
            v = row[head.index("학년")]
            grade = int(v) if v.isdigit() and 1 <= int(v) <= 6 else None
            break
        if "이수시기" in head and (m := TERM.match(row[head.index("이수시기")])):
            grade = int(m.group(1))
            break
    if grade is None and (m := GRADE_TEXT.search(lines[at])):
        grade = int(m.group(1))
    course_type = None
    for i in range(at, -1, -1):
        if (m := TABLE_TITLE.search(lines[i])) and len(lines[i]) <= 40:
            course_type = "교양" if m.group(1) == "교양" else "전공"
            break
    return grade, course_type


def course_key(card: EvidenceCard) -> str:
    """[54] 같은 과목 판단: 교과목 코드가 있으면 코드, 없으면 공백 뺀 과목명."""
    m = COURSE_CODE.search(card.quote)
    return m.group() if m else norm_quote(card.item).lower()
