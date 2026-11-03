# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

CareerQuest AI — 대학생(건양대·대전 지역) 맞춤 취업 준비 로드맵을 만드는 LLM + RAG 에이전트. 학생 프로필과 희망 직무를 받아 요구 역량과 비교하고, 교과목·자격증·공모전·지역 프로그램을 **근거(출처)와 함께** 추천한다. 설계 전체와 파트별 TODO는 `README.md`(작업용 초안)에 있다.

- 구현된 것: `engine/`(검색 도구·크롤링·근거 풀/근거 카드 스키마·1~3단계 파이프라인·RAG `engine/rag/`), `backend/app.py`(업로드·RAG 검색 API), `main.py`(인호: DB 조회·Gap 계산 API), `src/`(인호: 수집기·로더·Skill 매핑·Gap·DB 모델), `alembic/`(DB 스키마). 프론트는 AWS 서버(`/var/www/html`)에서 따로 만들기로 해서 저장소의 `frontend/`는 비어 있다(재열 UI는 병합 충돌을 피하려고 일부러 지움 — 되살리지 말 것). `db/`는 비어 있고 `data/raw`·`data/processed`는 git에서 제외.
- 실행 환경: 로컬 Ollama(qwen3.5:9b Q4_K_M, bge-m3 임베딩 1024차원). 호출은 `engine/llm.py`의 `chat()`·`embed()`. **외부 LLM API는 사용하지 않는다.**
- 코드 주석·docstring·커밋 메시지는 한국어로 쓴다.

