"""개인 시험용: 터미널 채팅으로 에이전트 전체(입력 판단 ~ 결과 생성)를 써 본다. 대화는 이어진다 ([71]).

실행: .venv/bin/python test_llm.py [--cards] [--no-rag] [--student-id N] [--out result.json] [--verbose]
- 메시지를 그냥 입력한다. 전공·직무 같은 정보도 대화로 말하면 된다.
- /new 새 대화, q 종료
- 실행 중 Ctrl+C는 그 실행만 멈추고 입력으로 돌아간다 (그 메시지는 대화에 넣지 않음).
- [74][75] LLM이 부르는 도구(search_rag·search_requirements·search_public_jobs·read_documents)를 실행 중에 바로 보여 준다.
  도구는 취업 가이드 2단계와 정보 질문만 쓴다. 인사·개념 설명·진로 고민·후속 질문은 도구 없이 답한다 ([83] 도구를 물으면 목록은 알려 줌).
- DB가 꺼져 있으면 search_rag 없이, .env에 JOBALIO_API_KEY가 없으면 search_public_jobs 없이 돈다 (시작할 때 상태를 알려 줌).
"""
import argparse
import re
import readline  # noqa: F401 — input()에서 한글 지우기·방향키가 제대로 되게 함
import traceback

from sqlalchemy import text

from engine.pipeline.answer import render_chat, render_markdown
from engine.pipeline.run import run_pipeline
from engine.schemas.chat import ChatTurn
from engine.schemas.job import StudentInput

# 진행 로그 중 도구 호출과 그 결과만 고른다 (--verbose면 전부 출력)
TOOL_LOG = re.compile(
    r"^=== \[|^\[[^\]]+\] (search_|읽기:|검색어 중복|에이전트 끝)|^카드 \d+장 중 통과|^읽기 출력이 잘림"
    r"|^정보 질문 (search_|읽기)|^정보 답변 검토|^도구 호출 오류|^RAG 검색 실패|^시간 상한|^필수 슬롯")


def rag_status() -> str:
    """시작할 때 search_rag를 쓸 수 있는지 알려 준다."""
    try:
        from src.db.database import engine
        with engine.connect() as conn:
            docs = conn.execute(text("SELECT count(DISTINCT source_document_id) FROM document_chunk")).scalar()
    except Exception as e:
        return f"DB 연결 안 됨 — search_rag 없이 검색 API로만 진행 ({type(e).__name__})"
    return f"DB 연결됨 — search_rag로 찾을 수 있는 문서 {docs}건"


def jobs_status() -> str:
    """[84] 잡알리오(공공기관·공공병원 채용 공고) 도구를 쓸 수 있는지."""
    from engine.tools import jobalio
    return "잡알리오 키 있음 — search_public_jobs 사용" if jobalio.available() else "잡알리오 키 없음 — search_public_jobs 없이 진행"


def show_tools(result) -> None:
    """이번 답변에서 쓴 도구를 요약한다. 정보 질문은 실행 안 된 호출까지 호출 순서대로 보여 준다."""
    calls = result.guide.tool_calls
    if calls:
        print(f"[도구 호출 {len(calls)}번]")
        for c in calls:
            print(f"  턴{c['turn']} {c['tool']}({c['args']}) → {c['result']}")
    elif result.extraction:
        e = result.extraction
        print(f"[도구 호출 {e['tool_calls']}번, 슬롯별 종료: {e['stops']}]")
    else:
        print("[도구 호출 없음]")


def show(result, cards: bool) -> None:
    """모델 답변과 중간 판단 요약을 보여 준다."""
    print()
    if result.route:
        print(f"[입력 판단: {result.route.kind} — {result.route.reason}]")
    if result.job:
        print(f"[목표 직무: {result.job.job_title}]")
    if result.extraction:
        e = result.extraction
        print(f"[근거: 문서 {e['docs']}건, 카드 {e['cards']}장, {e['rounds']}바퀴, 슬롯 {e['slots']}]")
        if e["rag_error"]:
            print(f"[RAG 오류 — 검색 API로만 진행: {e['rag_error'].splitlines()[0]}]")
    show_tools(result)
    g = result.guide
    if g.answer_by_code:
        print(f"[모델 답변 대신 코드 문구를 씀: {g.answer_issues}]")
    print("\n도우미: " + (render_markdown(g) if cards and g.sections else render_chat(g)))
    print(f"\n[소요(초): {result.timings}]")


def main() -> None:
    p = argparse.ArgumentParser(description="터미널 채팅으로 에이전트 시험")
    p.add_argument("--cards", action="store_true", help="대화형 답변 대신 항목 카드 형식으로 출력")
    p.add_argument("--no-rag", action="store_true", help="RAG 없이 검색 API로만")
    p.add_argument("--student-id", type=int, help="DB student_id (있으면 인호 Gap으로 보유 판단)")
    p.add_argument("--out", help="마지막 결과 전체를 JSON으로 저장할 경로")
    p.add_argument("--verbose", action="store_true", help="단계 이름·도구 호출만이 아니라 진행 로그를 모두 출력")
    a = p.parse_args()

    history: list[ChatTurn] = []
    print(f"CareerQuest 취업 가이드 채팅입니다. \n 편하게 말씀해 주세요. (/new 새 대화, q 종료)")
    print(f"[{'RAG 끔 (--no-rag) — 검색 API로만 진행' if a.no_rag else rag_status()}]")
    print(f"[{jobs_status()}]")
    while True:
        try:
            message = input("\n나: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if message.lower() in ("q", "quit", "exit"):
            return
        if message == "/new":
            history = []
            print("새 대화를 시작합니다.")
            continue
        if not message:
            continue

        shown = []

        def progress(stage: str, _msg: str) -> None:  # 취업 가이드는 몇 분 걸려서 단계를 보여 준다
            if stage not in shown:
                shown.append(stage)
                print(f"  … {stage}", flush=True)

        def tool_log(msg: str) -> None:
            if TOOL_LOG.search(msg):
                print(f"    {msg.splitlines()[0]}", flush=True)  # DB 오류처럼 여러 줄인 로그는 첫 줄만

        try:
            result = run_pipeline(StudentInput(question=message), student_id=a.student_id, use_rag=not a.no_rag,
                                  progress=progress, log=print if a.verbose else tool_log, history=history)
        except KeyboardInterrupt:
            print("\n실행을 멈췄습니다.")
            continue
        except Exception:  # 시험 도구라 오류를 보여 주고 다음 입력을 받는다
            traceback.print_exc()
            continue
        show(result, a.cards)
        history += [ChatTurn(role="user", content=message),
                    ChatTurn(role="assistant", content=result.guide.answer, guide=result.guide)]
        if a.out:
            with open(a.out, "w", encoding="utf-8") as f:
                f.write(result.model_dump_json(indent=1))
            print(f"저장: {a.out}")


if __name__ == "__main__":
    main()
