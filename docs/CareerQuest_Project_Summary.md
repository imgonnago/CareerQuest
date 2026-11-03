# CareerQuest 프로젝트 개발 현황 및 연동 가이드

> 기준일: 2026-11-01  \
> 목적: 팀 인수인계, Git 프로젝트 문서화, 새 채팅/새 개발 세션 재개용 체크포인트

## 1. 프로젝트 한눈에 보기

CareerQuest는 대학 교육과정, 자격증, 청년·비교과 프로그램, 실제 채용공고를 Skill 단위로 통합하고, 학생의 이수 과목·취득 자격증·프로젝트/인턴 경험에서 확인된 Skill을 목표 직무의 시장 요구 Skill과 비교해 Skill Gap과 Readiness를 계산한 뒤, 부족한 Skill을 채울 수 있는 COURSE / CERTIFICATE / PROGRAM을 추천하는 시스템이다.

**핵심 설계 원칙:** LLM은 구조화·Skill 추출·애매한 의미 판단을 보조하고, 최종 추천은 DB의 Skill Graph와 수치 기반 로직이 결정한다.

```text
COURSE / CERTIFICATE / PROGRAM / JOB
↓
RESOURCE → RESOURCE_SKILL
↓
JOB → RESOURCE_JOB_ROLE → JOB_ROLE
↓
ROLE_SKILL
↓
학생 이수과목 / 자격증 / 프로젝트·인턴 경험
↓
STUDENT_SKILL + STUDENT_SKILL_EVIDENCE
↓
SKILL GAP → READINESS
↓
COURSE / CERTIFICATE / PROGRAM 추천
↓
팀원 모델 / 프론트용 JSON
```

## 2. 현재 완료 현황

| 영역 | 현재 상태 | 판정 | 비고 |
|---|---:|---|---|
| Skill | 45개 | 완료 | AI/SW 도메인 seed. 다학과 확장 시 canonical Skill 추가 |
| JobRole | 8개 | 완료 | AI/SW MVP 기준 |
| COURSE | 66개 | 완료 | 건양대학교 2026 인공지능학과 |
| COURSE → Skill | TAUGHT 30개 | 완료 | 직접 문자열/alias + Gemini 2-pass 일부 |
| CERTIFICATE | Resource 100개 | 수집 완료 | Q-Net/DataQ 기반 |
| CERTIFICATE → Skill | 총 17개 | 완료 | 공식 SourceDocument 근거 기반 |
| PROGRAM | 7개 | 기본 완료 | 정책/취업지원 중심, Skill 0개도 정상 |
| JOB | 27개 수집 | 기본 완료 | 96~106은 비AI/SW 오염으로 비활성화 대상 |
| JOB → Skill | 약 82개 | 완료 | REQUIRED/PREFERRED 명시 구역 기반 |
| JOB → JobRole | 규칙 기반 15개 | 완료 | 나머지는 모델 검토/오염 제거 |
| RoleSkill | ML Engineer 표본 11개 중심 | 완료(임시 통계) | 표본 수와 함께 해석 필요 |
| Student 추천 파이프라인 | 통합 테스트 완료 | PASS | Skill Gap → Readiness → 추천 → JSON |

## 3. 역할 분담과 모델 경계

- 사용자 담당: 데이터 수집, DB 적재, Skill 사전/매핑, JobRole/RoleSkill 구조, 추천 계산 기반.
- 팀원 담당: 프론트/UI, 모델 자체 구현/고급 추론, 학생 경험 Skill 추출, 애매한 JOB Skill/JobRole 추론.
- 원칙: **규칙으로 확실한 것은 먼저 처리하고, 의미 추론이 필요한 것만 모델에 넘긴다.**

### 모델이 해야 하는 것
- 학생 프로젝트/인턴 경험 → Skill 추출
- 애매한 JOB → Skill 추론
- 애매한 JOB → JobRole 분류
- 추천 결과 설명 문장 생성

### 모델이 하지 말아야 하는 것
- 새 canonical Skill을 임의 생성
- 최종 추천 점수나 RoleSkill demand를 임의 결정
- DB canonical Skill을 무시한 자유 텍스트 Skill 생성
- 근거 없는 Skill 생성

## 4. 데이터 모델 핵심

DB는 PostgreSQL + SQLAlchemy 기반이며, 추천 핵심 테이블은 `resource`, `resource_skill`, `job_role`, `resource_job_role`, `role_skill`, `student`, `student_resource`, `student_experience`, `student_skill`, `student_skill_evidence`다.

