"""환경 변수(.env) 로딩."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} 가 .env에 없습니다 (.env.example 참고)")
    return value


# 크롤링 시 사이트에 보내는 User-Agent. robots.txt 판단도 이 이름 기준
USER_AGENT = "CareerQuestBot/0.1 (student project; Konyang Univ.)"
