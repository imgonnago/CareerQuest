"""인호 DB(source_document) → 벡터 검색용 조각·임베딩 (document_chunk).

인호 로더(src/loaders)는 수집한 자료의 본문을 source_document에 넣지만 조각·임베딩은 만들지 않는다.
이 모듈이 조각이 없거나 본문이 바뀐 행을 찾아 조각 → bge-m3 임베딩 → document_chunk로 넣는다.
그러면 2단계 RAG 검색(engine/tools/rag_search.py)이 바로 찾는다.

사용 (저장소 루트에서)
    python -m engine.rag.index --dry-run   # 무엇을 넣을지(문서 종류별 수, 건너뛸 이유)만 보기
    python -m engine.rag.index             # 실제로 조각·임베딩 만들기
    python -m engine.rag.index --source-name KONYANG_CURRICULUM_PDF --limit 50

- 인호 행(source_document)은 고치지 않는다. 검색 필터용 메타(doc_kind·school·department)는 조각 metadata에 둔다
  (인호 upsert_source_document는 다시 적재할 때 raw_metadata를 통째로 덮어쓰기 때문).
- 조각 metadata에 문서의 content_hash를 남겨, 인호가 본문을 고쳐 다시 적재하면 다음 실행 때 조각을 다시 만든다.
- engine.rag.ingest로 직접 넣은 파일 문서는 이미 조각이 있고 해시가 같아 건너뛴다.
- doc_kind 이름은 2단계 SLOT_DOC_KINDS와 같아야 슬롯 검색에 걸린다. 정하는 규칙은 doc_kind_of 한 곳에 둔다.
"""
import argparse
import logging
import sys
import time
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import text

from engine.rag.chunk import chunk_document
from engine.rag.extract import ExtractedDoc

log = logging.getLogger(__name__)

SCHOOL = "건양대학교"
SCHOOL_MARKS = ("KONYANG", "건양")

# 인호 로더 → 넣지 않는 문서 (근거로 읽을 본문이 아님)
SKIP_DOCUMENT_TYPES = {"SEARCH_RESULT"}  # 검색 API 요약. 2단계가 실시간으로 검색한다
SKIP_SOURCE_NAMES = {"KCUE_UNIVERSITY_DEPARTMENT_API"}  # 전국 대학·학과 목록 (학과 이름 레코드)


@dataclass
class Candidate:
    source_document_id: int
    source_name: str
    source_type: str
    document_type: str
    resource_type: str | None
    title: str
    content: str
    content_hash: str | None
    raw_metadata: dict
    chunk_count: int
    chunk_hash: str | None   # 지금 조각에 남긴 content_hash (없으면 None)
    chunk_kind: str | None   # 지금 조각의 doc_kind


def is_school(c: Candidate) -> bool:
    hay = f"{c.source_name} {c.title}".upper()
    return any(m.upper() in hay for m in SCHOOL_MARKS)


def doc_kind_of(c: Candidate) -> tuple[str | None, str]:
    """인호 행 → (doc_kind, 이유). doc_kind가 None이면 넣지 않는다.

    가정한 대응 (인호와 확인할 것, problem.txt [53]). 연결된 자원(resource_type)을 먼저 보고, 없으면 문서 종류로:
      COURSE → 교육과정 / CERTIFICATE → 자격증 / JOB → 채용공고 / PROGRAM → 건양대면 비교과, 아니면 지역프로그램
      (자원 없음) SYLLABUS → 교육과정 / CERTIFICATE_INFO → 자격증 / NOTICE → 채용공고
    프로그램 공지도 document_type이 NOTICE일 수 있어 자원 종류가 먼저다.
    """
    if c.document_type in SKIP_DOCUMENT_TYPES:
        return None, f"검색 결과 요약({c.document_type})"
    if c.source_name in SKIP_SOURCE_NAMES:
        return None, f"대학·학과 목록({c.source_name})"
    by_resource = {"COURSE": "교육과정", "CERTIFICATE": "자격증", "JOB": "채용공고",
                   "PROGRAM": "비교과" if is_school(c) else "지역프로그램"}
    if c.resource_type in by_resource:
        return by_resource[c.resource_type], ""
    by_document = {"SYLLABUS": "교육과정", "CERTIFICATE_INFO": "자격증", "NOTICE": "채용공고"}
    if c.document_type in by_document:
        return by_document[c.document_type], ""
    return None, f"종류를 정할 수 없음({c.document_type}/{c.resource_type or '-'})"


