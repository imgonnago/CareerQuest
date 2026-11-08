"""[78] LLM ↔ 도구 루프 공통 (2단계 SlotAgent, 0단계 InfoAgent).

LLM이 도구·횟수·종료를 정하고, 이 모듈은 실행과 상한만 맡는다.
- run_tool_loop: 대화 한 번의 루프. 무엇을 실행하고 언제 되물을지는 호출한 쪽이 함수로 넘긴다
- SearchGuard: 대화 안 검색 기록. 같은 검색 차단, 새 문서가 없는 도구는 목록에서 뺌, 아직 안 쓴 도구 알려 주기
"""
import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

import requests

from engine.llm import LLMConfig, chat

CALLS_PER_TURN = 3   # 한 턴에 실행할 도구 수. 결과를 보기 전에 검색을 몰아 부르지 않게 한다
EMPTY_LIMIT = 2      # 새 문서가 없는 검색이 한 도구에서 이만큼 이어지면 그 대화에서 도구를 뺀다
BROKEN_CALL = "방금 도구 호출 형식이 깨졌다. 인자를 짧게 해서 다시 호출하거나, 끝낼 거면 도구 없이 답해."
OVER_CALLS = f"한 턴에 도구는 {CALLS_PER_TURN}번까지 실행한다. 이 호출은 실행하지 않았다. 앞 결과를 보고 다시 정해."


@dataclass
class LoopResult:
    text: str = ""                                     # LLM이 도구 없이 쓴 마지막 글 (상한·오류로 끝나면 빈 글)
    stop: str = "turns"                                # agent / done / turns / idle / time / error
    turns: int = 0
    calls: list[dict] = field(default_factory=list)    # 턴·도구·인자·결과 첫 줄


def run_tool_loop(
    messages: list[dict],
    config: LLMConfig,
    *,
    tools: Callable[[], list[dict]],
    run_tool: Callable[[str, dict], tuple[str, bool]],
    on_text: Callable[[str], str | None],
    max_turns: int,
    idle_turns: int,
    deadline: float | None = None,
    done: Callable[[], bool] | None = None,
    on_chat: Callable[[float], None] | None = None,
    log=print,
) -> LoopResult:
    """messages를 이어 가며 LLM과 도구를 번갈아 부른다.

    tools: 이번 턴에 줄 도구 목록 (턴마다 달라질 수 있음)
    run_tool: (도구 이름, 인자) → (대화에 돌려줄 글, 새로 실행했는지)
    on_text: LLM이 도구 없이 글을 썼을 때 호출. 이어 가게 할 말을 돌려주면 되묻고, None이면 끝낸다
    done: 도구 실행 뒤 True면 LLM이 더 말하기 전에 끝낸다 (슬롯이 다 참 등)
    """
    res = LoopResult()
    idle, broken = 0, False
    while res.turns < max_turns:
        if deadline is not None and time.time() > deadline:
            res.stop = "time"
            return res
        res.turns += 1
        started = time.time()
        try:
            msg = chat(messages, config, tools=tools())
        except requests.HTTPError as e:  # 모델이 깨진 도구 호출을 내면 Ollama가 오류를 돌려준다. 한 번만 다시 시킨다
            log(f"도구 호출 오류: {e}")
            if broken:
                res.stop = "error"
                return res
            broken = True
            messages.append({"role": "user", "content": BROKEN_CALL})
            continue
        finally:
            if on_chat:
                on_chat(time.time() - started)
        calls = msg.get("tool_calls") or []
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        if not calls:
            text = (msg.get("content") or "").strip()
            again = on_text(text)
            if again is None:
                res.text, res.stop = text, "agent"
                return res
            messages.append({"role": "user", "content": again})
            continue
        ran = False
        for i, call in enumerate(calls):
            fn = call["function"]
            args = fn.get("arguments")
            args = args if isinstance(args, dict) else {}
            result, did = run_tool(fn["name"], args) if i < CALLS_PER_TURN else (OVER_CALLS, False)
            ran = ran or did
            res.calls.append({"turn": res.turns, "tool": fn["name"], "args": args, "result": result.split("\n")[0][:120]})
            messages.append({"role": "tool", "tool_name": fn["name"], "content": result})
        idle = 0 if ran else idle + 1
        if done and done():
            res.stop = "done"
            return res
        if idle >= idle_turns:
            res.stop = "idle"
            return res
    return res


class SearchGuard:
    """검색 기록. ran(같은 검색 차단)은 실행 전체에, 나머지는 대화 하나에 적용한다."""

    SEARCH_TOOLS = ("search_rag", "search_requirements", "search_public_jobs")

    def __init__(self):
        self.ran: set[tuple[str, str]] = set()
        self.new_conversation()

    def new_conversation(self) -> None:
        self.used: set[str] = set()         # 이 대화에서 쓴 검색 도구
        self.listed: set[str] = set()       # 이 대화에 이미 보여 준 D번호
        self.empty: Counter = Counter()     # 도구별로 새 문서 없이 이어진 검색 수

    def repeated(self, tool: str, query: str) -> bool:
        """이미 한 검색이면 True, 처음이면 기록하고 False."""
        key = (tool, re.sub(r"\s+", "", query).lower())
        if key in self.ran:
            return True
        self.ran.add(key)
        return False

    def fresh(self, tool: str, nums: list[str]) -> list[str]:
        """검색 결과 중 이 대화에 처음 나온 문서. 없으면 그 도구의 빈 검색으로 센다."""
        self.used.add(tool)
        new = [n for n in dict.fromkeys(nums) if n not in self.listed]
        self.listed.update(new)
        self.empty[tool] = 0 if new else self.empty[tool] + 1
        return new

    def exhausted(self, tool: str) -> bool:
        return self.empty[tool] >= EMPTY_LIMIT

    def unused(self, available: list[str]) -> list[str]:
        """이 대화에서 아직 안 쓴 검색 도구."""
        return [t for t in available if t in self.SEARCH_TOOLS and t not in self.used]

    def hint(self, available: list[str]) -> str:
        left = self.unused(available)
        return f"아직 {', '.join(left)}를 쓰지 않았다. " if left else ""
