"""출처 구분: 건양대 자료 · 공식 · 웹 · 커뮤니티 · 검색 요약."""
from engine.schemas.evidence import EvidenceDoc
from engine.schemas.rank import SourceLabel

OFFICIAL_SUFFIX = (".go.kr", ".ac.kr", ".or.kr", ".re.kr", ".gov", ".edu")


def source_label(doc: EvidenceDoc) -> SourceLabel:
    if doc.provider == "rag":
        return "건양대 자료"
    if doc.evidence_level != "fulltext":
        return "검색 요약"
    if doc.kind in ("blog", "cafe"):
        return "커뮤니티"
    return "공식" if doc.domain.endswith(OFFICIAL_SUFFIX) else "웹"


SOURCE_ORDER = {"건양대 자료": 0, "공식": 1, "웹": 2, "커뮤니티": 3, "검색 요약": 4}
