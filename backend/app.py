"""CareerQuest 백엔드 API (FastAPI). 프론트(frontend/index.html) ↔ 엔진 연결.

실행 (저장소 루트에서)
    uvicorn backend.app:app --reload --port 8000

엔드포인트
- POST /upload          학생이 올린 파일(이력서·포트폴리오·활동 기록)에서 본문을 바로 뽑아 돌려줌 (실시간)
                        multipart "files", 최대 5개 · 파일당 20MB (프론트 업로드 칸 안내와 같음)
- POST /rag/search      수집 데이터 벡터 DB 검색 (확인·디버깅용)
- GET  /rag/documents/{id}  검색으로 찾은 문서의 본문 전체
- GET  /health

업로드 파일은 학생 개인 자료라 공용 벡터 DB(source_document)에 넣지 않는다. 뽑은 본문은 응답으로만
돌려주고, 프론트가 추천 요청 때 학생 입력과 함께 보낸다 (StudentInput 연결은 형식 합의 후).
"""
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from engine.rag.extract import SUPPORTED_EXT, UnsupportedFile, extract

log = logging.getLogger(__name__)

MAX_FILES = 5
MAX_BYTES = 20 * 1024 * 1024


def _warm_up_ocr():
    """OCR 모델을 미리 올려 첫 업로드가 기다리지 않게 한다. 실패해도 서버는 뜬다 (OCR 필요할 때 다시 시도)."""
    try:
        from engine.rag.ocr import _engine
        _engine()
        log.info("OCR 모델 준비 완료")
    except Exception as e:
        log.warning("OCR 모델 미리 올리기 실패: %s", e)


@asynccontextmanager
async def lifespan(_: FastAPI):
    threading.Thread(target=_warm_up_ocr, daemon=True).start()
    yield


app = FastAPI(title="CareerQuest AI", lifespan=lifespan)
# 개발 중에는 index.html을 파일로 열거나(origin "null") 다른 포트에서 띄우므로 모두 허용. 배포 때 좁힐 것
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class UploadResult(BaseModel):
    filename: str
    ok: bool
    file_type: str | None = None
    method: str | None = Field(default=None, description="text(텍스트 층) / ocr / mixed")
    chars: int = 0
    pages: int | None = None
    ocr_pages: list[int] = []
    warnings: list[str] = []
    text: str = ""
    error: str | None = None


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/upload", response_model=list[UploadResult])
def upload(files: list[UploadFile] = File(...)):
    # 동기 함수로 두면 FastAPI가 스레드 풀에서 돌린다 (OCR이 수 초 걸려도 다른 요청을 막지 않음)
    if len(files) > MAX_FILES:
        raise HTTPException(413, f"파일은 최대 {MAX_FILES}개까지 올릴 수 있습니다")
    results = []
    for f in files:
        name = f.filename or "file"
        data = f.file.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            results.append(UploadResult(filename=name, ok=False, error="파일당 20MB를 넘습니다"))
            continue
        try:
            doc = extract(data, name)
        except UnsupportedFile as e:
            results.append(UploadResult(filename=name, ok=False, error=str(e)))
            continue
        results.append(UploadResult(
            filename=name, ok=True, file_type=doc.file_type, method=doc.method, chars=len(doc.text),
            pages=len(doc.pages) if doc.pages is not None else None, ocr_pages=doc.ocr_pages,
            warnings=doc.warnings, text=doc.text))
    return results


@app.get("/upload/formats")
def upload_formats():
    return sorted(SUPPORTED_EXT)


class SearchRequest(BaseModel):
    query: str
    k: int = Field(default=5, ge=1, le=20)
    filters: dict | None = Field(default=None, description='raw_metadata 포함 조건. 예: {"doc_kind": "교육과정"}')


@app.post("/rag/search")
def rag_search(req: SearchRequest):
    from engine.rag.store import search
    try:
        return search(req.query, k=req.k, filters=req.filters)
    except Exception as e:  # DB가 꺼져 있을 때 등
        raise HTTPException(503, f"벡터 DB 검색 실패: {type(e).__name__}: {e}") from e


@app.get("/rag/documents/{doc_id}")
def rag_document(doc_id: int):
    from engine.rag.store import get_document
    try:
        doc = get_document(doc_id)
    except Exception as e:
        raise HTTPException(503, f"벡터 DB 조회 실패: {type(e).__name__}: {e}") from e
    if doc is None:
        raise HTTPException(404, "문서 없음")
    return doc