현재 AI/SW MVP JobRole 8개:
- `AI Engineer`
- `Machine Learning Engineer`
- `Generative AI Engineer`
- `Data Scientist`
- `Data Analyst`
- `Data Engineer`
- `Backend Developer`
- `MLOps Engineer`

## 5. 데이터 수집/매핑 현황

### COURSE
- 건양대학교 2026 인공지능학과 공식 교육과정 PDF 15페이지 처리.
- 로컬 parser + Gemini fallback 구조.
- 고유 교과목 코드 66개 기준 로컬/구조화 결과 100% 일치 수준까지 검증.
- DB COURSE 66개, TAUGHT 매핑 30개.

| 교과목 | Skill |
|---|---|
| 파이썬 프로그래밍 | Python |
| JAVA 프로그래밍 | Java |
| 데이터 분석 기초 | Data Analysis |
| 파이썬 기반 자료구조와 알고리즘 | Python / Algorithm / Data Structure |
| 클라우드컴퓨팅이해 | Cloud |
| 리눅스기초 | Linux |
| AI를위한기초수학및통계 | Statistics |
| 머신러닝및딥러닝 | Machine Learning / Deep Learning |
| 딥러닝영상처리 | Deep Learning / Computer Vision |
| 딥러닝자연어처리 | Deep Learning / Natural Language Processing |

### CERTIFICATE
- Q-Net/DataQ 기반 Resource 100개.
- 공식 SourceDocument 근거가 실제로 존재하는 경우만 Skill 연결.
- 저장 후 총 Skill 매핑 17개.

| 자격증 | Skill |
|---|---|
| SQLD | SQL / Database |
| SQLP | SQL / Database |
| ADsP | Data Analysis / Statistics |
| ADP | Data Analysis / Statistics |
| 빅데이터분석기사 | Data Analysis |
| DAP | Database / Data Modeling |
| DAsP | Database / Data Modeling |

### PROGRAM
- 대전 청년 정책/지원 PROGRAM 7개 확보.
- 취업지원/상담형 PROGRAM은 특정 기술을 가르치지 않을 수 있으므로 Skill 0개가 정상.
- 청년소식 교육 프로그램 수집은 상세 HTML 구조 문제로 중단. searchSeq까지는 확보했으나 MVP 우선순위상 보류.

### JOB
- 초기 14개에서 기존 수집기를 재사용해 27개까지 확대.
- 96~106번은 시설·간호·방사선·상담 등 AI/SW와 무관한 검색 오염으로 비활성화 대상.
- 219 AI플랫폼엔지니어는 모델 검토 가치가 있어 유지.
- JOB → Skill은 공고의 자격/필수 구역을 REQUIRED, 우대 구역을 PREFERRED로 보수적으로 매핑.
- 명시적 Skill 후보 82개 수준까지 확보.

## 6. JobRole과 RoleSkill

- 제목에서 직무가 명확한 경우만 규칙 기반 JobRole 매핑. 27개 중 신규 후보 15개.
- 현재 표본은 Machine Learning Engineer에 집중되어 있으므로 RoleSkill은 **provisional(임시 통계)**로 해석한다.
- 가중치: REQUIRED=1.0, PREFERRED=0.6, RELATED=0.3.
- demand = weighted_count / analyzed_posting_count.

| ML Engineer Skill | demand |
|---|---:|
| Machine Learning | 0.52727 |
| Collaboration | 0.47273 |
| Deep Learning | 0.43636 |
| PyTorch | 0.41818 |
| Statistics | 0.38182 |
| Python | 0.29091 |
| Algorithm | 0.23636 |
| TensorFlow | 0.23636 |
| Git | 0.20000 |
| Computer Vision | 0.18182 |
| Docker | 0.10909 |

## 7. Student / Skill Gap / Recommendation

- 완료 COURSE의 TAUGHT, 완료 CERTIFICATE의 VERIFIED, 완료 PROGRAM의 TAUGHT를 StudentSkill로 동기화.
- 근거는 `student_skill_evidence`에 남겨 왜 해당 Skill을 보유한다고 판단했는지 추적 가능.
- DEMO 학생: student_id=1, 건양대학교 인공지능학과 2학년, 목표 Machine Learning Engineer.
- 초기 보유 Skill: Python, Statistics, Database, Machine Learning.
- 초기 Readiness: 전체 29.3%, 핵심 34.4%.
- Actionable 기준: demand_score >= 0.10.
- 추천 점수: 80% demand 가중 gap coverage + 20% 부족 Skill 개수 coverage.

