"""전체 실행: (0) 입력 판단 → [취업 가이드] (1) 목표 직무 → (2) 근거 추출 → (3) 근거 집계 → Gap → (4) 우선순위 판단 ⇄ (5) 검증 → 결과 생성.
                          [정보 질문] 검색(RAG → 검색 API) → 문서 읽기 → 답변   [진로 고민] 상담 답변 (검색 없음)
                          [후속 질문] 이전 추천·근거로 답변   [일반 대화] 도구 없이 모델이 바로 답변 ([72])
[66] 취업 가이드인데 1단계 결과에 목표가 없으면(희망 직무·전공·관심 분야·기업·보유 항목 모두 없음) 2단계를 돌리지 않고 되묻는다.

API(/analyze)와 시험 스크립트가 이 함수 하나를 부른다.
- student_id가 있으면 Gap은 인호 Gap API(student_skill), 없거나 실패하면 1단계 보유 항목으로 (engine/tools/gap.py)
- progress(stage, message): 단계가 바뀔 때·로그가 나올 때 불린다. 프론트 진행 표시("근거 수집 중 …")용
- (5) 검증의 "근거 부족 → (2)"는 아직 다시 추출하지 않고 RankingResult.lacking으로 알린다
  (2단계가 근거 풀을 이어받아 다시 도는 기능이 없음 — 처음부터 다시 돌면 3~4분이 더 걸림)

실행: python -m engine.pipeline.run student.json [--student-id 3] [--out result.json] [--no-rag]
"""
import argparse
import json
import time
from collections.abc import Callable

from pydantic import BaseModel, Field

from engine.pipeline.answer import generate_answer, render_chat, render_markdown
from engine.pipeline.stage0_route import (
    SAFETY_REMINDER, answer_chat, answer_counsel, answer_followup, answer_info, check_crisis, check_followup, clarify_answer, crisis_guided, latest_guide,
    keep_crisis, needs_more_info, resolve_history, route_input, since_last_guide,
)
from engine.pipeline.stage1_job import set_target_job
from engine.pipeline.stage2_evidence import extract_evidence
from engine.pipeline.stage3_aggregate import aggregate_evidence
from engine.pipeline.stage4_rank import prioritize
from engine.schemas.aggregate import AggregationResult
from engine.schemas.chat import ChatTurn
from engine.schemas.job import StudentInput, TargetJob
from engine.schemas.rank import GuideResult, RankingResult
from engine.schemas.route import RouteDecision
from engine.tools.skill_dict import load_skill_dict

STAGES = ("목표 직무 설정", "근거 추출", "근거 집계", "우선순위 판단·검증", "결과 생성")


class PipelineResult(BaseModel):
    route: RouteDecision | None = Field(default=None, description="[66] 입력 판단 결과")
    job: TargetJob | None = Field(default=None, description="1단계 결과 (취업 가이드만)")
    guide: GuideResult = Field(description="학생에게 보여 줄 최종 답변 (모든 경로 공통: answer·sources)")
    ranking: RankingResult | None = Field(default=None, description="근거 순위 카드·Gap·검증 기록 (취업 가이드만)")
    aggregation: AggregationResult | None = Field(default=None, description="근거 풀·상세 근거 카드·근거 집계 표 (취업 가이드만)")
    extraction: dict = Field(default_factory=dict, description="2단계 요약 (슬롯별 채운 수·바퀴 수·RAG 오류·뺀 카드 수)")
    timings: dict[str, float] = Field(description="단계별 초")


def run_pipeline(student: StudentInput, student_id: int | None = None, catalog: list[str] | None = None,
                 use_rag: bool = True, progress: Callable[[str, str], None] | None = None,
                 log: Callable[[str], None] = print, today: int | None = None,
                 history: list[ChatTurn] | None = None) -> PipelineResult:
    """history: [71] 이전 대화 (채팅). student.question이 지금 메시지다. 없으면 단발 요청과 같다."""
    result = _run_pipeline(student, student_id, catalog, use_rag, progress, log, today, history)
    if crisis_guided(history) and not result.guide.crisis:  # 위기 안내 뒤 새 질문에 답하면서 안내를 잊지 않게 짧게 다시 알린다
        result.guide.answer += SAFETY_REMINDER
    return result


