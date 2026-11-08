# CareerQuest 프로젝트 개발 현황 및 연동 가이드 — 2026-11-08 갱신본

> 이 문서는 이전 `CareerQuest_Project_Summary.md`(2026-11-01)의 **현재판**이다. 확인된 최신 내용이 우선하며, 이전 문서에 남아 있던 Backend 추천점수/Readiness 중심 구조, Gemini/Qwen 기반 로컬 파싱, 모델팀 RAG를 인호 담당으로 표기한 내용은 **현행 설계가 아니다**.
>
> 데이터 개수는 이전 개발 세션의 마지막 감사 수치이며 **이번 작성 중 AWS DB 전체 재집계는 하지 않았다**. 코드 계약은 GitHub `main`(2026-11-07)으로 대조했으며 로컬 통합 결과 `da9bd64`는 2026-11-08 현재 `inho`에서 확인되었으나 아직 원격에 Push되지 않아, 명세 최종 검증은 로컬 `/openapi.json`으로 수행해야 한다.

## 1. 프로젝트 핵심

CareerQuest는 대학 학과·교육과정과 직무 요구, 자격증, 프로그램 및 학생 경험을 Skill 단위로 연결한다. 모델팀이 근거와 학생 입력을 분석하여 `SkillRow`와 canonical `skill_id`를 제공하고, 백엔드는 기존 `StudentSkill`과 비교해 GAP을 반환한다. 추천 우선순위·이유·로드맵은 **모델팀**이 생성하고 프론트가 표시한다.

```text
대학 교육과정 / 자격증 / 프로그램 / 채용공고 / 학생 경험
                  ↓
           데이터 수집·정규화
                  ↓
          [모델팀] Evidence/RAG
        SkillRow(skill_id 포함)
                  ↓
        [백엔드] Skill ID 검증
          StudentSkill ↔ GAP
                  ↓
        [모델팀] 최종 추천
                  ↓
            [프론트엔드]
```

## 2. 팀 담당

- **백엔드·DB·수집 담당:** 수집·결정론적 파싱, PostgreSQL 및 DB 품질, canonical Skill 사전, Resource/ResourceSkill, StudentSkill/Evidence, GAP, FastAPI.
- **모델팀:** Evidence·Skill 추출/병합, SkillRow 식별, RAG·임베딩, 추천/순위/이유/로드맵, 모델 내부 파이프라인.
- **프론트팀:** 학생 프로필·질문·파일 업로드, 분석 진행 상황, 결과 및 후속 대화 UI.
- 인호 로컬 PC에서 Ollama/Qwen/RAG를 실행하지 않는다. 모델 실행은 모델팀 환경에서 담당한다.

## 3. 최근 데이터 확보 상황 (마지막 개발 세션 기준)

| 항목 | 마지막 확인 값 및 범위 |
|---|---|
| 대상 학과 | 건양대학교 메디컬캠퍼스 18개 학과로 범위 확장 |
| 교육과정 검증 | 16개 학과에서 공식 교육과정 1,557개 확인(앞선 감사 기준) |
| 수집 경로 | PDF 16건 + HTML 1건 성공, 의료인공지능학과 1건 `SKIPPED_REVIEW`로 별도 확인 필요(이전 단계 기준) |
| Canonical Skill | 245개 (앞선 감사 기준) |
| Resource | 10,993개 (앞선 DB 감사 기준) |
| 데모 학생 | `student_id=1`, StudentSkill 8개 (2026-11-08 읽기 전용 확인) |

주의: 학과 수와 교육과정 수는 서로 다른 집계 기준이며, 18개 학과 전체가 모두 적재 완료되었다는 뜻은 아니다. 모든 수치는 실제 DB 감사 후 갱신한다.

## 4. 서버 코드·계약 최신 상태

- 데이터 조회 및 GAP: `main.py`, `src/gap_service.py`, `src/student_skill_service.py`.
- 모델 분석과 통합 서버: `backend/app.py`; `main.py` 라우트를 함께 포함.
- 요청 스키마: `engine/schemas/job.py`의 `StudentInput`, `backend/app.py`의 `AnalyzeRequest`.
- 분석 상태: `backend/app.py`의 `AnalyzeStatus`.
- 결과 UI 데이터: `engine/schemas/rank.py`의 `GuideResult`.
- 대화 히스토리: `engine/schemas/chat.py`의 `ChatTurn`.

| API | 기능 |
|---|---|
| `GET /health` | 통합 서버 및 DB 접속 상태 |
| `POST /analyze` | 학생 입력·선택적 student_id 수신 → HTTP 202/job_id |
| `GET /analyze/{job_id}` | 대기/실행 중/완료/실패, 진행상황, 최종 `result.guide` |
| `POST /upload` | 개인 문서 텍스트 추출 |
| `GET /api/skills` | canonical Skill 사전 |
| `GET /api/students/{student_id}/skills` | 학생 보유 Skill |
| `POST /api/gap/calculate` | SkillRow와 StudentSkill 비교 |

학생 DB ID가 없으면 모델팀이 입력 프로필 기준으로 GAP을 보완하고, 실제 `student_id`가 있으면 DB+프로필을 사용할 수 있다. 모든 신규 학생에게 데모 학생 ID `1`을 전달하면 안 된다. 개인 문서는 공용 RAG 색인에 넣지 않는다.

## 5. 2026-11-08 검증