### StudentExperience 통합 테스트
- DEMO 프로젝트: 딥러닝 이미지 분류 프로젝트.
- 모델/Mock 추출: PyTorch, Deep Learning, Computer Vision, Git (confidence 0.95).
- 반영 후 Readiness: 전체 59.6%, 핵심 69.8%.
- 남은 핵심 Gap: Collaboration, Algorithm, TensorFlow, Docker.
- 현재 추천: AI를위한알고리즘 / 파이썬 기반 자료구조와 알고리즘.

## 8. 팀원 모델 연동 계약

입력 JSON은 `allowed_skills`에 존재하는 기존 Skill ID만 제공하며, 모델은 새 Skill을 만들 수 없다. `evidence_text`는 Experience 원문에 실제로 존재해야 하고, confidence는 0~1 범위다. 서버는 confidence < 0.75를 저장하지 않는다.

```json
{
  "contract_version": "student_experience_skill_v1",
  "student_id": 1,
  "experience_id": 1,
  "mappings": [
    {
      "skill_id": 23,
      "evidence_text": "실제 경험 원문",
      "confidence": 0.95,
      "level": null,
      "reason": "직접 사용했다고 명시됨"
    }
  ],
  "unmapped_skills": []
}
```

## 9. 프론트/팀원용 추천 JSON

```json
{
  "student": {
    "student_id": 1,
    "department_name": "인공지능학과",
    "target_role_name": "Machine Learning Engineer"
  },
  "readiness": {
    "overall": 0.596,
    "actionable": 0.698,
    "actionable_threshold": 0.1
  },
  "owned_skills": [],
  "role_skills": [],
  "missing_skills": [],
  "actionable_missing_skills": [],
  "recommendations": [],
  "warnings": [],
  "metadata": {}
}
```

## 10. 다학과 확장 원칙

1. Skill은 학과 독립적인 canonical entity로 유지한다. 같은 Statistics를 학과별로 중복 생성하지 않는다.
2. 새 학과는 교육과정 수집 → Skill 후보 → Skill/alias 확장 → COURSE→Skill → 관련 JOB → JOB→Skill/JobRole → RoleSkill 순으로 확장한다.
3. 현재 JobRole 8개는 AI/SW seed일 뿐이며, 기계·경영·보건 등 확장 시 새 JobRole을 추가한다.
4. `job_role_mapper.py`의 하드코딩 규칙은 MVP에서는 허용하지만, 다학과 확장 단계에서는 DB 기반 alias/rule로 리팩터링하는 것이 좋다.

## 11. 현재 부족 데이터와 후속 우선순위

- ML Engineer: 표본 11개로 MVP 가능.
- AI Engineer / Data Engineer / Generative AI Engineer / Data Scientist / Data Analyst / Backend Developer / MLOps Engineer: 표본 보강 필요.
- Collaboration / TensorFlow / Docker를 직접 가르치는 Resource 부족.
- PROGRAM 7개는 기본 구조 검증에 충분하나 기술교육형 프로그램을 추가하면 추천 폭이 넓어진다.
- 다른 학과는 아직 미수집. 핵심 알고리즘은 동일하게 재사용 가능.

## 12. 새 데이터가 들어왔을 때 실행 루틴

### JOB 추가 시
```text
search_collector → detail_collector → job_loader
→ job_skill_mapper DRY RUN / --save
→ job_role_mapper DRY RUN / --save
→ role_skill_aggregator DRY RUN / --save
```

### 새 학과 COURSE 추가 시
```text
교육과정 수집 → COURSE DB 적재 → Skill 후보 검토
→ 필요시 Skill/SkillAlias 확장 → course_skill_mapper → TAUGHT 저장
```

## 13. 주요 파일

