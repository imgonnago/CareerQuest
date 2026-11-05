"""로컬 Ollama 연결: 대화 모델(chat)과 임베딩 모델(embed).

외부 LLM API는 쓰지 않는다. 모델·주소는 .env로 바꿀 수 있다.
- OLLAMA_HOST    기본 http://localhost:11434
- LLM_MODEL      기본 qwen3.5:9b
- EMBED_MODEL    기본 bge-m3 (1024차원, RAG용)
- LLM_NUM_CTX    기본 32768. LLM이 본문 전체를 읽으므로 Ollama 기본값보다 크게 잡는다

단계·역할마다 LLMConfig를 따로 정의하고 chat(messages, config)로 부른다.
평가(eval/agent_eval.py)는 record_usage로 호출마다 토큰 수·소요 시간을 받는다. 그 밖의 실행에는 영향 없음.
"""
import os
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import requests

import engine.config  # noqa: F401  .env 로딩

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3.5:9b")
EMBED_MODEL = os.getenv("EMBED_MODEL", "bge-m3")
LLM_NUM_CTX = int(os.getenv("LLM_NUM_CTX", "32768"))
EMBED_DIM = 1024  # bge-m3 출력 차원. pgvector 컬럼 크기와 맞출 것

TIMEOUT = 600  # 긴 본문을 읽으면 응답이 수 분 걸릴 수 있다

_usage: list[dict] | None = None             # record_usage 안에서만 목록. 평가는 한 스레드에서 차례로 돈다
_forced_temperature: float | None = None     # record_usage(temperature=...) 안에서 모든 chat의 온도를 고정


@contextmanager
def record_usage(temperature: float | None = None) -> Iterator[list[dict]]:
    """이 안의 LLM 호출 사용량을 목록으로 모은다. temperature를 주면 단계별 설정 대신 그 온도로 고정한다."""
    global _usage, _forced_temperature
    saved = _usage, _forced_temperature
    _usage, _forced_temperature = [], temperature
    try:
        yield _usage
    finally:
        _usage, _forced_temperature = saved


def _record(kind: str, data: dict) -> None:
    """Ollama 응답의 사용량 칸을 기록한다 (시간은 나노초 → 초)."""
    if _usage is None:
        return
    _usage.append({
        "kind": kind,                                            # chat / embed
        "input_tokens": data.get("prompt_eval_count", 0),
        "cached_tokens": data.get("prompt_eval_cached_count", 0),  # 입력 중 이전 호출 캐시를 재사용한 토큰
        "output_tokens": data.get("eval_count", 0),
        "seconds": data.get("total_duration", 0) / 1e9,
        "load_seconds": data.get("load_duration", 0) / 1e9,      # 모델을 메모리에 올린 시간
        "truncated": data.get("done_reason") == "length",        # num_predict에 닿아 출력이 잘림
    })


@dataclass(frozen=True)
class LLMConfig:
    """역할별 LLM 설정. 단계 모듈마다 역할에 맞게 하나씩 정의한다."""
    model: str = LLM_MODEL
    temperature: float | None = None   # None이면 모델 기본값(qwen3.5: 1.0)
    think: bool = False                # 사고 과정. 켜면 느려진다
    num_ctx: int = LLM_NUM_CTX
    num_predict: int | None = None     # 출력 토큰 상한. None이면 제한 없음 (반복 출력이 컨텍스트 끝까지 가는 것 방지용)


def chat(
    messages: list[dict],
    config: LLMConfig = LLMConfig(),
    tools: list[dict] | None = None,
    format: dict | str | None = None,
) -> dict:
    """대화 모델 1회 호출. 반환은 Ollama의 message 그대로.

    - content: 답변 텍스트
    - tool_calls: 도구 호출이 있으면 [{"function": {"name", "arguments"}}] (arguments는 dict)
    - thinking: think=True일 때 사고 과정
    format에 JSON 스키마(dict)를 주면 그 형식으로만 답한다.
    """
    options = {"num_ctx": config.num_ctx}
    temperature = config.temperature if _forced_temperature is None else _forced_temperature
    if temperature is not None:
        options["temperature"] = temperature
    if config.num_predict is not None:
        options["num_predict"] = config.num_predict
    body = {"model": config.model, "messages": messages, "stream": False,
            "think": config.think, "options": options}
    if tools:
        body["tools"] = tools
    if format is not None:
        body["format"] = format
    resp = requests.post(f"{OLLAMA_HOST}/api/chat", json=body, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    _record("chat", data)
    # Ollama는 num_ctx를 넘는 입력을 경고 없이 앞에서부터 잘라낸다
    if data.get("prompt_eval_count", 0) >= config.num_ctx - 256:
        warnings.warn(f"입력 {data['prompt_eval_count']}토큰이 num_ctx({config.num_ctx})에 닿음. 앞부분이 잘렸을 수 있음")
    return data["message"]


def embed(texts: list[str], model: str = EMBED_MODEL) -> list[list[float]]:
    """문장 목록 → 임베딩 목록 (입력 순서 유지, L2 정규화된 벡터)."""
    if not texts:
        return []
    resp = requests.post(f"{OLLAMA_HOST}/api/embed", json={"model": model, "input": texts}, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    _record("embed", data)
    return data["embeddings"]
