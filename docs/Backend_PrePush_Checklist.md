# CareerQuest 백엔드 Push 전 체크리스트

> 2026-11-08, `inho` 로컬 HEAD `da9bd64` 기준. 본 문서는 명령을 자동 실행하지 않음. 모델팀 코드 및 RAG는 변경하지 않음.

## A. 변경 범위 감사

- [ ] `git status -sb`로 현재 `inho` 및 작업 상태 확인.
- [ ] `git diff --name-status origin/main...HEAD`로 현재 45개 변경 파일의 추가/수정/삭제 검토.
- [ ] 교육과정/채용/프로그램 수집기, DB 로더, GAP API, 검증 스크립트 유지 여부 판단.
- [ ] `src/db/seed_*`, `expand_*`, `map_*`는 DB 쓰기가 가능한 일괄 구축·보강 스크립트로 명시. 자동 시작/상시 서버 코드로 간주하지 않음.
- [ ] `data/reports/`의 검토 CSV 4개는 코드 의존성과 개인/민감정보 여부를 확인 후 추적 유지 여부 결정.
- [ ] 큰 변경인 `konyang_curriculum.py` 파서에 외부 LLM 실행 의존성이 남았는지 확인, 실제 사용 경로만 문서화.

## B. 비밀정보·배포 환경

- [ ] `.env`, dump, API 키, DB 비밀번호, 개인 식별 가능 데이터가 커밋된 파일 **및 기존 커밋 기록**에 포함되지 않는지 확인.
- [ ] 이전 정규식 검색이 반환한 의심 줄을 로컬에서 직접 확인(값을 채팅에 붙여 넣지 않음).
- [ ] 샘플 JSON/CSV에 실제 개인 토큰·전화번호·이메일 등 민감한 테스트 데이터가 없는지 확인.
- [ ] `.env.example`은 더미값/빈값만 있는지 확인.
- [ ] AWS DB는 지정된 팀 IP만 허용하고 실서비스에서 프론트가 DB에 직접 접근하지 않도록 구성.
- [ ] 모델팀의 `backend/app.py` 개발용 CORS `*`는 실서비스 배포 전에 허용 프론트 origin으로 제한.

## C. 문서

- [ ] `docs/CareerQuest_Project_Summary.md`를 이 킷의 11월 8일 갱신본으로 교체.
- [ ] `docs/Frontend_Model_API_Contract.md`를 새로 추가.
- [ ] `docs/Backend_PrePush_Checklist.md`를 새로 추가.
- [ ] `README.md`는 오래된 *팀 초안*임을 표시하고, 새 문서 위치를 안내. 다른 팀이 소유한 세부 내용은 일방적으로 바꾸지 않음.
- [ ] `CLAUDE.md`는 모델팀의 상세 규칙이므로 사전 협의 없이 전체 덮어쓰지 않음.

## D. 읽기 전용 최종 검증

```powershell
git status -sb
git diff --check origin/main...HEAD
python -m compileall -q main.py src
Write-Host "compileall exit:" $LASTEXITCODE
```

- [ ] 최종 코드의 `AnalyzeRequest`/GAP 계약을 실제 로컬 소스로 확인. 신규 학생은 `student_id=null`.
- [ ] 기존 학생 DB 조회/E2E 검증 결과가 현재 작업 코드와 일치하는지 확인.
- [ ] 모델 실행(Qwen/Ollama)은 사용자 로컬 PC에서 금지; 모델팀 서버에서만 모델 E2E.
- [ ] 중요: `python -m compileall` 성공은 파싱 검증일 뿐, 동작 또는 DB 무결성을 완전히 보장하지 않음.

## E. Push 전 diff 확인과 최종 공유

```powershell
git diff --stat
git diff -- docs/
git status --short
```

- [ ] 문서 변경만 의도대로인지 확인한 뒤 문서 파일을 명시적으로 stage 및 commit.
- [ ] `git fetch origin` 후 `git status -sb`에서 원격 변경 없음 확인. 원격에 타인이 추가로 Push했다면 무조건 Push하지 말고 이력 검토.
- [ ] 팀원이 이해할 수 있는 커밋 메시지와 11월 8일 최신 문서 링크 준비.
- [ ] 마지막에만 `git push origin inho` 실행. `--force` 사용하지 않음.

## 화요일 연동에서 확인할 것

1. 모델 실행 서버의 실제 URL(프론트 PC에서 접근 가능)과 CORS.
2. `POST /analyze` → 202/job_id → `GET /analyze/{job_id}` → 완료 `result.guide`.
3. 신규 학생 `student_id=null` 프로필 GAP, 기존 학생 실제 ID로 DB+프로필 GAP.
4. 서버 재시작으로 작업이 사라졌을 때 404 처리, 실패 시 에러 메시지 표시.
5. 프론트가 결과 `sections`가 빈 경우에도 `answer`를 보여 주도록 구현.
