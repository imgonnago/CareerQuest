"""개인 시험용: 터미널 채팅으로 에이전트 전체(입력 판단 ~ 결과 생성)를 써 본다. 대화는 이어진다 ([71]).

실행: .venv/bin/python test_llm.py [--cards] [--no-rag] [--student-id N] [--out result.json] [--verbose]
- 메시지를 그냥 입력한다. 전공·직무 같은 정보도 대화로 말하면 된다.
- /new 새 대화, q 종료
- 실행 중 Ctrl+C는 그 실행만 멈추고 입력으로 돌아간다 (그 메시지는 대화에 넣지 않음).
"""
import argparse
import readline  # noqa: F401 — input()에서 한글 지우기·방향키가 제대로 되게 함
import traceback

from engine.pipeline.answer import render_chat, render_markdown
from engine.pipeline.run import run_pipeline
from engine.schemas.chat import ChatTurn
from engine.schemas.job import StudentInput


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
            print(f"[RAG 오류 — 검색 API로만 진행: {e['rag_error']}]")
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
    p.add_argument("--verbose", action="store_true", help="단계 이름만이 아니라 진행 로그를 모두 출력")
    a = p.parse_args()

    history: list[ChatTurn] = []
    print("CareerQuest 채팅입니다. 편하게 말씀해 주세요. (/new 새 대화, q 종료)")
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

        try:
            result = run_pipeline(StudentInput(question=message), student_id=a.student_id, use_rag=not a.no_rag,
                                  progress=progress, log=print if a.verbose else (lambda _: None), history=history)
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
