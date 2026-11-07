"""잡알리오(공공기관 채용정보) 검색 도구 ([84]).

공공기관·공공병원(국립대병원·의료원 등) 채용 공고 원문을 API로 받는다. 크롤링이 아니라 공식 API라 본문(지원자격·우대·전형)까지 근거로 쓴다.
건양대는 병원·의과·간호학과가 있어 병원 취업 근거로 쓰고, 공기업·체험형 인턴 공고도 여기서 찾는다.

API (2026-11-07 시험으로 확인, 명세서 없음 — problem.txt [84])
- https://opendata.alio.go.kr/new/v1/recruit/list.do, POST 폼(GET은 405). serviceKey(공공데이터포털 일반 인증키, Decoding)·pageNo·numOfRows·resultType=json
- 걸러지는 칸: ongoingYn=Y(진행 중), ncsCdLst, workRgnLst, hireTypeLst, recrutSe 등. 제목 검색 칸은 없다
- 목록 응답에 지원자격(aplyQlfcCn)·우대(prefCondCn·prefCn)·전형(scrnprcdrMthdExpln)이 다 들어 있어 detail.do는 부르지 않는다

흐름: 진행 중 공고 전체(500건 안팎)를 받아 CACHE_SECONDS 동안 두고, 검색어 단어가 제목·기관·분야·지역·자격에 몇 개 나오는지로 고른다.
결과는 search_requirements와 같은 RequirementSearchResult (본문은 texts에, EvidenceDoc에는 넣지 않음).
"""
import re
import time
from datetime import datetime, timezone

import requests

from engine.config import require
from engine.schemas import EvidenceDoc
from engine.tools.requirements import RequirementSearchResult
from engine.tools.text import make_doc_id

LIST_URL = "https://opendata.alio.go.kr/new/v1/recruit/list.do"
VIEW_URL = "https://job.alio.go.kr/recruitview.do?idx={sn}"   # 학생에게 줄 공고 원문 링크
DOMAIN = "job.alio.go.kr"
PAGE_ROWS = 100
MAX_PAGES = 20               # 진행 중 공고가 갑자기 많아져도 이만큼만 받는다 (2026-11-07 504건 = 6쪽)
CACHE_SECONDS = 30 * 60      # 진행 중 공고 목록을 다시 받는 간격
TIMEOUT = 30
DEFAULT_K = 15
# 검색어에 흔히 붙지만 공고를 가르지 못하는 말
STOP_WORDS = {"채용", "공고", "모집", "공채", "신입", "경력", "직원", "채용공고", "모집공고", "정보", "자격", "자격요건", "우대", "우대사항", "지원"}

# 단어가 나오면 점수를 주는 칸과 가중치. 제목·기관·분야가 맞는 공고를 자격 문구에만 나온 공고보다 앞에 둔다
FIELDS = (("recrutPbancTtl", 3), ("instNm", 3), ("ncsCdNmLst", 2), ("workRgnNmLst", 2), ("hireTypeNmLst", 2),
          ("recrutSeNm", 1), ("aplyQlfcCn", 1), ("prefCondCn", 1), ("prefCn", 1))

_cache: dict = {"at": 0.0, "rows": []}


class JobAlioError(RuntimeError):
    """잡알리오 API를 부르지 못함 (키 없음·네트워크·응답 오류)."""


def available() -> bool:
    """키가 있으면 도구를 LLM에게 준다."""
    try:
        require("JOBALIO_API_KEY")
        return True
    except RuntimeError:
        return False


def _fetch_open() -> list[dict]:
    """진행 중 공고 전체."""
    key = require("JOBALIO_API_KEY").strip()
    rows: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        r = requests.post(LIST_URL, data={"serviceKey": key, "pageNo": page, "numOfRows": PAGE_ROWS,
                                          "resultType": "json", "ongoingYn": "Y"}, timeout=TIMEOUT)
        r.raise_for_status()
        body = r.json()
        if body.get("resultCode") != 200:
            raise JobAlioError(f"resultCode {body.get('resultCode')}: {body.get('resultMsg')}")
        got = body.get("result") or []
        rows += got
        if not got or len(rows) >= (body.get("totalCount") or 0):
            break
    return rows


def open_postings() -> list[dict]:
    if time.time() - _cache["at"] > CACHE_SECONDS or not _cache["rows"]:
        try:
            _cache["rows"] = _fetch_open()
        except (requests.RequestException, ValueError) as e:
            raise JobAlioError(f"{type(e).__name__}: {e}") from e
        _cache["at"] = time.time()
    return _cache["rows"]