def chunk_meta(c: Candidate, doc_kind: str) -> dict:
    """조각마다 붙일 검색 필터용 메타."""
    meta = {"doc_kind": doc_kind, "source_name": c.source_name, "document_type": c.document_type,
            "content_hash": c.content_hash, "indexed_from": "source_document"}
    if c.resource_type:
        meta["resource_type"] = c.resource_type
    if is_school(c):
        meta["school"] = SCHOOL
    dept = c.raw_metadata.get("department_name") or c.raw_metadata.get("department")
    if dept:
        meta["department"] = dept
    if c.raw_metadata.get("curriculum_year"):
        meta["curriculum_year"] = c.raw_metadata["curriculum_year"]
    return meta


def load_candidates(source_name: str | None = None) -> list[Candidate]:
    """본문이 있고 근거로 쓸 수 있는(evidence_eligible) source_document 전부 + 지금 조각 상태."""
    from engine.rag.store import _engine

    where = "d.evidence_eligible AND d.content IS NOT NULL AND length(btrim(d.content)) > 0"
    params = {}
    if source_name:
        where += " AND d.source_name = :sn"
        params["sn"] = source_name
    with _engine().connect() as conn:
        rows = conn.execute(text(f"""
            SELECT d.source_document_id, d.source_name, d.source_type, d.document_type, r.resource_type,
                   coalesce(d.title, ''), d.content, d.content_hash, coalesce(d.raw_metadata, '{{}}'::jsonb),
                   count(c.chunk_id), max(c.metadata->>'content_hash'), max(c.metadata->>'doc_kind')
            FROM source_document d
            LEFT JOIN resource r ON r.resource_id = d.resource_id
            LEFT JOIN document_chunk c ON c.source_document_id = d.source_document_id
            WHERE {where}
            GROUP BY d.source_document_id, r.resource_type
            ORDER BY d.source_document_id"""), params).all()
    return [Candidate(*r) for r in rows]


def needs_index(c: Candidate, kind: str) -> str | None:
    """조각을 (다시) 만들어야 하면 이유, 아니면 None. engine.rag.ingest로 넣은 파일 문서(indexed_from 없음)는 그쪽이 관리."""
    if c.chunk_count == 0:
        return "새 문서"
    if c.chunk_hash is None:  # 예전 ingest 조각 (content_hash를 남기기 전)
        return None
    if c.chunk_hash != c.content_hash:
        return "본문 바뀜"
    if c.chunk_kind != kind:
        return f"종류 바뀜({c.chunk_kind} → {kind})"
    return None


def index_one(c: Candidate, doc_kind: str) -> int:
    """문서 1건의 조각을 만들어 넣는다. 반환: 조각 수."""
    from engine.rag.store import _engine, write_chunks

    title = c.title or f"문서 {c.source_document_id}"
    doc = ExtractedDoc(filename=title, file_type="db", text=c.content, method="text")
    chunks = chunk_document(doc)
    with _engine().begin() as conn:
        write_chunks(conn, c.source_document_id, title, chunks, chunk_meta(c, doc_kind))
    return len(chunks)


def index_pending(source_name: str | None = None, limit: int | None = None, dry_run: bool = False,
                  out=print) -> Counter:
    """조각이 없거나 본문이 바뀐 행을 넣는다. 반환: 문서 종류별 넣은 수 (dry_run이면 넣을 수)."""
    cands = load_candidates(source_name)
    done, skipped, todo = Counter(), Counter(), []
    for c in cands:
        kind, why = doc_kind_of(c)
        if kind is None:
            if c.chunk_count == 0:
                skipped[why] += 1
            else:  # 직접 적재(ingest)한 파일 문서 등 — 이미 조각이 있으면 그대로 둔다
                skipped["이미 조각 있음"] += 1
            continue
        reason = needs_index(c, kind)
        if reason is None:
            skipped["이미 조각 있음"] += 1
            continue
        todo.append((c, kind, reason))
    if limit is not None:
        todo = todo[:limit]
    out(f"source_document {len(cands)}건 중 넣을 문서 {len(todo)}건"
        + (f" (종류별 {dict(Counter(k for _, k, _ in todo))})" if todo else ""))
    for why, n in skipped.most_common():
        out(f"  건너뜀 {n}건: {why}")
    if dry_run:
        return Counter(k for _, k, _ in todo)
    t0 = time.time()
    for i, (c, kind, reason) in enumerate(todo, start=1):
        n = index_one(c, kind)
        done[kind] += 1
        out(f"[{i}/{len(todo)}] {kind} · {reason} · 조각 {n}개 · {c.title[:40]}")
    out(f"완료: {dict(done)} ({time.time() - t0:.1f}초)")
    return done


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="인호 DB source_document에 벡터 검색용 조각·임베딩 붙이기")
    p.add_argument("--source-name", help="이 source_name 문서만")
    p.add_argument("--limit", type=int, help="이번에 넣을 최대 문서 수")
    p.add_argument("--dry-run", action="store_true", help="넣지 않고 무엇을 넣을지만 보기")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    index_pending(args.source_name, args.limit, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
