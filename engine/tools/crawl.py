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
DEFAULT_DELAY = 2.0     # 같은 도메인 연속 요청 최소 간격(초)
MIN_TEXT_LEN = 200      # 이보다 짧으면 본문 추출 실패로 본다

#링크만 가져오는 도메인 정의
LINK_ONLY_DOMAINS = (
    "jobkorea.co.kr", "saramin.co.kr", "linkareer.com", "catch.co.kr",
    "wanted.co.kr", "jobplanet.co.kr", "incruit.com",
)
#크롤링 하지 않는 도메인 정의
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

#도메인을 확인하여 LINK_ONLY_DOMAINS, NO_CRAWL_DOMAINS에 속하는지 확인하고, 속하면 해당 상태와 노트를 반환. 속하지 않으면 "pending" 반환
def _matches(domain: str, suffixes: tuple[str, ...]) -> bool:
    #suffixes에서 정의한 도메인을 하나씩(s) 확인하면서 domain이 s와 같거나, domain이 s로 끝나는지 확인. 
    return any(domain == s or domain.endswith("." + s) for s in suffixes)

#_matches를 써서 domain이 LINK_ONLY인지 NO_CREAL인지 확인하고, 상태와 노트를 반환. 속하지 않으면 pending 상태와 None 반환
def precheck_status(domain: str) -> tuple[str, str | None]:
    """네트워크 없이 도메인 정책만으로 정해지는 상태."""
    #LINK_ONLY에 속하면 link_only 상태와 노트 반환
    if _matches(domain, LINK_ONLY_DOMAINS):
        return "link_only", "채용 플랫폼: 링크만 제공" #status, note
    #NO_CRAWL에 속하면 disallowed 상태와 노트 반환
    if _matches(domain, NO_CRAWL_DOMAINS):
        return "disallowed", "운영 정책상 AI/RAG 수집 금지" #status, note
    #그 외에는 pending 상태와 None 반환
    return "pending", None

#도메인에 robots.txt를 확인하고, 해당 도메인에 대한 RobotFileParser를 반환. 
#robot.txt가 없으면 전부 허용(400), 접근 거부(401,403)면 전부 차단. 
def _robots_for(domain: str) -> RobotFileParser | None:
    #domain이 이미 _robots에 있으면 해당 도메인 파서를 반환
    if domain in _robots:
        return _robots[domain]
    #파서를 새로 생성하고, robots.txt를 요청하여 상태에 따라 파서를 설정
    parser: RobotFileParser | None = RobotFileParser()
    #try-expect로 무조건 실행되는 구간
    try:
        #domain에 대한 robots.txt를 요청
        r = requests.get(f"https://{domain}/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=5)
        #r.status_code가 401 또는 403이면 접근 거부로 parser를 None으로 설정
        if r.status_code in (401, 403):
            parser = None                      # 접근 거부 → 전부 차단으로 간주
        #r.status_code가 400 이상이면 robots.txt가 없다고 판단하고 전부 허용으로 parser 설정
        elif r.status_code >= 400:
            parser.parse([])                   # robots.txt 없음 → 전부 허용 (표준)
        #r.status_code가 그외의 값이면 robots.txt가 존재하므로 parser에 robots.txt 내용 파싱
        else:
            parser.parse(r.text.splitlines())
    #requests.RequestException가 발생하면 parser를 None으로 설정하여 확인 불가로 간주 -> 예외처리 
    except requests.RequestException:
        parser = None                          # 확인 불가 → 보수적으로 차단
    _robots[domain] = parser
    return parser

#도메인에 대한 마지막 요청 시간을 확인하고, delay보다 짧으면 sleep을 하여 요청 간격을 맞춤. 과도한 요청을 방지하기 위해 사용.
def _wait_turn(domain: str, delay: float) -> None:
    elapsed = time.monotonic() - _last_hit.get(domain, 0.0)
    if elapsed < delay:
        time.sleep(delay - elapsed)
    #마지막 요청 시간을 현재 시간으로 갱신
    _last_hit[domain] = time.monotonic()

#도메인이 tistory.com이면 티스토리 html을 가져오고 스킨별 본문 영역을 찾아 본문을 추출. 
#그외 도메인은 html이 있으면 trafilatura.extract로 본문을 추출. 
#본문 길이가 MIN_TEXT_LEN보다 짧으면 None 반환. 
def _extract(html: str, domain: str) -> str | None:
    #domain의 끝이 .tistory.com이면 티스토리 html을 가져옴.
    if domain.endswith(".tistory.com"):
        #BeautifulSoup으로 html을 파싱하고, _TISTORY_SELECTORS에서 본문 영역을 찾음.
        soup = BeautifulSoup(html, "html.parser")
        #_TISTORY_SELECTORS에서 본문 영역을 찾음.
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
    #공백을 정리하고, 빈 줄을 제거하여 text를 반환. 
    lines = (" ".join(line.split()) for line in text.splitlines())
    text = "\n".join(line for line in lines if line)
    #본문 길이가 MIN_TEXT_LEN보다 짧으면 None 반환, 아니면 text 반환
    return text if len(text) >= MIN_TEXT_LEN else None

"""
precheck_status로 도메인 정책을 확인한 뒤 pending 상태인 경우만 fetch_fulltext를 호출하여 본문 수집을 시도.
robots.txt를 확인하고, 접근 가능한 경우 요청 간격을 맞춰 요청.
html을 가져와서 본문을 추출하고, 본문 길이가 충분하면 ok 상태로 업데이트. 실패하면 failed 상태로 업데이트.
"""
def fetch_fulltext(doc: EvidenceDoc) -> tuple[EvidenceDoc, str | None]:
    #doc.fetch_status가 pending이 아니면 그대로 반환.
    if doc.fetch_status != "pending":
        return doc, None
    #최초 요청 시간 now를 현재 시간으로 설정
    now = datetime.now().astimezone()
    #done 함수 정의: fetch_status, fetch_note, fetched_at을 업데이트한 EvidenceDoc과 text를 반환
    def done(status: str, note: str | None = None, text: str | None = None) -> tuple[EvidenceDoc, str | None]:
        return doc.model_copy(update={"fetch_status": status, "fetch_note": note, "fetched_at": now}), text
    #도메인의 파서를 가져옴
    parser = _robots_for(doc.domain)
    if parser is None or not parser.can_fetch(BOT_NAME, doc.url):
        return done("disallowed", "robots.txt 차단 또는 확인 불가")
    #요청 간격을 맞추기 위해 _wait_turn 호출
    _wait_turn(doc.domain, float(parser.crawl_delay(BOT_NAME) or DEFAULT_DELAY))
    try:
        #doc.url에 요청을 보내고, User-Agent를 설정하고, timeout을 10초로 설정
        r = requests.get(doc.url, headers={"User-Agent": USER_AGENT}, timeout=10)
        #r.raise_for_status()로 HTTP 오류가 발생하면 예외 처리
        r.raise_for_status()
    except requests.RequestException as e:
        return done("failed", f"요청 실패: {type(e).__name__}")
    #r.headers의 Content-Type에 "html"이 없으면 failed 상태와 노트를 반환
    if "html" not in r.headers.get("Content-Type", ""):
        return done("failed", "HTML 문서 아님")
    #r.encoding이 None이거나 "iso-8859-1"이면 r.apparent_encoding으로 설정
    if r.encoding is None or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding
    #_extract로 본문을 추출하고, None이면 failed 상태와 노트를 반환. 아니면 ok 상태와 text를 반환
    text = _extract(r.text, doc.domain)
    if text is None:
        return done("failed", "본문 추출 실패")
    return done("ok", text=text)
