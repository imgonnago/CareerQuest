"""수집 데이터 일괄 적재: 파일 → 추출(OCR 포함) → 조각 → 임베딩 → 벡터 DB.

사용 예 (저장소 루트에서)
    # DB 없이 추출·조각만 확인. 추출 본문은 data/processed/<파일명>.txt로 저장
    python -m engine.rag.ingest data/raw/교육과정.pdf --dry-run

    # 파일마다 메타데이터를 명령줄로
    python -m engine.rag.ingest data/raw/교육과정.pdf --title "2022 의료인공지능학과 교육과정" \\
        --doc-kind 교육과정 --department 의료인공지능학과 --year 2022 --source-type UNIVERSITY_WEB

    # 여러 파일은 목록(JSON)으로. 항목마다 path와 위 옵션 이름(title, doc_kind, department, year, source_url,
    # source_type, document_type, trust_level, source_name, record_key, extra)을 적는다
    python -m engine.rag.ingest --manifest data/rag_manifest.json

같은 파일(record_key 기본값 "file:<파일명>")을 다시 넣으면 덮어쓰고, 내용이 같으면 건너뛴다(--force로 강제).
"""
import argparse
import hashlib
import json
import logging
import sys
import time
from pathlib import Path

from engine.config import ROOT
from engine.rag.chunk import chunk_document
from engine.rag.extract import SUPPORTED_EXT, UnsupportedFile, extract_path

PROCESSED_DIR = ROOT / "data" / "processed"


def ingest_file(entry: dict, dry_run: bool = False, force: bool = False) -> None:
    path = Path(entry["path"])
    source_name = entry.get("source_name", "건양대학교")
    record_key = entry.get("record_key", f"file:{path.name}")
    if not dry_run and not force:
        from engine.rag.store import stored_hash
        if stored_hash(source_name, record_key) == hashlib.sha256(path.read_bytes()).hexdigest():
            print(f"[건너뜀] {path.name}: 이미 같은 내용으로 저장됨 (--force로 다시 적재)")
            return
    t0 = time.time()
    doc = extract_path(path)
    chunks = chunk_document(doc)
    took = time.time() - t0
    ocr = f", OCR {len(doc.ocr_pages)}쪽" if doc.ocr_pages else ""
    ocr += f", 그림 OCR {doc.ocr_images}개" if doc.ocr_images else ""
    print(f"[추출] {path.name}: {doc.method}{ocr}, {len(doc.text):,}자, 조각 {len(chunks)}개, {took:.1f}초")
    for w in doc.warnings:
        print(f"  ! {w}")

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    (PROCESSED_DIR / f"{path.stem}.txt").write_text(doc.text, encoding="utf-8")
    if dry_run:
        return

    from engine.rag.store import ingest  # DB 패키지는 실제 적재할 때만 필요

    meta = {"school": entry.get("school", "건양대학교")}
    for key in ("doc_kind", "department"):
        if entry.get(key):
            meta[key] = entry[key]
    if entry.get("year"):
        meta["curriculum_year"] = int(entry["year"])
    meta.update(entry.get("extra") or {})
    doc_id, n = ingest(
        doc,
        source_name=source_name,
        record_key=record_key,
        title=entry.get("title") or path.stem,
        source_type=entry.get("source_type", "MANUAL"),
        document_type=entry.get("document_type", "OTHER"),
        trust_level=entry.get("trust_level", "A"),
        source_url=entry.get("source_url"),
        metadata=meta,
        force=force,
    )
    print(f"[저장] source_document_id={doc_id}, 조각 {n}개")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="수집 데이터를 RAG 벡터 DB에 적재")
    p.add_argument("paths", nargs="*", help="파일 또는 폴더 (폴더는 지원 형식 파일 전체)")
    p.add_argument("--manifest", help="파일별 메타데이터 목록(JSON 배열)")
    p.add_argument("--title")
    p.add_argument("--doc-kind", help="문서 종류. 2단계는 이 값으로 슬롯마다 찾는다(stage2_evidence.SLOT_DOC_KINDS): "
                                      "교육과정·수업계획서·로드맵(교과목), 비교과·교내프로그램(교내 프로그램), "
                                      "채용공고, 자격증, 공모전·대외활동·지역프로그램, 프로젝트")
    p.add_argument("--department")
    p.add_argument("--year", type=int, help="교육과정 학년도")
    p.add_argument("--source-url")
    p.add_argument("--source-type", default="MANUAL")
    p.add_argument("--document-type", default="OTHER")
    p.add_argument("--dry-run", action="store_true", help="DB에 넣지 않고 추출·조각만")
    p.add_argument("--force", action="store_true", help="내용이 같아도 다시 임베딩")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    entries: list[dict] = []
    if args.manifest:
        entries += json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    common = {k: v for k, v in {
        "title": args.title, "doc_kind": args.doc_kind, "department": args.department, "year": args.year,
        "source_url": args.source_url, "source_type": args.source_type,
        "document_type": args.document_type}.items() if v is not None}
    for raw in args.paths:
        path = Path(raw)
        files = sorted(f for f in path.rglob("*") if f.suffix.lower() in SUPPORTED_EXT) if path.is_dir() else [path]
        if len(files) > 1:
            common.pop("title", None)  # 여러 파일에 같은 제목을 붙이지 않게
        entries += [{**common, "path": str(f)} for f in files]
    if not entries:
        p.error("파일 경로나 --manifest를 주세요")

    failed = 0
    for entry in entries:
        try:
            ingest_file(entry, dry_run=args.dry_run, force=args.force)
        except UnsupportedFile as e:
            failed += 1
            print(f"[실패] {entry['path']}: {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
