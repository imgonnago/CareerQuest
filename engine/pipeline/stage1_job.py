"""1단계 목표 직무 설정: 직무 설정 LLM(학생 입력 → 목표 직무 JSON) → 관심 분야 확인 LLM(관심/경험) → 코드(검증).

검증
- interests.original이 학생 입력에 실제로 있는지 대조 (없으면 뺌 — LLM이 관심 분야를 지어내는 것 방지)
  희망 직무와 같거나 기업명이 든 관심 분야도 뺌 (LLM이 자주 섞음)
- companies.original이 학생 입력에 실제로 있는지 대조 (없으면 뺌 — 학생이 말하지 않은 기업 추천 방지)
  공식명(name)은 대조하지 않는다 — 학생 표기와 달라도 되는 칸 ([25])
- skills·certificates·projects(보유 항목)의 original도 학생 입력과 대조하고, 폼 칸(StudentInput) 항목을 합친다 ([24])
  이미 한 것(보유 항목)과 겹치는 관심 분야는 뺌
- 관심 분야 확인 LLM(새 대화)이 interests를 관심/경험으로 다시 판정하고, 경험이면 projects로 옮김 ([24])
- catalog_job이 표준 직무 목록에 있는지 확인 (없으면 null)

표준 직무 목록(README: 인호 담당, 20~30개)이 아직 없어서 catalog는 선택 인자다.
목록에 없는 직무를 검색으로 보완하는 부분은 아직 없다.
"""
import json
import re

from engine.llm import LLMConfig, chat
from engine.prompts.stage1_job import CHECK_REQUEST, CHECK_SYSTEM, SYSTEM_PROMPT, USER_REQUEST
from engine.schemas.job import Held, Interest, InterestCheck, StudentInput, TargetJob, TargetJobDraft

JOB_SETTER = LLMConfig(temperature=0)
INTEREST_CHECKER = LLMConfig(temperature=0)  # [24] 관심 분야 확인 (관심/경험)


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


def _keywords(keywords: list[str]) -> list[str]:
    """빈 키워드를 빼고 앞뒤 공백을 지운다."""
    return [k.strip() for k in keywords if k.strip()]


def _held(drafts: list[Held], form: list[str], source: str, label: str, dropped: list[str]) -> list[Held]:
    """[24] 보유 항목: LLM이 뽑은 것 중 원문이 학생 입력에 있는 것 + 폼 칸 항목(LLM이 빠뜨린 것만 추가)."""
    out = []
    for h in drafts:
        if _norm(h.original) in source:
            out.append(h)
        else:
            dropped.append(f"{label}: {h.original}")
    for x in form:
        if not any(_norm(x) in (_norm(h.original), _norm(h.name)) for h in out):
            out.append(Held(original=x, name=x))
    return out


def check_interests(student: StudentInput, interests: list[Interest]) -> set[int]:
    """[24] 관심 분야 확인 LLM: interests 중 학생이 이미 해 본 것(경험)의 번호(0부터)를 돌려준다."""
    if not interests:
        return set()
    items = "\n".join(f"K{i} {it.original}" for i, it in enumerate(interests, 1))
    messages = [{"role": "system", "content": CHECK_SYSTEM},
                {"role": "user", "content": CHECK_REQUEST.format(student=render_student(student), items=items)}]
    msg = chat(messages, INTEREST_CHECKER, format=InterestCheck.model_json_schema())
    return {int(x.item[1:]) - 1 for x in InterestCheck.model_validate(json.loads(msg["content"])).items
            if x.status == "경험"}


def set_target_job(student: StudentInput, catalog: list[str] | None = None) -> TargetJob:
    catalog_text = ("표준 직무 목록:\n" + "\n".join(f"- {c}" for c in catalog)) if catalog else "표준 직무 목록: 없음"
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": USER_REQUEST.format(student=render_student(student), catalog=catalog_text)}]
    msg = chat(messages, JOB_SETTER, format=TargetJobDraft.model_json_schema())
    draft = TargetJobDraft.model_validate(json.loads(msg["content"]))

    source = _norm(render_student(student))
    dropped = []
    # [25] 기업은 학생 표기(original)로 대조하고, 검색에는 LLM이 고친 공식명(name)·키워드를 쓴다
    companies = []
    for c in draft.companies:
        if _norm(c.original) in source:
            c.keywords = _keywords(c.keywords) or [c.name]
            companies.append(c)
        else:
            dropped.append(f"기업: {c.original}")
    # [24] 보유 항목(기술·자격증·프로젝트)도 원문 대조. 폼 칸에 적힌 것은 LLM이 빠뜨려도 합친다
    skills = _held(draft.skills, student.skills, source, "보유 기술", dropped)
    certificates = _held(draft.certificates, student.certificates, source, "자격증", dropped)
    projects = _held(draft.projects, student.projects, source, "프로젝트", dropped)
    # 관심 분야로 들어오면 안 되는 것: 희망 직무 그 자체, 기업명이 든 표현 (LLM이 자주 섞음)
    not_interest = {_norm(student.desired_job or ""), _norm(draft.job_title)}
    company_names = [_norm(n) for c in companies for n in (c.original, c.name)]
    # [24] 경험으로 판정된 관심 분야는 projects로 옮긴다 (원문이 학생 입력에 있을 때만)
    experienced = check_interests(student, draft.interests)
    for i in sorted(experienced):
        it = draft.interests[i]
        # 직무 설정 LLM이 이미 projects에 넣은 것(원문이 겹치거나 이름이 같음)은 다시 넣지 않는다
        orig = _norm(it.original)
        if orig in source and not any(orig in _norm(h.original) or _norm(it.name) == _norm(h.name) for h in projects):
            projects.append(Held(original=it.original, name=it.name))
        dropped.append(f"관심 분야(경험 → projects): {it.original}")
    held = skills + certificates + projects
    interests = []
    for i, it in enumerate(draft.interests):
        if i in experienced:
            continue
        it.keywords = _keywords(it.keywords)
        orig = _norm(it.original)
        if orig not in source:
            dropped.append(f"관심 분야: {it.original}")
        elif orig in not_interest or any(n in orig for n in company_names):
            dropped.append(f"관심 분야(직무·기업과 중복): {it.original}")
        # [24] 이미 한 것(보유 항목 원문 안에 들어 있거나 이름이 같음)은 관심 분야에서 뺀다 — 프롬프트 규칙을 놓칠 때 대비
        elif any(orig in _norm(h.original) or _norm(it.name) == _norm(h.name) for h in held):
            dropped.append(f"관심 분야(이미 보유·경험): {it.original}")
        else:
            interests.append(it)
    catalog_job = draft.catalog_job if catalog and draft.catalog_job in catalog else None

    return TargetJob(
        **draft.model_dump(exclude={"interests", "companies", "catalog_job", "search_keywords",
                                    "skills", "certificates", "projects"}),
        search_keywords=_keywords(draft.search_keywords),
        interests=interests, companies=companies, catalog_job=catalog_job,
        skills=skills, certificates=certificates, projects=projects,
        student=student, dropped=dropped,
    )