| 파일 | 역할 |
|---|---|
| `src/collectors/curriculum/konyang_curriculum.py` | 건양대학교 인공지능학과 교육과정 PDF 수집·파싱 |
| `src/collectors/jobs/search_collector.py` | JOB 후보 검색 |
| `src/collectors/jobs/detail_collector.py` | JOB 상세 본문 수집 |
| `src/loaders/job_loader.py` | JOB 검증 및 DB 적재 |
| `src/collectors/programs/daejeon_programs.py` | 대전 청년 정책/지원 PROGRAM 수집 |
| `src/collectors/programs/daejeon_news_programs.py` | 대전 청년소식 PROGRAM 실험용 수집기 |
| `src/mapping/course_skill_mapper.py` | COURSE → Skill(TAUGHT) 매핑 |
| `src/mapping/certificate_skill_mapper.py` | CERTIFICATE → Skill(VERIFIED) 매핑 |
| `src/mapping/job_skill_mapper.py` | JOB → REQUIRED/PREFERRED Skill 보수적 매핑 |
| `src/mapping/job_role_mapper.py` | JOB → JobRole 제목 기반 보수적 매핑 |
| `src/mapping/role_skill_aggregator.py` | JobRole별 Skill 수요 집계(RoleSkill) |
| `src/recommendation_engine.py` | StudentSkill → Skill Gap → 자원 추천 핵심 엔진 |
| `src/recommendation_service.py` | Readiness/추천 결과 통합 및 프론트·팀원용 JSON 출력 |
| `src/mapping/student_experience_skill_bridge.py` | 학생 경험 ↔ 팀원 모델 Skill 추출 계약 및 검증/저장 |
| `src/db/models.py` | DB ORM 모델 |
| `src/db/seed_master.py` | Skill / JobRole 초기 Seed |

## 14. 주요 실행 명령

### COURSE 확인
```powershell
python -m src.collectors.curriculum.konyang_curriculum
python -m src.mapping.course_skill_mapper --show
```

### CERTIFICATE Skill 확인
```powershell
python -m src.mapping.certificate_skill_mapper --show
```

### JOB 수집
```powershell
python -m src.collectors.jobs.search_collector
python -m src.collectors.jobs.detail_collector
python -m src.loaders.job_loader
```

### JOB Skill
```powershell
python -m src.mapping.job_skill_mapper
python -m src.mapping.job_skill_mapper --save
python -m src.mapping.job_skill_mapper --show
```

### JobRole
```powershell
python -m src.mapping.job_role_mapper
python -m src.mapping.job_role_mapper --save
python -m src.mapping.job_role_mapper --show
```

### RoleSkill
```powershell
python -m src.mapping.role_skill_aggregator
python -m src.mapping.role_skill_aggregator --save
python -m src.mapping.role_skill_aggregator --show
```

### 추천
```powershell
python -m src.recommendation_service --student-id 1 --top 10
```

### 추천 JSON
```powershell
python -m src.recommendation_service --student-id 1 --top 10 --json
```

### Experience 목록
```powershell
python -m src.mapping.student_experience_skill_bridge --list --student-id 1
```

### Experience 모델 입력
```powershell
python -m src.mapping.student_experience_skill_bridge --export --experience-id 1
```

### Experience mock
```powershell
python -m src.mapping.student_experience_skill_bridge --mock --experience-id 1
```

### Experience 결과 적용
```powershell
python -m src.mapping.student_experience_skill_bridge --apply --file data/processed/student_experience_mapping_outputs/experience_1_output.json
```

### Student Skill 확인
```powershell
python -m src.mapping.student_experience_skill_bridge --show --student-id 1
```

## 15. Git 체크리스트

```powershell
git status
git branch
git add .
git commit -m "feat: complete CareerQuest skill gap recommendation pipeline"
git push origin inho
```

> `.env`, API Key, 개인 설정 파일은 commit하지 않는다.

## 16. 발표용 한 문장

> CareerQuest는 대학 교육과정, 자격증, 프로그램, 실제 채용공고를 하나의 Skill Graph로 통합하고, 학생의 이수 과목과 프로젝트 경험에서 확인된 Skill을 목표 직무의 시장 요구 Skill과 비교하여 Skill Gap을 계산한 뒤, 부족한 Skill을 채울 수 있는 과목·자격증·프로그램을 추천하는 시스템이다.

## 17. 현재 완료 판정

**MVP 백엔드 핵심 흐름: PASS**

완료 범위: DB 기본 구조 → 수집 → Skill 매핑 → JobRole → RoleSkill → StudentSkill/Evidence → StudentExperience 모델 계약 → Skill Gap → Readiness → 추천 → JSON.

다음 단계는 새로운 핵심 구조 추가보다 **Git 상태 확정 → 팀원 모델 계약 전달 → 프론트 JSON 전달 → 실제 팀 통합 → 부족 데이터 보강** 순서가 적절하다.