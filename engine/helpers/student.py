"""학생 입력·목표 직무·보유 항목을 LLM에 줄 글로 (여러 단계가 같은 글을 씀)."""
from engine.helpers.school import SCHOOL
from engine.schemas.aggregate import AggregationResult
from engine.schemas.job import StudentInput, TargetJob
from engine.schemas.rank import RowGap


def render_student(student: StudentInput) -> str:
    """LLM용 학생 입력 텍스트. 비어 있는 항목은 뺀다."""
    rows = [
        ("전공", student.major),
        ("학년", f"{student.grade}학년" if student.grade else None),
        ("희망 직무", student.desired_job),
        ("보유 기술", ", ".join(student.skills)),
        ("보유 자격증", ", ".join(student.certificates)),
        ("프로젝트", " / ".join(student.projects)),
        ("비교과 활동", " / ".join(student.activities)),
        ("학생 질문", student.question),
    ]
    text = "학생 입력\n" + "\n".join(f"- {k}: {v}" for k, v in rows if v)
    # [48] 업로드 문서는 본문 전체를 그대로 붙인다 (LLM은 본문 전체를 읽는다 — 자르지 않음)
    for i, d in enumerate(student.documents, 1):
        if d.text.strip():
            text += f"\n\n[업로드 문서 {i}: {d.filename}]\n{d.text.strip()}"
    return text


def make_target(job: TargetJob) -> str:
    """LLM에 줄 조사 대상 텍스트 (1단계 결과 + 학생 질문 원문)."""
    lines = [f"학교: {SCHOOL}" + (f" {job.student.major}" if job.student.major else "")]  # [33] 교내 슬롯 검색용
    if job.student.grade:  # [54] 지난 학년 과목은 추천하지 않음
        lines.append(f"학년: {job.student.grade}학년 ({job.student.grade}학년 이상 과목만 추천)")
    lines.append(f"목표 직무: {job.job_title} ({job.level})")
    for it in job.interests:
        lines.append(f"세부 관심 분야: {it.name} (검색 키워드: {', '.join(it.keywords)})")
    for c in job.companies:  # [25] 학생 표기 대신 공식명과 검색 키워드를 넘긴다
        lines.append(f"관심 기업: {c.name} (검색 키워드: {', '.join(c.keywords)})")
    lines.append(f"직무 검색 키워드: {', '.join(job.search_keywords)}")
    if job.student.question:
        lines.append(f"학생 질문: {job.student.question}")
    return "\n".join(lines)


def render_held(agg: AggregationResult, held: list[RowGap]) -> str:
    job = agg.job
    lines = []
    if job.skills:
        lines.append("보유 기술: " + ", ".join(h.name for h in job.skills))
    if job.certificates:
        lines.append("자격증: " + ", ".join(h.name for h in job.certificates))
    if job.projects:
        lines.append("해 본 프로젝트: " + ", ".join(h.name for h in job.projects))
    if job.student.activities:
        lines.append("참여한 활동: " + ", ".join(job.student.activities))
    if held:
        lines.append("근거에 나왔지만 이미 가진 것: " + ", ".join(dict.fromkeys(g.skill for g in held)))
    return "\n".join(lines) or "없음"
