# CareerQuest — 프론트엔드·모델팀 API 연동 계약

- 기준일: **2026-11-08**
- 기준 소스: GitHub `imgonnago/CareerQuest`의 2026-11-07 `main` (`backend/app.py`, `engine/schemas/job.py`, `engine/schemas/rank.py`, `engine/schemas/chat.py`, `main.py`) + 2026-11-08 로컬 `inho`/`integration-prep-20261108`의 `da9bd64` 검증 결과
- 주의: 로컬 `inho` HEAD `da9bd64`는 이 문서 작성 시 원격 GitHub에 미게시됨. 실제 화요일 연동 직전 **해당 브랜치의 FastAPI `/openapi.json`으로 스키마 최종 대조**할 것.

## 1. 서버와 담당 경계

- 모델+백엔드 **통합 진입점**: `uvicorn backend.app:app --host 0.0.0.0 --port 8000` (모델 실행 가능한 팀 서버에서)
- `backend/app.py`는 `main.py`의 DB·GAP 조회 라우트도 포함한다. `main.py` 단독 실행과 구분한다.
- 프론트에서 접근할 공용 서버 URL은 팀에서 확정한다. `http://127.0.0.1:8000`은 **요청을 보내는 기기 자신의 로컬 주소**이며 공용 주소가 아니다.
- 개발용 CORS가 설정돼 있지만, 배포 시에는 허용 origin을 실제 프론트 도메인으로 제한한다.
- 백엔드 담당: 정형 데이터, DB, canonical `skill_id`, StudentSkill/Evidence, GAP API. 모델팀 담당: Skill 추출·RAG·최종 추천 및 Roadmap.
- **사용자 담당 로컬 PC에서는 Ollama/Qwen/RAG 실행하지 않는다.** 분석 실연은 모델팀 실행 환경에서 진행한다.

## 2. 분석 생성: `POST /analyze`

- 본문은 `AnalyzeRequest`:
  - `student`: 필수 `StudentInput`
  - `student_id`: `integer > 0` 또는 `null` (선택)
  - `use_rag`: `boolean`, 기본 `true`
  - `history`: 이전 대화 배열, 기본 `[]`

### 학생 정보 (`StudentInput`)

| 필드 | 타입 | 기본값 | 의미 |
|---|---|---|---|
| `major` | string/null | null | 전공 |
| `grade` | integer/null | null | 학년 |
| `desired_job` | string/null | null | 희망 직무 |
| `skills` | string[] | [] | 보유 기술 |
| `certificates` | string[] | [] | 보유 자격증 |
| `projects` | string[] | [] | 프로젝트 |
| `activities` | string[] | [] | 비교과 활동 |
| `question` | string/null | null | 현재 질문·고민 |
| `documents` | `{filename: string, text: string}[]` | [] | 업로드에서 추출한 개인 문서 본문 |

신규 학생 예시 (테스트 입력 형식; 실제 테스트의 학생 프로필 내용을 복원한 것이 아니라 계약의 유효한 예시):

```json
{
  "student": {
    "major": "인공지능학과",
    "grade": 3,
    "desired_job": "AI 엔지니어",
    "skills": ["Python", "SQL"],
    "certificates": [],
    "projects": [],
    "activities": [],
    "question": "취업 준비에 어떤 경험이 더 필요한가요?",
    "documents": []
  },
  "student_id": null,
  "use_rag": true,
  "history": []
}
```

기존 DB 학생일 경우에는 동일한 구조에서 `"student_id": 1`로 바꾼다. **`1`은 데모 학생 ID이지 모든 학생에게 부여할 기본값이 아니다.**

### 202 응답의 핵심

```json
{
  "job_id": "<서버에서 발행한 작업 ID>",
  "status": "대기",
  "stage": null,
  "stage_index": 0,
  "stage_total": 5,
  "queue_position": 0,
  "messages": [],
  "created_at": "<서버 타임스탬프>",
  "started_at": null,
  "finished_at": null,
  "error": null,
  "result": null
}
```

이 JSON은 **필드 구조 설명용**이다. 생성 직후 이미 실행이 시작되었을 수도 있으므로 `status`, `queue_position`, `stage`의 실제 값은 달라질 수 있다.

## 3. 작업 조회: `GET /analyze/{job_id}`

- 프론트는 `POST`에서 받은 `job_id`로 진행 상태를 주기적으로 조회한다.
- `status` 가능값: `대기`, `실행 중`, `완료`, `실패`.
- `stage_index`/`stage_total`은 진행 단계 표시에 사용하고, `messages`는 진행 로그 표시용이다.
- 완료 시 **`result.guide`**를 렌더링한다. `result.timings`도 응답에 포함된다.
- `GET /analyze/{job_id}?detail=true`는 상세 파이프라인 결과를 포함하는 **진단용** 응답이다. 일반 사용자 화면의 필수 요구사항은 아니다.
- 실패 시 `error`를 노출하며 재시도 안내를 제공한다.
- 서버 작업 기록은 메모리에 저장되므로 서버 재시작이나 오래된 작업 정리 뒤에는 **404**일 수 있다. 이때 무한 재조회하지 말고 사용자에게 새 요청을 안내한다.
- 모델 실행 환경이 하나라 분석은 1개씩 순차 실행하며, 대기열이 있을 수 있다. 정확한 소요시간을 API 계약으로 약속하지 않는다.

### `result.guide` 핵심 데이터

