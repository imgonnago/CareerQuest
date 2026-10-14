"""본문 수집: 도메인 정책 → robots.txt 확인 → 요청 간격 → 본문 추출.

판단 순서
1. LINK_ONLY_DOMAINS: robots.txt와 상관없이 링크만 제공 (README 데이터 수집 원칙)
2. NO_CRAWL_DOMAINS: 운영 측이 AI/RAG 목적 수집을 명시적으로 금지한 곳
3. 그 외: 요청 시점에 robots.txt를 받아 CareerQuestBot 기준으로 판단
"""
import time
from datetime import datetime
from urllib.robotparser import RobotFileParser

import requests
import trafilatura
from bs4 import BeautifulSoup

from engine.config import USER_AGENT
from engine.schemas import EvidenceDoc

BOT_NAME = "CareerQuestBot"
DEFAULT_DELAY = 1.0     # 같은 도메인 연속 요청 최소 간격(초)
MIN_TEXT_LEN = 200      # 이보다 짧으면 본문 추출 실패로 본다

LINK_ONLY_DOMAINS = (
    "jobkorea.co.kr", "saramin.co.kr", "linkareer.com", "catch.co.kr",
    "wanted.co.kr", "jobplanet.co.kr", "incruit.com",
)
NO_CRAWL_DOMAINS = (
    "naver.com",        # robots.txt: RAG 목적 접근 금지 명시
    "brunch.co.kr",     # robots.txt: AI 크롤러 차단
)
# 티스토리 스킨별 본문 영역
_TISTORY_SELECTORS = (
    "div.tt_article_useless_p_margin", "div.contents_style",
    "div.article-view", "div.entry-content", "article",
)

_robots: dict[str, RobotFileParser | None] = {}   # 도메인 → 파서 (None = 전부 차단)
_last_hit: dict[str, float] = {}                  # 도메인 → 마지막 요청 시각


def _matches(domain: str, suffixes: tuple[str, ...]) -> bool:
    return any(domain == s or domain.endswith("." + s) for s in suffixes)


def precheck_status(domain: str) -> tuple[str, str | None]:
    """네트워크 없이 도메인 정책만으로 정해지는 상태."""
    if _matches(domain, LINK_ONLY_DOMAINS):
        return "link_only", "채용 플랫폼: 링크만 제공"
    if _matches(domain, NO_CRAWL_DOMAINS):
        return "disallowed", "운영 정책상 AI/RAG 수집 금지"
    return "pending", None


def _robots_for(domain: str) -> RobotFileParser | None:
    if domain in _robots:
        return _robots[domain]
    parser: RobotFileParser | None = RobotFileParser()
    try:
        r = requests.get(f"https://{domain}/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=5)
        if r.status_code in (401, 403):
            parser = None                      # 접근 거부 → 전부 차단으로 간주
        elif r.status_code >= 400:
            parser.parse([])                   # robots.txt 없음 → 전부 허용 (표준)
        else:
            parser.parse(r.text.splitlines())
    except requests.RequestException:
        parser = None                          # 확인 불가 → 보수적으로 차단
    _robots[domain] = parser
    return parser


def _wait_turn(domain: str, delay: float) -> None:
    elapsed = time.monotonic() - _last_hit.get(domain, 0.0)
    if elapsed < delay:
        time.sleep(delay - elapsed)
    _last_hit[domain] = time.monotonic()


def _extract(html: str, domain: str) -> str | None:
    if domain.endswith(".tistory.com"):
        soup = BeautifulSoup(html, "html.parser")
        for sel in _TISTORY_SELECTORS:
            node = soup.select_one(sel)
            if node:
                for junk in node.select("script, style, figure, .another_category, .container_postbtn"):
                    junk.decompose()
                text = node.get_text("\n")
                break
        else:
            text = trafilatura.extract(html) or ""
    else:
        text = trafilatura.extract(html, include_comments=False) or ""
    lines = (" ".join(line.split()) for line in text.splitlines())
    text = "\n".join(line for line in lines if line)
    return text if len(text) >= MIN_TEXT_LEN else None


def fetch_fulltext(doc: EvidenceDoc) -> tuple[EvidenceDoc, str | None]:
    """pending 상태인 근거의 본문을 수집한다.

    반환: (fetch_status가 갱신된 EvidenceDoc, 본문 전체 또는 None)
    본문은 근거 풀에 넣지 않고 LLM이 읽도록 따로 넘긴다.
    """
    if doc.fetch_status != "pending":
        return doc, None
    now = datetime.now().astimezone()

    def done(status: str, note: str | None = None, text: str | None = None) -> tuple[EvidenceDoc, str | None]:
        return doc.model_copy(update={"fetch_status": status, "fetch_note": note, "fetched_at": now}), text

    parser = _robots_for(doc.domain)
    if parser is None or not parser.can_fetch(BOT_NAME, doc.url):
        return done("disallowed", "robots.txt 차단 또는 확인 불가")

    _wait_turn(doc.domain, float(parser.crawl_delay(BOT_NAME) or DEFAULT_DELAY))
    try:
        r = requests.get(doc.url, headers={"User-Agent": USER_AGENT}, timeout=10)
        r.raise_for_status()
    except requests.RequestException as e:
        return done("failed", f"요청 실패: {type(e).__name__}")
    if "html" not in r.headers.get("Content-Type", ""):
        return done("failed", "HTML 문서 아님")
    if r.encoding is None or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding

    text = _extract(r.text, doc.domain)
    if text is None:
        return done("failed", "본문 추출 실패")
    return done("ok", text=text)