def _terms(query: str) -> list[str]:
    """검색어 단어. "간호사"·"간호직"처럼 끝 글자만 다른 표기도 잡게 3글자 이상 한글 단어는 앞부분도 쓴다."""
    out = []
    for w in re.findall(r"[0-9A-Za-z가-힣]+", query):
        if w in STOP_WORDS or len(w) < 2:
            continue
        out.append(w.lower())
        if len(w) >= 3 and re.fullmatch(r"[가-힣]+", w) and w[-1] in "사직원자":
            out.append(w[:-1])
    return list(dict.fromkeys(out))


def _score(row: dict, terms: list[str]) -> int:
    score = 0
    for term in terms:
        best = max((w for f, w in FIELDS if term in (row.get(f) or "").lower()), default=0)
        score += best
    return score


def _ymd(v) -> str:
    s = str(v or "")
    return f"{s[:4]}.{s[4:6]}.{s[6:8]}" if re.fullmatch(r"\d{8}", s) else s


def render_posting(row: dict) -> str:
    """공고 본문. 날짜는 5단계가 읽는 YYYY.MM.DD 형식으로 쓴다."""
    parts = [
        f"기관: {row.get('instNm') or ''}",
        f"공고명: {row.get('recrutPbancTtl') or ''}",
        f"채용 분야(NCS): {row.get('ncsCdNmLst') or ''}",
        f"근무 지역: {row.get('workRgnNmLst') or ''}",
        f"고용 형태: {row.get('hireTypeNmLst') or ''}",
        f"채용 구분: {row.get('recrutSeNm') or ''}",
        f"채용 인원: {row.get('recrutNope') or ''}",
        f"학력: {row.get('acbgCondNmLst') or ''}",
        f"접수 기간: {_ymd(row.get('pbancBgngYmd'))} ~ {_ymd(row.get('pbancEndYmd'))}",
    ]
    for label, field in (("지원 자격", "aplyQlfcCn"), ("우대 조건", "prefCondCn"), ("우대 내용", "prefCn"),
                         ("전형 방법", "scrnprcdrMthdExpln"), ("결격 사유", "disqlfcRsn")):
        text = (row.get(field) or "").replace("\r\n", "\n").strip()
        if text:
            parts.append(f"{label}:\n{text}")
    if row.get("srcUrl"):
        parts.append(f"기관 채용 페이지: {row['srcUrl']}")
    return "\n".join(parts)


def search_public_jobs(query: str, k: int = DEFAULT_K) -> RequirementSearchResult:
    """진행 중 공공기관 채용 공고 중 검색어 단어가 많이 나오는 k건. 같은 점수면 마감이 가까운 순."""
    try:
        rows = open_postings()
    except JobAlioError as e:
        return RequirementSearchResult(query=query, docs=[], errors=[f"jobalio: {e}"])
    terms = _terms(query)
    scored = [(s, row) for row in rows if terms and (s := _score(row, terms)) > 0]
    scored.sort(key=lambda x: (-x[0], str(x[1].get("pbancEndYmd") or "99999999")))
    now = datetime.now(timezone.utc)
    docs, texts = [], {}
    for rank, (_s, row) in enumerate(scored[:k], start=1):
        url = VIEW_URL.format(sn=row["recrutPblntSn"])
        doc_id = make_doc_id(url)
        snippet = " · ".join(x for x in (row.get("ncsCdNmLst"), row.get("workRgnNmLst"), row.get("hireTypeNmLst"),
                                         row.get("recrutSeNm"), f"마감 {_ymd(row.get('pbancEndYmd'))}") if x)
        begin = str(row.get("pbancBgngYmd") or "")
        docs.append(EvidenceDoc(
            doc_id=doc_id, provider="jobalio", kind="web", query=query, rank=rank,
            title=f"[{row.get('instNm') or ''}] {row.get('recrutPbancTtl') or ''}", snippet=snippet,
            url=url, domain=DOMAIN, source_name=row.get("instNm"), doc_kind="채용공고",
            published_at=int(begin) if re.fullmatch(r"\d{8}", begin) else None,
            fetch_status="ok", fetch_note="잡알리오 API", retrieved_at=now, fetched_at=now,
        ))
        texts[doc_id] = render_posting(row)
    return RequirementSearchResult(query=query, docs=docs, texts=texts)


# LLM 도구 호출용 정의 (Ollama 형식)
TOOL_SPEC = {
    "type": "function",
    "function": {
        "name": "search_public_jobs",
        "description": "잡알리오(공공기관 채용정보)에서 지금 모집 중인 공공기관·공공병원 채용 공고를 찾는다. "
                       "병원·의료·간호·보건, 공기업·공공기관, 체험형 인턴 공고는 여기서 찾는다. 공고 원문(지원자격·우대·전형)까지 읽을 수 있다. "
                       "결과는 문서 번호와 제목 목록이다.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "직무·분야·지역·기관 단어. 예: '간호사 대전', '의료기사 병원', '체험형 인턴 정보통신'"},
            },
            "required": ["query"],
        },
    },
}