- `AnalyzeRequest`에서 신규 학생 `student_id=None` 입력: PASS → `PROFILE` 경로.
- 기존 학생 `student_id=1` 입력: PASS → `DB + PROFILE` 보완.
- DB에서 학생 1 존재 확인: PASS.
- DB에서 StudentSkill 8개 확인: PASS.
- LLM 실행 없음, DB 쓰기 없음. 분석 모델 전체 E2E는 별도 팀 서버에서 검증해야 한다.
- 이전 API/DB/GAP 통합 read-only 검증 4/4 PASS(기존 개발 대화 기준).

## 6. 2026-11-08 Git 상황 — Push 이전 최신 기록

- 저장소: `imgonnago/CareerQuest`.
- **현재 로컬 작업 브랜치:** `inho` (이전 작업 브랜치에서 fast-forward 완료).
- 현재 로컬 HEAD: `da9bd64` — `merge: integrate latest model API with backend`.
- `origin/inho`보다 **21개 커밋 앞섬**, 미Push. 작업 디렉터리 깨끗함(문서 편집 이전 상태).
- `integration-prep-20261108`도 동일한 `da9bd64` 커밋을 가리킴.
- `backup/integration-before-push-20261108`, `backup/integration-before-clean-20261108` 백업 브랜치 존재.
- `origin/main` 및 이전 로컬 `inho` 커밋 모두 `da9bd64`의 조상임을 `git merge-base --is-ancestor`에서 검증(PASS).
- 2026-11-08 `git diff --check origin/main...HEAD`: PASS.
- 2026-11-08 `python -m compileall -q main.py src`: 종료코드 0.
- 보안 키워드 검색 결과는 여러 코드 파일의 줄번호를 반환했지만 **실제 비밀정보 노출은 아직 개별 확인 필요**. 문자열 검색만으로 비밀정보 안전을 확정할 수 없음.
- 원격 `main` 최신(조회 시점): `f8b2fd2`(11월 7일).

**Push 방침:** 코드·문서·비밀정보 점검 및 로컬 회귀 확인을 마칠 때까지 `git push origin inho` 보류. `main` 또는 모델팀 전용 소스의 이력을 재작성하지 않음. 기존 커밋에 민감정보가 포함됐는지도 별도 검토.

## 7. 화요일 프론트 전달 계약

`POST /analyze` (`student`, optional `student_id`, optional `use_rag`, optional `history`) → **202 + job_id** → `GET /analyze/{job_id}` 상태 조회 → 완료 시 **`result.guide`** 표시.

주의: 다른 PC에서 `127.0.0.1`은 사용자 PC를 가리키므로 통합 서버의 실제 주소가 필요하다. CORS 개발 설정과 실제 서버 URL은 구분한다. 상태 기록은 메모리 기반이므로 서버 재시작 시 `job_id`는 사라질 수 있다.

구체적인 요청/응답 형식과 테스트 시나리오는 동봉한 `Frontend_Model_API_Contract.md`가 기준이다.

## 8. 앞으로의 우선순위

1. `inho`의 45개 변경 파일 중 실행 코드와 검토 CSV·DB 스크립트를 분류하고 비밀정보/재현성/실행 안전성 확인.
2. `docs/CareerQuest_Project_Summary.md` 및 `docs/Frontend_Model_API_Contract.md` 최신화; 전체 README는 팀 공동 결정 후 개정.
3. 로컬에서 문법·코드 무결성·기존 회귀 스크립트 점검 후 `inho` Push. 모델팀 서버에서 `/analyze` E2E 통합 테스트(신규/기존 학생, 진행률/결과 포함).
4. 신규 학생 영구 저장 API는 입력·DB 규격 합의 후 구현(지금 급히 추가하지 않음).
5. 학과별 교육과정 적재/품질 검증과 아직 `SKIPPED_REVIEW`인 학과 확인.
6. 직무별 고품질 JOB, 기술교육형 PROGRAM, 자격증 Skill 연결 품질 보강.
7. GAP와 StudentSkill 자동 회귀 테스트 및 운영 백업/권한 점검.

## 9. 보안·운영

`.env`, AWS DB 접속 정보, API 키, 백업 dump는 commit 금지. PostgreSQL 5432 포트는 전체 인터넷 `0.0.0.0/0`에 개방하지 않는다. 모델팀이 접근할 공용 통합 서버 주소는 팀에서 확정한다.

## 10. 한 줄 기준점

> 모델팀이 근거로 Skill과 `skill_id`를 만들고, 백엔드는 학생의 보유 Skill과 비교하여 GAP을 계산하며, 최종 추천은 모델팀이 결정한다. 지금은 설계 확장보다 프론트 통합 검증·DB 품질 확보가 우선이다.

## 10. 문서 관리 및 안전한 명령

- `README.md`: 현재 초기 계획을 담은 팀 공용 초안(예: SQLite/Llama 3.1/Chroma). 전체 교체는 모델·프론트 담당과 조율해야 함. 우선 이 문서 및 연동 명세를 최신 기준으로 사용.
- `CLAUDE.md`: 모델팀의 파이프라인·RAG 상세 문서. 백엔드 담당이 임의로 덮어쓰지 않음.
- 데이터 구축 스크립트(`src/db/seed_*`, `expand_*`, `map_*`, `src/loaders/*`)는 DB 변경 가능성이 있으므로 읽기 전용 검증과 구분하고 운영 DB에서 무심코 실행하지 않음.
- 로컬 시험은 `python -m compileall -q main.py src` 및 `git diff --check`부터 수행. DB를 쓰는 통합 테스트는 별도 명시적인 승인·백업 후 진행.
- 상세 업로드 전 체크리스트는 `docs/Backend_PrePush_Checklist.md` 참고.
