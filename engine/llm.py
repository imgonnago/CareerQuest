"""로컬 Ollama 연결: 대화 모델(chat)과 임베딩 모델(embed).

외부 LLM API는 쓰지 않는다. 모델·주소는 .env로 바꿀 수 있다.
- OLLAMA_HOST    기본 http://localhost:11434
- LLM_MODEL      기본 qwen3.5:9b
- EMBED_MODEL    기본 bge-m3 (1024차원, RAG용)
- LLM_NUM_CTX    기본 32768. LLM이 본문 전체를 읽으므로 Ollama 기본값보다 크게 잡는다

단계·역할마다 LLMConfig를 따로 정의하고 chat(messages, config)로 부른다.
"""
import os
import warnings
from dataclasses import dataclass

import requests

import engine.config  # noqa: F401  .env 로딩

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3.5:9b")
EMBED_MODEL = os.getenv("EMBED_MODEL", "bge-m3")
LLM_NUM_CTX = int(os.getenv("LLM_NUM_CTX", "32768"))
EMBED_DIM = 1024  # bge-m3 출력 차원. pgvector 컬럼 크기와 맞출 것

TIMEOUT = 600  # 긴 본문을 읽으면 응답이 수 분 걸릴 수 있다


@dataclass(frozen=True)
class LLMConfig:
    """역할별 LLM 설정. 단계 모듈마다 역할에 맞게 하나씩 정의한다."""
    model: str = LLM_MODEL
    temperature: float | None = None   # None이면 모델 기본값(qwen3.5: 1.0)
    think: bool = False                # 사고 과정. 켜면 느려진다
    num_ctx: int = LLM_NUM_CTX


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
    if config.temperature is not None:
        options["temperature"] = config.temperature
    body = {"model": config.model, "messages": messages, "stream": False,
            "think": config.think, "options": options}
    if tools:
        body["tools"] = tools
    if format is not None:
        body["format"] = format
    resp = requests.post(f"{OLLAMA_HOST}/api/chat", json=body, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
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
    return resp.json()["embeddings"]
