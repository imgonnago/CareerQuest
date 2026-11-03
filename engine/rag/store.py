"""RAG 벡터 DB: 문서 저장(source_document + document_chunk)과 검색 (PostgreSQL + pgvector).

테이블은 인호 쪽 alembic 스키마(alembic/versions/5e6b07cc8ac9_*.py)를 그대로 쓴다.
- source_document: 문서 1건 = 1행. content에 본문 전체. 출처·메타데이터는 raw_metadata(JSONB)
- document_chunk : 조각 본문 + embedding(vector(1024), bge-m3) + metadata(JSONB, 문서 메타 + page)

검색은 조각 단위로 코사인 거리(<=>) → 문서별로 묶어 돌려준다. 찾은 문서의 본문 전체는
get_document()로 읽어 LLM에게 준다 (조각만 주지 않는다).

같은 (source_name, source_record_key)로 다시 넣으면 덮어쓴다. 본문 해시가 같으면 임베딩을 다시 하지 않는다.
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy import text

from engine.llm import EMBED_DIM, EMBED_MODEL, embed
from engine.rag.chunk import Chunk, chunk_document
from engine.rag.extract import ExtractedDoc

log = logging.getLogger(__name__)

EMBED_BATCH = 16


def _engine():
    # DB 설정(.env)이 없어도 엔진의 다른 부분은 import되도록 쓸 때 가져온다 (job_catalog.py와 같은 방식)
    from src.db.database import engine
    return engine


def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


def _embed_chunks(title: str, chunks: list[Chunk]) -> list[list[float]]:
    # 조각만으로는 무슨 문서인지 모를 때가 많다(표 한 부분 등). 제목을 앞에 붙여 임베딩하고, 저장은 조각 본문만
    vecs = []
    for i in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[i:i + EMBED_BATCH]
        vecs += embed([f"{title}\n{c.content}" for c in batch])
    if vecs and len(vecs[0]) != EMBED_DIM:
        raise RuntimeError(f"임베딩 차원 {len(vecs[0])} ≠ DB 컬럼 {EMBED_DIM}")
    return vecs


def stored_hash(source_name: str, record_key: str) -> str | None:
    """이미 저장된 문서의 파일 해시(조각이 있을 때만). 일괄 적재 때 OCR 전에 확인해 같은 파일은 건너뛴다."""
    with _engine().connect() as conn:
        return conn.execute(text("""
            SELECT d.content_hash FROM source_document d
            WHERE d.source_name = :sn AND d.source_record_key = :rk
              AND EXISTS (SELECT 1 FROM document_chunk c WHERE c.source_document_id = d.source_document_id)"""),
            {"sn": source_name, "rk": record_key}).scalar()


def ingest(doc: ExtractedDoc, *, source_name: str, record_key: str, title: str,
           source_type: str = "MANUAL", document_type: str = "OTHER", trust_level: str = "A",
           source_url: str | None = None, metadata: dict | None = None,
           force: bool = False) -> tuple[int, int]:
    """추출한 문서 1건을 저장·임베딩. 반환: (source_document_id, 조각 수).

    source_type   : OFFICIAL_API / OFFICIAL_WEB / PUBLIC_DATA / UNIVERSITY_WEB / JOB_BOARD / SEARCH_API / MANUAL / OTHER
    document_type : SEARCH_RESULT / WEB_PAGE / API_RECORD / SYLLABUS / NOTICE / CERTIFICATE_INFO / OTHER
    metadata      : 검색 필터용. 예) {"doc_kind": "교육과정", "school": "건양대학교", "department": "의료인공지능학과",
                    "curriculum_year": 2022}
    """
    meta = {**(metadata or {}), "file_name": doc.filename, "file_type": doc.file_type,
            "extract_method": doc.method}
    if doc.ocr_pages:
        meta["ocr_pages"] = doc.ocr_pages
    if doc.warnings:
        meta["extract_warnings"] = doc.warnings
    host = urlparse(source_url).hostname if source_url else None

    with _engine().begin() as conn:
        row = conn.execute(text("""
            SELECT d.source_document_id, d.content_hash, count(c.chunk_id)
            FROM source_document d LEFT JOIN document_chunk c USING (source_document_id)
            WHERE d.source_name = :sn AND d.source_record_key = :rk
            GROUP BY d.source_document_id"""), {"sn": source_name, "rk": record_key}).first()
        if row and row[1] == doc.sha256 and row[2] > 0 and not force:
            log.info("변경 없음 — 건너뜀: %s", title)
            return row[0], row[2]

        params = {"sn": source_name, "rk": record_key, "st": source_type, "dt": document_type,
                  "url": source_url, "host": host, "title": title, "content": doc.text,
                  "meta": json.dumps(meta, ensure_ascii=False), "hash": doc.sha256,
                  "fs": "SUCCESS" if doc.text else "NO_TEXT", "tl": trust_level,
                  "ok": bool(doc.text), "now": datetime.now(timezone.utc)}
        doc_id = conn.execute(text("""
            INSERT INTO source_document
                (source_name, source_record_key, source_type, document_type, source_url, normalized_url,
                 source_host, title, content, raw_metadata, content_hash, fetch_status,
                 validation_status, trust_level, evidence_eligible, fetched_at)
            VALUES (:sn, :rk, :st, :dt, :url, :url, :host, :title, :content, CAST(:meta AS jsonb), :hash, :fs,
                    'ACCEPTED', :tl, :ok, :now)
            ON CONFLICT (source_name, source_record_key) DO UPDATE SET
                source_type = EXCLUDED.source_type, document_type = EXCLUDED.document_type,
                source_url = EXCLUDED.source_url, normalized_url = EXCLUDED.normalized_url,
                source_host = EXCLUDED.source_host, title = EXCLUDED.title, content = EXCLUDED.content,
                raw_metadata = EXCLUDED.raw_metadata, content_hash = EXCLUDED.content_hash,
                fetch_status = EXCLUDED.fetch_status, trust_level = EXCLUDED.trust_level,
                evidence_eligible = EXCLUDED.evidence_eligible, fetched_at = EXCLUDED.fetched_at,
                updated_at = now()
            RETURNING source_document_id"""), params).scalar_one()

        chunks = chunk_document(doc)
        vecs = _embed_chunks(title, chunks)
        conn.execute(text("DELETE FROM document_chunk WHERE source_document_id = :id"), {"id": doc_id})
        if chunks:
            conn.execute(text("""
                INSERT INTO document_chunk
                    (source_document_id, chunk_index, content, embedding, embedding_model, embedding_dim, metadata)
                VALUES (:id, :idx, :content, CAST(:emb AS vector), :model, :dim, CAST(:meta AS jsonb))"""),
                [{"id": doc_id, "idx": c.index, "content": c.content, "emb": _vec(v), "model": EMBED_MODEL,
                  "dim": EMBED_DIM, "meta": json.dumps({**meta, "page": c.page}, ensure_ascii=False)}
                 for c, v in zip(chunks, vecs)])
    log.info("저장: %s (문서 %d, 조각 %d)", title, doc_id, len(chunks))
    return doc_id, len(chunks)


@dataclass
class ChunkHit:
    chunk_index: int
    page: int | None
    content: str
    score: float  # 코사인 유사도 (1 - 거리)


@dataclass
class DocHit:
    source_document_id: int
    title: str
    source_name: str
    document_type: str
    source_url: str | None
    metadata: dict
    score: float                       # 가장 비슷한 조각의 점수
    chunks: list[ChunkHit] = field(default_factory=list)


def search(query: str, k: int = 5, filters: dict | None = None, min_score: float = 0.0,
           chunks_per_doc: int = 3, chunk_pool: int = 40) -> list[DocHit]:
    """질문과 비슷한 문서 k건 (점수 높은 순). 문서마다 맞은 조각을 점수 순으로 chunks_per_doc개까지 붙인다.

    filters  : raw_metadata에 포함돼야 하는 값. 예) {"school": "건양대학교", "doc_kind": "교육과정"}
    min_score: 이보다 낮은 조각은 버린다. 문서가 적으면 무관한 문서도 k건 안에 들어오기 때문
               (시험: 관련 0.59~0.68, 무관 0.42~0.47, bge-m3 기준)
    chunk_pool: 먼저 뽑을 조각 수. 한 문서의 조각이 위를 다 차지해도 k개 문서가 나오도록 넉넉히 뽑는다.
    """
    qv = _vec(embed([query])[0])
    where = "d.evidence_eligible"
    params: dict = {"q": qv, "n": max(chunk_pool, k * 4)}
    if filters:
        where += " AND d.raw_metadata @> CAST(:f AS jsonb)"
        params["f"] = json.dumps(filters, ensure_ascii=False)
    with _engine().connect() as conn:
        rows = conn.execute(text(f"""
            SELECT d.source_document_id, d.title, d.source_name, d.document_type, d.source_url, d.raw_metadata,
                   c.chunk_index, c.metadata->>'page', c.content, 1 - (c.embedding <=> CAST(:q AS vector))
            FROM document_chunk c JOIN source_document d USING (source_document_id)
            WHERE {where} AND c.embedding IS NOT NULL
            ORDER BY c.embedding <=> CAST(:q AS vector)
            LIMIT :n"""), params).all()
    docs: dict[int, DocHit] = {}
    for r in rows:
        if float(r[9]) < min_score:
            break  # 점수 순으로 정렬돼 있다
        hit = docs.get(r[0])
        if hit is None:
            if len(docs) >= k:
                continue
            hit = docs[r[0]] = DocHit(r[0], r[1] or "", r[2], r[3], r[4], r[5] or {}, float(r[9]))
        if len(hit.chunks) < chunks_per_doc:
            hit.chunks.append(ChunkHit(r[6], int(r[7]) if r[7] else None, r[8], float(r[9])))
    return list(docs.values())


def get_document(source_document_id: int) -> dict | None:
    """문서 본문 전체와 출처. 검색으로 찾은 문서를 LLM이 읽을 때 쓴다."""
    with _engine().connect() as conn:
        r = conn.execute(text("""
            SELECT source_document_id, title, content, source_name, source_url, document_type, raw_metadata,
                   fetched_at
            FROM source_document WHERE source_document_id = :id"""), {"id": source_document_id}).mappings().first()
    return dict(r) if r else None
