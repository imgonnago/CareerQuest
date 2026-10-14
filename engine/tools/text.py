"""검색 결과 정리용 공통 함수."""
import hashlib
import html
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_TRACKING = ("utm_", "fbclid", "gclid")


def clean(s: str) -> str:
    """<b> 강조 태그 제거, &quot; 같은 엔티티 디코딩, 공백 정리."""
    return _SPACE.sub(" ", html.unescape(_TAG.sub("", s))).strip()


def normalize_url(url: str) -> str:
    """같은 문서가 같은 doc_id를 갖도록 URL 정규화."""
    p = urlsplit(url.strip())
    host = p.netloc.lower()
    path = p.path
    # 티스토리 모바일 주소(/m/57)는 PC 주소(/57)와 같은 글
    if host.endswith(".tistory.com") and path.startswith("/m/"):
        path = path[2:]
    path = path.rstrip("/") or "/"
    query = urlencode(
        [(k, v) for k, v in parse_qsl(p.query) if not k.startswith(_TRACKING)]
    )
    return urlunsplit(("https" if p.scheme in ("http", "https") else p.scheme, host, path, query, ""))


def make_doc_id(url: str) -> str:
    return hashlib.sha1(normalize_url(url).encode()).hexdigest()[:16]


def domain_of(url: str) -> str:
    return urlsplit(url).netloc.lower()


def yyyymmdd(s: str | None) -> int | None:
    """'20261004' 또는 ISO 8601 '2026-10-04T...' → 20261004."""
    if not s:
        return None
    digits = re.sub(r"\D", "", s)[:8]
    return int(digits) if len(digits) == 8 else None
