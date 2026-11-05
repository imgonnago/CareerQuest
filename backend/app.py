"""CareerQuest 백엔드 API (FastAPI). 프론트(AWS /var/www/html) ↔ 엔진·DB를 서버 하나로 연결 ([50]).

실행 (저장소 루트에서)
    uvicorn backend.app:app --port 8000

엔드포인트 (용재)
- POST /analyze         분석 시작 (1~5단계 + 결과 생성, 3~5분). 바로 job_id를 돌려주고 뒤에서 돈다
                        본문: {"student": StudentInput, "student_id": DB 학생 번호(있으면 인호 Gap API로 보유 판단)}
                        [71] 채팅: "history"에 이전 대화 [{"role","content","guide"}], student.question에 지금 메시지. 대화는 프론트가 보관해 매번 보낸다
                        업로드 문서는 POST /upload 결과의 filename·text를 student.documents에 넣어 보낸다 ([48])
- GET  /analyze/{job_id}  진행 상황(단계·로그·대기 순번)과 끝나면 결과. ?detail=true면 근거 풀·카드·집계 표·검증 기록까지
- POST /upload          학생이 올린 파일(이력서·포트폴리오·활동 기록)에서 본문을 바로 뽑아 돌려줌 (실시간)
                        multipart "files", 최대 5개 · 파일당 20MB
- POST /rag/search      수집 데이터 벡터 DB 검색 (확인·디버깅용)
- GET  /rag/documents/{id}  검색으로 찾은 문서의 본문 전체
- GET  /health          서버·DB 상태
엔드포인트 (인호, main.py를 그대로 붙임): /api/db/…, /api/skills, /api/job-roles, /api/students…, /api/gap/…, /api/model/…
  main.py는 고치지 않고 라우트만 가져온다. 경로가 겹치면(/health) 이 파일 것이 먼저다

업로드 파일은 학생 개인 자료라 공용 벡터 DB(source_document)에 넣지 않는다. 뽑은 본문은 응답으로만
돌려주고, 프론트가 분석 요청 때 student.documents로 함께 보낸다 (1단계만 읽음).
분석은 Ollama 모델 하나를 쓰므로 한 번에 하나씩 돈다 (나머지는 대기). 작업 기록은 서버 메모리에만 있다 (재시작하면 사라짐).
"""
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from engine.rag.extract import SUPPORTED_EXT, UnsupportedFile, extract
from engine.schemas.chat import ChatTurn
from engine.schemas.job import StudentInput

log = logging.getLogger(__name__)

MAX_FILES = 5
MAX_BYTES = 20 * 1024 * 1024
MAX_JOBS = 50           # 메모리에 남길 분석 작업 수 (오래된 완료 작업부터 지움)
MAX_MESSAGES = 40       # 작업마다 남길 진행 로그 줄 수


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
    ocr_images: int = Field(default=0, description="문서 속 그림(붙여 넣은 스캔·캡처)을 OCR한 수")
    warnings: list[str] = []
    text: str = ""
    error: str | None = None


@app.get("/health")
def health():
    """서버는 DB가 꺼져 있어도 뜬다 (업로드·검색 API만으로 분석 가능). database는 접속되면 DB 이름, 아니면 null."""
    try:
        from sqlalchemy import text
        from src.db.database import engine
        with engine.connect() as conn:
            db = conn.execute(text("SELECT current_database()")).scalar()
    except Exception:
        db = None
    return {"ok": True, "status": "ok", "database": db}


# ---------------------------------------------------------------- 분석 (/analyze)

JobState = Literal["대기", "실행 중", "완료", "실패"]


class AnalyzeRequest(BaseModel):
    student: StudentInput
    student_id: int | None = Field(default=None, gt=0, description="DB student_id. 있으면 인호 Gap API(student_skill)로 보유 판단")
    use_rag: bool = True
    history: list[ChatTurn] = Field(default_factory=list, description="[71] 이전 대화 (지금 메시지 제외, 오래된 순). assistant 차례에는 받은 guide를 그대로")


class AnalyzeStatus(BaseModel):
    job_id: str
    status: JobState
    stage: str | None = Field(default=None, description="지금 단계 (목표 직무 설정·근거 추출·근거 집계·우선순위 판단·검증·결과 생성)")
    stage_index: int = Field(default=0, description="지금 단계 번호 1~5 (대기 중이면 0)")
    stage_total: int = 5
    queue_position: int | None = Field(default=None, description="대기 중일 때 앞에 있는 작업 수")
    messages: list[str] = Field(default_factory=list, description="최근 진행 로그")
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    result: dict | None = Field(default=None, description="완료 시 결과. 기본은 guide(최종 답변)만, detail=true면 PipelineResult 전체")


