"""1단계 목표 직무 설정: 직무 설정 LLM(학생 입력 → 목표 직무 JSON) → 코드(검증).

검증
- interests.original이 학생 입력에 실제로 있는지 대조 (없으면 뺌 — LLM이 관심 분야를 지어내는 것 방지)
  희망 직무와 같거나 기업명이 든 관심 분야도 뺌 (LLM이 자주 섞음)
- companies가 학생 입력에 실제로 있는지 대조 (없으면 뺌 — 학생이 말하지 않은 기업 추천 방지)
- catalog_job이 표준 직무 목록에 있는지 확인 (없으면 null)

표준 직무 목록(README: 인호 담당, 20~30개)이 아직 없어서 catalog는 선택 인자다.
목록에 없는 직무를 검색으로 보완하는 부분은 아직 없다.
"""
import json
import re

from engine.llm import LLMConfig, chat
from engine.prompts.stage1_job import SYSTEM_PROMPT, USER_REQUEST
from engine.schemas.job import StudentInput, TargetJob, TargetJobDraft

JOB_SETTER = LLMConfig(temperature=0)


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
    return "학생 입력\n" + "\n".join(f"- {k}: {v}" for k, v in rows if v)


def _norm(s: str) -> str:
    """대조용: 공백 제거, 소문자."""
    return re.sub(r"\s+", "", s).lower()


def set_target_job(student: StudentInput, catalog: list[str] | None = None) -> TargetJob:
    catalog_text = ("표준 직무 목록:\n" + "\n".join(f"- {c}" for c in catalog)) if catalog else "표준 직무 목록: 없음"
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": USER_REQUEST.format(student=render_student(student), catalog=catalog_text)}]
    msg = chat(messages, JOB_SETTER, format=TargetJobDraft.model_json_schema())
    draft = TargetJobDraft.model_validate(json.loads(msg["content"]))

    source = _norm(render_student(student))
    dropped = []
    companies = []
    for c in draft.companies:
        if _norm(c) in source:
            companies.append(c)
        else:
            dropped.append(f"기업: {c}")
    # 관심 분야로 들어오면 안 되는 것: 희망 직무 그 자체, 기업명이 든 표현 (LLM이 자주 섞음)
    not_interest = {_norm(student.desired_job or ""), _norm(draft.job_title)}
    interests = []
    for it in draft.interests:
        it.keywords = [k.strip() for k in it.keywords if k.strip()]
        if _norm(it.original) not in source:
            dropped.append(f"관심 분야: {it.original}")
        elif _norm(it.original) in not_interest or any(_norm(c) in _norm(it.original) for c in companies):
            dropped.append(f"관심 분야(직무·기업과 중복): {it.original}")
        else:
            interests.append(it)
    catalog_job = draft.catalog_job if catalog and draft.catalog_job in catalog else None

    return TargetJob(
        **draft.model_dump(exclude={"interests", "companies", "catalog_job", "search_keywords"}),
        search_keywords=[k.strip() for k in draft.search_keywords if k.strip()],
        interests=interests, companies=companies, catalog_job=catalog_job,
        student=student, dropped=dropped,
    )
