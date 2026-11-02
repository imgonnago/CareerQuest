# CareerQuest Mapping

CareerQuest의 현재 운영용 Mapping 모듈입니다.

## 현재 사용 중인 Mapping

### course_skill_mapper.py
교과목(COURSE)을 Skill에 연결합니다.

- relation_type: TAUGHT
- 백엔드 데이터 전처리 영역
- 모델팀 작업 대상 아님

### certificate_skill_mapper.py
자격증(CERTIFICATE)을 Skill에 연결합니다.

- relation_type: VERIFIED
- 공식 근거가 확인된 Skill 중심
- 모델팀 작업 대상 아님

### job_skill_mapper.py
채용공고의 명시적인 자격요건/우대사항에서 Skill을 추출합니다.

- REQUIRED
- PREFERRED
- 규칙 기반
- 모델팀 작업 대상 아님

### job_role_mapper.py
채용공고 제목에서 직무가 명확한 경우 표준 JobRole에 연결합니다.

현재 AI/SW MVP JobRole을 대상으로 합니다.

### role_skill_aggregator.py
JOB의 Skill과 JobRole을 집계하여 직무별 Skill 수요도(RoleSkill)를 생성합니다.

### student_experience_skill_bridge.py

모델팀과 직접 연결되는 핵심 파일입니다.

흐름:

StudentExperience
→ Model Input
→ Model
→ Model Output
→ Validation
→ StudentSkill
→ StudentSkillEvidence

모델은 DB의 기존 Skill ID만 선택합니다.

새 Skill을 임의로 생성하지 않습니다.

## 모델팀 MVP 담당 범위

현재 모델팀의 핵심 담당 범위는:

StudentExperience → Skill Mapping

입니다.

COURSE / CERTIFICATE / 명시적 JOB Skill Mapping,
RoleSkill 집계,
Skill Gap 계산,
Recommendation 계산은 백엔드에서 처리합니다.

현재 Skill 사전은 AI/SW MVP용 초기 사전이며,
향후 다른 학과가 추가될 때 Skill 사전도 확장됩니다.