class _Job:
    def __init__(self, req: AnalyzeRequest):
        self.id = uuid.uuid4().hex[:12]
        self.req = req
        self.status: JobState = "대기"
        self.stage: str | None = None
        self.messages: list[str] = []
        self.created_at = datetime.now()
        self.started_at = self.finished_at = None
        self.error: str | None = None
        self.result = None  # PipelineResult


_jobs: dict[str, _Job] = {}
_jobs_lock = threading.Lock()
# Ollama 모델 하나라 동시에 돌리면 둘 다 느려진다 → 한 번에 하나씩
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analyze")


def _run_job(job: _Job) -> None:
    from engine.pipeline.run import run_pipeline

    job.status, job.started_at = "실행 중", datetime.now()

    def progress(stage: str, msg: str) -> None:
        job.stage = stage
        job.messages = (job.messages + [msg])[-MAX_MESSAGES:]

    try:
        job.result = run_pipeline(job.req.student, student_id=job.req.student_id, use_rag=job.req.use_rag, history=job.req.history,
                                  progress=progress, log=lambda m: log.info("[%s] %s", job.id, m))
        job.status = "완료"
    except Exception as e:
        log.exception("분석 실패 %s", job.id)
        job.status, job.error = "실패", f"{type(e).__name__}: {e}"
    finally:
        job.finished_at = datetime.now()


def _forget_old_jobs() -> None:
    with _jobs_lock:
        done = [j for j in _jobs.values() if j.status in ("완료", "실패")]
        for j in sorted(done, key=lambda j: j.created_at)[:max(0, len(_jobs) - MAX_JOBS)]:
            del _jobs[j.id]


@app.post("/analyze", response_model=AnalyzeStatus, status_code=202)
def analyze(req: AnalyzeRequest):
    _forget_old_jobs()
    job = _Job(req)
    with _jobs_lock:
        _jobs[job.id] = job
    _executor.submit(_run_job, job)
    return _status(job, detail=False)


def _status(job: _Job, detail: bool) -> AnalyzeStatus:
    from engine.pipeline.run import STAGES

    queue = None
    if job.status == "대기":
        queue = sum(1 for j in _jobs.values() if j.status in ("대기", "실행 중") and j.created_at < job.created_at)
    result = None
    if job.result is not None:
        result = job.result.model_dump(mode="json") if detail else {
            "guide": job.result.guide.model_dump(mode="json"), "timings": job.result.timings}
    return AnalyzeStatus(
        job_id=job.id, status=job.status, stage=job.stage,
        stage_index=STAGES.index(job.stage) + 1 if job.stage in STAGES else 0, stage_total=len(STAGES),
        queue_position=queue, messages=job.messages, created_at=job.created_at, started_at=job.started_at,
        finished_at=job.finished_at, error=job.error, result=result,
    )


@app.get("/analyze/{job_id}", response_model=AnalyzeStatus)
def analyze_status(job_id: str, detail: bool = False):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "작업 없음 (서버가 다시 시작됐거나 오래된 작업)")
    return _status(job, detail)


# ---------------------------------------------------------------- 업로드


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
            pages=len(doc.pages) if doc.pages is not None else None, ocr_pages=doc.ocr_pages, ocr_images=doc.ocr_images,
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


# ---------------------------------------------------------------- 인호 API (main.py) 붙이기 ([50])
def _include_db_api() -> None:
    """main.py의 라우트를 이 앱에 붙인다. 이미 있는 경로(/health 등)는 건너뛴다. main.py를 못 읽으면 경고만."""
    try:
        from main import app as db_app
    except Exception as e:
        log.warning("인호 API(main.py)를 붙이지 못함: %s", e)
        return
    have = {(getattr(r, "path", None), tuple(sorted(getattr(r, "methods", None) or ()))) for r in app.router.routes}
    for r in db_app.router.routes:
        key = (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", None) or ())))
        if key in have or getattr(r, "path", "").startswith(("/docs", "/redoc", "/openapi")):
            continue
        app.router.routes.append(r)


_include_db_api()