## 개발 환경

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # NAVER_CLIENT_ID / NAVER_CLIENT_SECRET / KAKAO_REST_API_KEY 등 채우기
```

- DB: `docker compose up -d` → `alembic upgrade head`. pgvector 확장은 마이그레이션이 만든다.
- 빌드·린트·테스트 설정은 아직 없다. 모듈은 저장소 루트에서 `python -c "from engine.tools.kakao import search_kakao; ..."`처럼 `engine.` 절대 import로 실행한다.
- API 키는 `engine.config.require("KEY")`로 읽는다 (없으면 RuntimeError).
- `docs/samples/*.json`은 각 검색 API·본문 수집 결과의 실제 샘플이다. API를 호출하지 않고 형태를 확인할 때 쓴다.

## 엔진 아키텍처

README의 5단계 파이프라인: (1) 목표 직무 설정 → (2) 근거 추출(LLM ↔ 도구 루프) → (3) 근거 집계 → (4) 우선순위 판단 → (5) 검증(코드). 역할 분담 원칙: **LLM은 도구 선택·문서 읽기·판단, 코드는 도구 실행·저장·세기·검증**(8B 모델 약점 보완).

구현된 것은 (1)~(3)과 RAG(`engine/rag/`)다. (4) 우선순위 판단·(5) 검증은 아직 없다 — 추천 상한(개수·순서)은 지금 2단계 끝 `apply_caps`가 정한다.

- `engine/schemas/evidence.py` — `EvidenceDoc`: 검색 결과 1건 = 1개. `doc_id`는 정규화 URL의 SHA1 앞 16자(`text.make_doc_id`)라서 같은 문서는 같은 id. **본문(text)은 담지 않는다** — 본문은 `fetch_fulltext`가 별도로 반환하고, LLM이 읽어 만든 상세 근거 카드가 `doc_id`로 이 항목을 참조한다. `evidence_level`은 `fetch_status == "ok"`일 때만 `fulltext`.
- `engine/tools/naver.py` — 네이버(NCP API HUB) 검색. 네이버는 robots.txt로 RAG 수집을 금지하므로 snippet만 쓰고 `fetch_status="skipped"`.
- `engine/tools/kakao.py` — 카카오(Daum) 검색. blog는 기본적으로 티스토리만 남긴다(`tistory_only`). 생성 시 `crawl.precheck_status`로 초기 상태(`link_only`/`disallowed`/`pending`)를 정한다.
- `engine/tools/crawl.py` — `pending` 문서만 본문 수집: 도메인 정책(`LINK_ONLY_DOMAINS`, `NO_CRAWL_DOMAINS`) → robots.txt(`CareerQuestBot` 기준, 도메인별 캐시, 401/403·요청 실패는 차단 취급) → 도메인별 요청 간격(기본 2초 또는 crawl-delay) → 추출(티스토리는 스킨별 셀렉터, 그 외 trafilatura, 200자 미만이면 실패). 반환은 `(갱신된 EvidenceDoc, text | None)`.
- `engine/tools/requirements.py` — 요구사항 검색 도구. 카카오 소스끼리 순위대로 번갈아 섞어 앞에 두고 네이버(요약만)는 뒤에 보충으로 붙인 뒤 doc_id로 중복 제거(`collect_candidates`, **카카오 1순위**) → 앞에서부터 pending만 `max_fetch`개 본문 수집(`fetch_texts`). 두 단계 사이가 재정렬 모델이 들어갈 자리다. LLM용 `TOOL_SPEC`(Ollama·OpenAI function 형식) 포함.
- **단계별 LLM 분리**: 단계(및 단계 안 역할)마다 LLM 설정·시스템 프롬프트·입출력 스키마를 따로 둔다. 실행은 `engine/pipeline/stageN_*.py`, 프롬프트는 `engine/prompts/stageN_*.py`(입출력 형식을 docstring에 적음), 설정은 모듈 상단의 `LLMConfig`(`engine/llm.py`: model·temperature·think·num_ctx). 호출은 `chat(messages, config, tools=, format=)`.
- `engine/pipeline/stage1_job.py` — (1) 목표 직무 설정. 학생 입력(`schemas/job.py`의 `StudentInput`, 임시 형식) → 직무 설정 LLM이 `TargetJobDraft` JSON(직무명·신입/인턴/경력·세부 관심 분야(원문/고친 용어/검색 키워드)·학생이 말한 기업(원문/공식명/검색 키워드, `Company`)·보유 기술·자격증·프로젝트(원문/표준 이름, `Held`)·직무 검색 키워드) → 관심 분야 확인 LLM(새 대화, 관심 분야마다 관심/경험 `InterestCheck`, 경험이면 projects로 옮김) → 코드가 원문(관심 분야·기업·보유 항목)이 학생 입력에 실제로 있는지 대조, 폼 칸 보유 항목을 합치고, 희망 직무·기업명·보유 항목과 겹치는 관심 분야를 빼서 `TargetJob`. 2단계에는 기업 공식명을 넘긴다(그룹을 말하면 name은 그룹명, keywords에 직무 관련 계열사 공식명). 표준 직무 목록은 `catalog` 인자로 받고, 안 주면 `engine/tools/job_catalog.py`의 `load_job_catalog()`가 DB(`src/db/database.py`, docker-compose의 PostgreSQL+pgvector)에서 읽는다. `JOB_QUERY`는 `job_role`의 활성 직무 `name`. DB가 꺼져 있거나 테이블이 비어 있으면 None → 목록 없이 진행.
- **프롬프트 작성 요령(9B 모델)**: 시스템 프롬프트 규칙을 놓치는 일이 잦다. 자주 틀리는 규칙은 사용자 메시지 끝(출력 직전)에 한 번 더 적는다. 선택·추출처럼 짧은 판단은 긴 대화를 이어 가지 말고 새 대화로 필요한 입력만 준다. 사고 기능(think)은 정확도가 오르지만 수십 초가 걸려 기본은 끈다.
- `engine/pipeline/stage2_evidence.py` — (2) 근거 추출. 입력은 1단계 `TargetJob`(`make_target`이 텍스트로 만듦, 학생 학년 포함). 학생에게 줄 정보를 **가이드 슬롯**(`SLOTS`)으로 정해 두고 슬롯이 다 찰 때까지 바퀴를 도는 **추출 루프**:
  - 슬롯: 채용 요건 4(고유 근거 문서 수) · 자격증 4(어학 성적 TOEIC·OPIc·TOPIK 포함) · 활동·공모전·교과목 8(`ACTIVITY`) · 추천 포트폴리오 3. 채용 요건 외에는 `ITEM_SLOTS` — 서로 다른 항목 수(`item_keys`: 자격증은 item을 쉼표·슬래시로 나눔, 교과목은 과목 코드). 활동(교내프로그램·대외활동)은 문서당 `ACTIVITY_PER_DOC` 2개까지, 교과목은 루프에서 `COURSE_FILL_MAX` 3칸까지만 센다(`fill_counts`). 교과목 슬롯은 따로 없다(학생이 보고 고르는 것이고 해마다 바뀜).
  - 순서 `SLOT_PRIORITY`: 채용 요건 → 자격증 → 활동·공모전·교과목 → 포트폴리오. **필수 슬롯**(`REQUIRED_SLOTS`: 채용 요건·자격증)은 시간·바퀴 상한에 닿아도 0건이면 그 슬롯만 한 바퀴 더(`REQUIRED_EXTRA_ROUNDS`). 상한 `MAX_ROUNDS` 6·`MAX_SECONDS` 205(실험 평균, 바퀴 시작 전 확인).
  - 한 바퀴 = 부족한 슬롯마다 따로: 계획 LLM(새 대화, `SLOT_TASKS` 설명·검색 방향 + 그 슬롯에서 쓴 검색어 → `SearchPlan` 검색어만) → 코드가 검색어마다 **RAG 먼저**(`RagState` → `tools/rag_search.search_rag`, 슬롯별 `SLOT_DOC_KINDS`만, 활동 슬롯은 `RAG_K_SCHOOL` 6건) 그다음 검색 API → 선택 LLM(새 대화, 그 슬롯의 안 읽은 문서 제목 목록 `render_slot` → `DocSelection`, 기본 `SELECT_PER_SLOT` 2건, 활동 슬롯은 RAG 6건·웹 `ACTIVITY_WEB_SELECT` 4건까지) → 고른 문서만 본문 수집 → 읽기 LLM(새 대화, 본문 전체를 `READ_BUDGET_CHARS` 25,000자 단위로 문서째 묶음, `num_predict` 8192, 잘리면 `_salvage_cards`, 카드 칸은 quote → item → category, 문서당 `MAX_CARDS_PER_DOC` 8장) → 코드(`verify`).
  - `verify`: quote를 공백 무시하고 원문과 대조, 적힌 문서에 없으면 같이 읽은 문서 중 한 곳에만 있을 때 그 문서로 고침, 교과목은 같은 과목 코드·과목명이 있는 원문 행으로 인용을 바로잡음(`_course_row`), 전형 절차(`SELECTION_STEP`)는 기업정보로. 교과목의 개설 학년·교양/전공은 LLM이 아니라 코드(`course_info`)가 원문 표 머리글의 학년 칸·표 제목에서 읽는다.
  - 카드 걸러내기(`extract_evidence`): 교과목은 건양대 문서(`is_school_doc`)이면서 RAG 문서이거나 과목 코드가 있을 때만, 학생 학년보다 낮은 과목은 버림(`past_grade`). 건양대 문서가 아닌 교내프로그램은 대외활동으로, 건양대 문서의 대외활동 중 item·quote에 "건양대"가 있으면 교내프로그램으로. 활동 카드는 item에 `ACTIVITY_WORD`(대회·공모·해커톤·캠프·프로그램·인턴·특강 …)가 없으면 버림.
  - 끝에 `apply_caps`가 추천 상한으로 줄임: 활동·공모전·교과목 8은 `ACTIVITY_RANK` 순(**지금 [61] 실험: 대외활동 → 교내프로그램 → 교과목**, 교과목 안에서는 교양 먼저), 자격증 4는 채용 공고(채용 요건 카드가 나온 문서)에 많이 나온 순, 포트폴리오 3은 여러 문서에 나온 순. 활동 슬롯 선택 목록도 `ACTIVITY_KIND_RANK`·`_activity_tier`로 외부 → 건양대 → 교육과정 순([61]). 다른 슬롯 목록은 RAG(`(DB 자료)`) → 웹 순.
  - DB 오류가 나면 `RagState`가 그 실행에서 RAG를 끄고 검색 API로만(`ExtractionResult.rag_error`). 한 번에 모든 슬롯을 계획·선택시키면 9B 모델이 일부 슬롯만 하거나 이전 계획을 되풀이해서 슬롯마다 나눴다. 검색어는 "대상(건양대·기업 공식명·직무·상위 분야) + 찾는 영역" 2~5단어. JSON 출력은 Ollama `format`에 pydantic 스키마를 넘겨 강제하고, D번호 칸은 정규식 패턴으로 묶는다(안 묶으면 제목까지 붙여 씀).
- `engine/pipeline/stage3_aggregate.py` — (3) 근거 집계. 입력은 2단계 `ExtractionResult`. 코드((doc_id, quote) 중복 제거, 근거 번호 C1…) → 역할 LLM(카드가 나온 문서마다 채용공고·우대사항정리·자격증정보·프로젝트추천 등 `DocRole`, 제목·요약·카드 item으로 판단) → 이름 뽑기 LLM(item마다 짧은 Skill 이름 목록 — 여러 개면 나눠서, 카드 하나가 표 여러 줄에 들어감. 카드의 `skills`·`skill_ids`·`skill_names`는 같은 순서로 짝) → 코드(표기 병합, bge-m3 임베딩 후보 묶음) → 합치기 LLM(후보만) → 사전 매칭(DB Skill 사전 `skill`·`skill_alias`를 `engine/tools/skill_dict.py`가 읽음, 이름·별칭 표기 대조 → 나머지는 임베딩 후보(`MATCH_MIN_SIM` 이상) 중 사전 매칭 LLM이 고르거나 "없음" → 확인 LLM이 Gap 기준 예/아니오, 자격증·교과목·교내프로그램·대외활동 카드에서만 나온 이름은 정확히 같을 때만, 새 Skill은 만들지 않음) → 코드(skill_id(없으면 이름)별 가이드 슬롯·고유 문서 수·분류별·역할별·기업·근거 번호 `SkillRow`, 슬롯 순 정렬). 기업정보 카드는 집계에서 빼고, Gap(보유/미보유)은 집계 표의 `skill_id`로 인호 쪽 코드가 판단한다. DB를 못 읽으면 skill_id는 모두 null. `render_table`이 표 텍스트.
- `engine/rag/` — RAG. **업로드(실시간)와 수집 데이터 적재(일괄)가 같은 추출기를 쓴다.**
  - `extract.py`: 파일 → 본문 전체(`ExtractedDoc`). PDF(텍스트 층, 30자 미만 쪽은 OCR, 쪽마다 `[페이지 N]`)·이미지(OCR, HEIC·GIF 포함)·DOCX(글상자 포함, 표는 행을 ` | `로)·DOC(Word 97 조각 표)·HWP(OLE BodyText 문단, 배포용·암호는 `UnsupportedFile`)·HWPX(zip XML)·TXT(UTF-8→CP949). **문서 속 그림**(DOCX·HWP·HWPX·PDF 글자 쪽의 텍스트 층 없는 큰 그림)도 OCR해 그 자리에 `[그림 N]`으로 넣음(짧은 변 300px 미만·중복은 건너뜀, 문서당 `OCR_MAX_EMBEDDED`(40)개까지).
  - `ocr.py`: PaddleOCR(PP-OCRv5 mobile 검출 + korean 인식, CPU, 약 2초/쪽). 괘선 표는 `table.py`가 칸 단위로, 나머지는 상자를 줄로 묶고 넓은 간격은 ` | `. 자주 틀리는 단어는 `OCR_FIXES`. paddlepaddle은 3.2.2 고정(3.3은 CPU 버그).
  - `table.py`: 이미지에서 괘선(밝기 200 미만의 가는 선)으로 칸을 찾아 OCR 상자를 넣고 행마다 ` | `로 이음. 빈 칸 자리를 남기고, 글자 없는 칸은 픽셀로 ●/○ 판단, 세로 병합 칸의 짧은 글은 행마다 되풀이. 표 안의 표는 예전 줄 묶기로. **VLM(qwen3.5 vision)으로 표를 다시 쓰는 방식은 그럴듯하게 틀려서(problem.txt [47]) 쓰지 않는다.**
  - `chunk.py`: 줄 단위 800자·겹침 150자, PDF는 쪽을 넘지 않음. **조각은 문서 찾기용** — LLM에게는 `get_document`로 본문 전체를 준다.
  - `store.py`: 인호 스키마의 `source_document`(본문 전체·`raw_metadata`) + `document_chunk`(조각·bge-m3 `vector(1024)`·`metadata`). `ingest`(같은 source_name+record_key면 덮어씀, 해시 같으면 건너뜀) / `search`(코사인, 문서별로 묶음, `filters`·`doc_kinds`는 조각 `metadata` 기준, `min_score`) / `get_document`.
  - `index.py`: **인호 DB → 벡터 검색**. 인호 로더가 넣은 `source_document` 중 조각이 없거나 본문(`content_hash`)·종류가 바뀐 행에 조각·임베딩을 붙임(`python -m engine.rag.index [--dry-run]`). 인호 행은 고치지 않고 `doc_kind`·`school`·`department`는 조각 `metadata`에 둠(인호 upsert가 `raw_metadata`를 덮어써서). `doc_kind_of`: 자원 COURSE·CERTIFICATE·JOB·PROGRAM(건양대면 비교과, 아니면 지역프로그램) 우선, 없으면 SYLLABUS·CERTIFICATE_INFO·NOTICE. 검색 결과 요약(SEARCH_RESULT)·대학학과 목록(KCUE)은 안 넣음. 인호 로더를 돌린 뒤 실행.
  - `ingest.py`: 일괄 적재 CLI. `python -m engine.rag.ingest <파일|폴더> --doc-kind 교육과정 --department ... --year 2022 [--dry-run]` 또는 `--manifest`. 추출 본문은 `data/processed/<이름>.txt`.
- `backend/app.py` — FastAPI(`uvicorn backend.app:app`). `POST /upload`(최대 5개·20MB, 본문을 바로 돌려줌. **학생 개인 자료라 공용 벡터 DB에 넣지 않는다**), `POST /rag/search`, `GET /rag/documents/{id}`.
- **근거 풀 vs 근거 카드**: 근거 풀은 코드가 검색 결과를 전부 담는 원문 목록, 근거 카드는 LLM이 그중 근거만 골라 만든 것. "근거를 못 찾으면 넣지 않는다"는 카드 기준이다.
- `engine/tools/rag_search.py` — RAG 검색 결과(`store.search` → `get_document` 본문 전체)를 근거 풀 형식(`RequirementSearchResult`·`EvidenceDoc`)으로. doc_id는 `rag://source_document/<id>` 해시(16자), `MIN_SCORE` 0.55.
- `engine/tools/text.py` — 태그/엔티티 정리, URL 정규화(티스토리 `/m/` 제거, 추적 파라미터 제거), 날짜를 `YYYYMMDD` 정수로.

산출물 이름(README 기준): 근거 풀(원문 전체) → 상세 근거 카드(인용 문장) → 근거 집계 표(숫자·근거 번호) → 근거 순위 카드(순위·행동·근거 번호).

## 현재 상태 (2026-11-04)

- **결정 대기**: 2단계 활동·공모전·교과목 우선순위. 코드는 지금 [61] 외부 우선(대외활동 → 교내프로그램 → 교과목, 활동 슬롯 웹 문서 바퀴당 4건). 같은 학생 3명 실험에서 [60] 건양대 우선은 평균 133초·다 찬 경우 1/3, [61] 외부 우선은 평균 191초·0/3 — 외부 공모전은 늘었지만 자격증·포트폴리오가 덜 읽힘. 추천안: 외부 우선 순서는 두고 `ACTIVITY_WEB_SELECT`를 2~3으로, 같은 대회 다른 이름("중앙 해커톤"/"멋쟁이사자처럼 13기 중앙 해커톤") 묶기. 되돌리려면 `ACTIVITY_RANK`·`ACTIVITY_KIND_RANK`·`ACTIVITY_WEB_SELECT`와 프롬프트 `SLOT_GUIDE`·`SLOT_TASKS`·`SELECT_EXTRA`의 활동 항목.
- **남은 2단계 문제**(problem.txt [58]~[61]): 포트폴리오에 교육 과정(아카데미·부트캠프)이 섞임(사용자: 포트폴리오로 볼 수도 있어 애매 — 보류), 이름이 불분명한 활동("특강"), 백엔드 학생에게 의료 AI 과목이 붙음, 읽기 출력 잘림([36])이 한 바퀴를 150초로 늘리는 경우.
- **합의된 것**: 3단계 Skill 사전에 없는 항목은 지우지 않고 `SkillRow.mapping` = "매핑되지 않음", 인호 `src/gap_service.py`도 `skill_id` null 줄을 `gap: "매핑되지 않음"`으로 돌려줌([49]). 인호 파일을 고쳤으니 인호에게 공유할 것.
- **인호와 정할 것**: `engine/rag/index.py`의 `doc_kind_of` 대응표(인호 resource_type·document_type → doc_kind, [53]), index 실행 시점(로더 끝에서 부를지), Skill 사전에 어학 Skill·별칭 추가(자격증은 3단계에서 정확히 같을 때만 매핑).
- **API 서버**: `main.py`(인호)와 `backend/app.py`(용재)를 하나로 합치기로 함([50]). 업로드 문서를 RAG에 넣으려면 학생 문서를 공용 자료와 나눠야 함(`student_id`·공개 범위) — 미구현.
- **시험 데이터**: 실험은 conda 별도 환경 `cq-pgtest`(PostgreSQL 16 + pgvector)의 시험 DB(포트 55432, `careerquest_test`, 사용자 `cqtest`)로 함. 내용은 의료인공지능학과 교육과정 PDF 1건 + 인호 로더 형식 시험 행 8건(비교과·지역 프로그램 각 1건)뿐이라 활동 슬롯 수치는 실제 데이터로 다시 재야 함. 켜는 법: `~/miniconda3/envs/cq-pgtest/bin/initdb -D <dir> -U cqtest --auth=trust` → `pg_ctl -D <dir> -o "-p 55432 -k ''" start` → `createdb -h localhost -p 55432 -U cqtest careerquest_test` → `POSTGRES_DB=careerquest_test POSTGRES_USER=cqtest POSTGRES_PASSWORD=cqtest POSTGRES_PORT=55432 alembic upgrade head` (환경 변수가 .env보다 우선). 측정 스크립트(학생 3명, 시간 상한 없이 6바퀴)는 저장소에 없음.

## 지켜야 할 규칙

- **LLM은 본문 전체를 읽는다.** 코드로 문단을 골라내거나 본문을 잘라 줄이지 말 것. 근거 풀 항목에 본문을 다시 넣지 말 것.
- **데이터 우선순위: RAG에 있는 건양대학교 자료(교육과정·로드맵·수업계획서·비교과)가 최우선이다.** 검색 API·크롤링 결과는 RAG에 없거나 부족한 근거를 보충하는 용도다. 하지만 검색 API 및 크롤링 결과도 적극 활용하여 최대한 많고 정확한 정보 제공을 목표로 한다. (예외 실험 중: 2단계 활동·공모전·교과목 슬롯은 외부 활동·공모전(검색 API)을 1순위로 시험 중 — 아래 "현재 상태", problem.txt [61])
- 수집 방법 순서: API → 크롤링(robots.txt·이용약관 확인한 사이트만) → 수작업.
- 채용 플랫폼(잡코리아·사람인·링커리어·캐치·원티드 등) 상세 페이지는 크롤링하지 않고 링크만 제공한다. 합격 자소서는 수집하지 않는다.
- 날짜(작성일·마감일)는 정수 `YYYYMMDD`로 저장한다.
- 코드 수정·테스트 중 발견한 문제점이나 고쳐야 할 부분은 루트의 `problem.txt`에 번호를 붙여 추가한다. 고친 항목은 제목 옆에 `(해결)`을 붙이고 "해결" 블록(해결: 날짜·한 줄 요약 / 방법: 어디를 어떻게 바꿨고 왜 / 확인: 테스트 결과·남은 한계)으로 처리 방법을 요약한다. 작업 시작 전에 미해결 항목을 확인한다.
- 커밋은 사용자가 직접 한다. 파일 수정까지만 하고 commit/push 하지 않는다.