def _run_pipeline(student: StudentInput, student_id: int | None, catalog: list[str] | None, use_rag: bool,
                  progress: Callable[[str, str], None] | None, log: Callable[[str], None], today: int | None,
                  history: list[ChatTurn] | None) -> PipelineResult:
    stage = STAGES[0]

    def say(msg: str) -> None:
        log(msg)
        if progress:
            progress(stage, msg)

    timings = {}
    t = time.time()
    stage = "입력 판단"
    say("질문을 살펴보는 중")
    current = student.question  # 대화 정리 전 지금 메시지
    route = check_crisis(student, log=say, history=history)  # 대화 정리보다 먼저, 지금 메시지 원문으로
    if route is None:
        student = resolve_history(student, history, log=say)
        route = check_followup(student, latest_guide(history), log=say) or route_input(student, log=say, question_only=bool(history))
        route = keep_crisis(current, route, history)
        if route.kind == "취업 가이드":
            student = since_last_guide(student, history)
    timings["route"] = time.time() - t
    say(f"입력 판단: {route.kind} ({route.reason})")
    if route.kind != "취업 가이드":
        t = time.time()
        if route.kind == "정보 질문":
            stage = "정보 검색"
            say("관련 자료를 찾는 중")
            guide = answer_info(route.question, log=say)
        elif route.kind == "일반 대화":
            stage = "답변 작성"
            guide = answer_chat(current or "", history, log=say)
        elif route.kind == "후속 질문":
            stage = "답변 작성"
            guide = answer_followup(route.question, history, log=say)
        else:
            stage = "답변 작성"
            guide = answer_counsel(student, route.crisis, log=say, history=history)
        timings["answer"] = time.time() - t
        timings["total"] = sum(timings.values())
        return PipelineResult(route=route, guide=guide, timings={k: round(v, 1) for k, v in timings.items()})

    if not route.has_goal:  # [66] 직무·관심 분야·전공·기업을 하나도 말하지 않음 → 1단계가 직무를 지어내기 전에 되묻는다
        say("목표를 정할 정보가 없어 되묻는 답을 냄")
        t = time.time()
        guide = clarify_answer(student, log=say, history=history)
        timings["answer"] = time.time() - t
        timings["total"] = sum(timings.values())
        return PipelineResult(route=route, guide=guide, timings={k: round(v, 1) for k, v in timings.items()})

    stage = STAGES[0]
    t = time.time()
    say("학생 입력에서 목표 직무를 정하는 중")
    job = set_target_job(student, catalog)
    timings["stage1"] = time.time() - t
    if needs_more_info(job):  # [66] 검색할 목표가 없으면 2단계를 돌리지 않고 되묻는다
        say("목표 직무를 정할 정보가 부족해 되묻는 답을 냄")
        t = time.time()
        guide = clarify_answer(student, log=say, history=history)
        timings["answer"] = time.time() - t
        timings["total"] = sum(timings.values())
        return PipelineResult(route=route, job=job, guide=guide,
                              timings={k: round(v, 1) for k, v in timings.items()})

    stage = STAGES[1]
    t = time.time()
    say(f"{job.job_title} 근거를 찾는 중")
    ext = extract_evidence(job, log=say, use_rag=use_rag)
    timings["stage2"] = time.time() - t

    stage = STAGES[2]
    t = time.time()
    say(f"근거 카드 {len(ext.cards)}장을 Skill별로 묶는 중")
    skill_dict = load_skill_dict()
    agg = aggregate_evidence(ext, skill_dict=skill_dict, log=say)
    timings["stage3"] = time.time() - t

    stage = STAGES[3]
    t = time.time()
    say("우선순위를 판단하고 검증하는 중")
    ranking = prioritize(agg, student_id=student_id, skill_dict=skill_dict, log=say, today=today)
    timings["stage4_5"] = time.time() - t

    stage = STAGES[4]
    t = time.time()
    say("최종 답변을 쓰는 중")
    guide = generate_answer(ranking, agg, today=today, log=say)
    timings["answer"] = time.time() - t
    timings["total"] = sum(timings.values())

    extraction = {"slots": ext.slots, "rounds": len(ext.rounds), "tool_calls": sum(len(r.tool_calls) for r in ext.rounds),
                  "stops": {r.slot: r.stop for r in ext.rounds}, "rag_error": ext.rag_error, "queries": len(ext.queries),
                  "docs": len(ext.docs), "selected": ext.selected, "cards": len(ext.cards), "rejected": len(ext.rejected), "capped": ext.capped,
                  "timings": ext.timings}
    return PipelineResult(route=route, job=job, guide=guide, ranking=ranking, aggregation=agg, extraction=extraction,
                          timings={k: round(v, 1) for k, v in timings.items()})


def main() -> None:
    p = argparse.ArgumentParser(description="CareerQuest 전체 파이프라인 (1~5단계 + 결과 생성)")
    p.add_argument("student", help="학생 입력 JSON 파일 (schemas.job.StudentInput)")
    p.add_argument("--student-id", type=int, help="DB student_id (있으면 인호 Gap API로 보유 판단)")
    p.add_argument("--out", help="결과 JSON 저장 경로")
    p.add_argument("--no-rag", action="store_true", help="RAG 없이 검색 API로만")
    p.add_argument("--cards", action="store_true", help="대화형 답변 대신 항목 카드 형식(render_markdown)으로 출력")
    a = p.parse_args()
    with open(a.student, encoding="utf-8") as f:
        student = StudentInput.model_validate(json.load(f))
    result = run_pipeline(student, student_id=a.student_id, use_rag=not a.no_rag)
    print(render_chat(result.guide) if not a.cards else render_markdown(result.guide))
    print(f"\n소요: {result.timings}")
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(result.model_dump_json(indent=1))


if __name__ == "__main__":
    main()