- `kind`: 취업 가이드 / 정보 질문 / 진로 고민 / 후속 질문 / 일반 대화
- `job_title`, `answer`, `summary`: 화면의 대화/요약 표시
- `sources`: 답변 근거(제목·URL·인용·분류·근거 번호)
- `sections`: 슬롯별 추천 카드 모음. 각 `items` 원소는 `rank`, `title`, `skill`, `gap`, `action`, `reason`, `evidence` 등을 포함한다.
- `held`, `lacking`, `past`: 이미 보유한 것, 근거 부족 항목, 지난 회차 활동
- `crisis`, `answer_by_code`, `answer_issues`, `summary_by_code`: 상태·검증 관련 필드

`sections[]` 항목은 빈 배열일 수 있다. `answer`만 존재하는 일반 대화 등도 고려해 프론트에서 모든 카드가 있다고 가정하지 않는다.

## 4. GAP 경로 구분 — 이번에 실제 PASS

| 시나리오 | `student_id` | GAP 판단 |
|---|---|---|
| 신규 학생(아직 DB ID 없음) | `null` | 입력 프로필 기준 (`PROFILE`) |
| DB에 존재하는 학생 | 실제 ID (검증 예: `1`) | DB StudentSkill + 입력 프로필 보완 |

2026-11-08 **읽기 전용 계약 검증** 결과:
- `AnalyzeRequest.model_validate(...)` 신규/기존 학생: 모두 PASS
- 데모 학생 `student_id=1` 존재: PASS
- 보유 StudentSkill `8`: PASS
- LLM 실행 없음, DB 변경 없음

이 검증은 **모델 분석이 끝까지 실행됐다는 증거는 아니다**. 완전한 프론트 통합 테스트는 모델팀 실행 서버에서 별도로 진행한다.

## 5. 별도 GAP API (모델팀 ↔ 백엔드)

- `POST /api/gap/calculate`: `{"student_id": 1, "skills": [{"skill_id": 23}, {"skill_id": null, "skill": "예시 미매핑 기술"}]}` 형식.
- 존재하지 않는 Skill ID, 중복 등 잘못된 입력은 422 처리.
- `skill_id=null`인 줄은 삭제하지 않고 `gap="매핑되지 않음"`으로 반환하는 현재 계약.
- Backend는 Skill 이름을 새 ID로 추론하지 않으며, 최종 추천 순위를 결정하지 않는다.
- `required_level`이 있는 입력은 `level_order` 계약을 추가로 따른다. 지금 기본 통합 시나리오는 보유/미보유 위주.

## 6. 학생 파일 업로드 (필요한 경우)

- `POST /upload`: multipart/form-data, 필드명 `files`.
- 최대 5개, 파일당 최대 20MB.
- 성공한 결과 중 `filename`, `text`를 다음 `POST /analyze`의 `student.documents`로 넣는다.
- 개인 업로드 본문은 공용 RAG 색인에 저장하지 않는다.

## 7. 대화 이어가기 (`history`)

- 매번 현재 질문은 `student.question`에 담는다.
- `history`는 오래된 순서의 이전 턴 `{role: "user"|"assistant", content: string, guide?: GuideResult}`.
- 이전 assistant 답변의 `guide`는 서버가 보내준 원본 객체를 그대로 보내야 근거 기반 후속 질문 처리에 유리하다.
- 프론트가 history를 보관해 요청마다 전달한다. 현재 백엔드는 대화 영구 저장소가 아니다.

## 8. 화요일 통합 테스트 시나리오

| 번호 | 시나리오 | 합격 기준 |
|---|---|---|
| FT-01 | 통합 서버 `GET /health` | HTTP 200, 서버 정상. DB가 없으면 `database=null` 여부 별도 식별 |
| FT-02 | 신규 학생 `POST /analyze`, ID 없음 | HTTP 202 + 유효한 `job_id`, 입력 계약 통과 |
| FT-03 | 기존 학생 `student_id=1`, 본인 Skill 입력 | HTTP 202 + `job_id`, DB+프로필 GAP 경로 진입 확인 |
| FT-04 | `GET /analyze/{job_id}` 반복 조회 | 상태/단계/대기열이 화면에 반영됨 |
| FT-05 | 분석 완료 | `result.guide.answer` 출력, `sections` 있으면 카드 표시 |
| FT-06 | 모델 에러 또는 없는 작업 ID | 사용자에게 실패/404 안내, 무한 폴링 없음 |
| FT-07 | 후속 질문 + `history` | 이전 `guide` 전달 및 결과 표시 |
| FT-08 | 파일 업로드 후 분석 | 추출 `filename`,`text`가 `student.documents`로 전달 |
| FT-09 | 다른 PC에서 접속 | `127.0.0.1`이 아닌 팀 서버 주소로 접속 성공 |
| FT-10 | 개발/배포 전 CORS | 개발 요청 통과, 배포 시 실제 프론트 origin으로 제한 예정 |

## 9. 이번 단계에서 새로 만들지 않는 것

- 신규 학생 영구 저장 API: 프론트/DB 공통 규격 확정 전에는 보류.
- 모델팀의 RAG 구현·Qwen 실행: 사용자 백엔드 범위 밖.
- 최종 추천 점수/순위 산출 로직: 모델팀 범위.

## 10. 전달 전 마지막 확인

1. `integration-prep-20261108`의 실제 최신 라우트와 GitHub 최신 `main` 병합 내용이 일치하는지 확인.
2. 작업 브랜치를 안전하게 원격에 보존한 뒤 프론트·모델 담당에게 URL과 테스트 시나리오 전달.
3. `GET /openapi.json`으로 `AnalyzeRequest`, `StudentInput`, `AnalyzeStatus` 스키마를 최종 비교.
4. EC2/AWS 접근 주소와 포트는 팀에서 정하고, DB 비밀번호·`.env`·백업 dump는 Git에 올리지 않는다.
