"""글 정리와 대조용 정규화."""
import re


def norm_space(s: str) -> str:
    """대조용: 공백 제거, 소문자."""
    return re.sub(r"\s+", "", s).lower()


def norm_quote(s: str) -> str:
    """대조용: 공백·말줄임표·따옴표를 모두 지운다.

    모델이 인용을 옮길 때 "AI나" → "AI 나", "3점" → "3 점"처럼 띄어쓰기를 넣는 경우가 많아 공백은 무시한다.
    """
    s = s.replace("…", "").replace("...", "")
    return re.sub(r"[\s\"'“”‘’]", "", s)


# qwen이 숫자·영문 뒤에 띄어 쓰는 것("3 학년 2 학기", "Python 과")을 붙인다
UNIT_SPACE = re.compile(r"(\d) (학년|학기|년|월|일|개월|주|시간|분|개|건|회|차|기|대)")
PARTICLE_SPACE = re.compile(r"([A-Za-z0-9)]) (과|와|를|을|이|가|은|는|에|의|로|으로|에서|도|만)(?=[\s.,]|$)")


def tidy(text: str) -> str:
    return PARTICLE_SPACE.sub(r"\1\2", UNIT_SPACE.sub(r"\1\2", " ".join(text.split())))


def tidy_lines(text: str) -> str:
    """줄 구조(문단·목록)는 두고 줄마다 띄어쓰기만 정리 (tidy는 줄바꿈까지 합침)."""
    return "\n".join(tidy(ln) if ln.strip() else "" for ln in text.strip().splitlines())
