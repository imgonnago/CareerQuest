# CareerQuest Mapping

CareerQuest의 데이터 및 Skill 연결 모듈입니다.

## 기본 원칙

CareerQuest의 Skill은 PostgreSQL의 `skill` 테이블을
Canonical Skill Dictionary로 사용합니다.

AI 모델은 임의의 Skill ID를 생성하지 않고,
CareerQuest Skill DB에 존재하는 `skill_id`를 사용합니다.

---

## 교과목 / 자격증 Mapping

### course_skill_mapper.py

교과목(COURSE)을 Skill에 연결합니다.

- relation_type: `TAUGHT`
- 명확한 문자열 관계는 코드 기반
- 애매한 의미 관계는 로컬 Qwen이 보조 판단
- 새로운 Skill을 임의로 생성하지 않음

### certificate_skill_mapper.py

자격증(CERTIFICATE)을 Skill에 연결합니다.

- relation_type: `VERIFIED`
- 공식 근거가 확인된 Skill 중심

---

## Student Experience Mapping

### student_experience_skill_bridge.py

학생의 프로젝트, 인턴, 연구 등의 자연어 경험을
기존 CareerQuest Skill ID에 연결하기 위한
모델 연동 Bridge입니다.

흐름:

StudentExperience
→ Model Input
→ Qwen
→ Model Output
→ Validation
→ StudentSkill
→ StudentSkillEvidence

모델은 기존 Skill DB의 `skill_id`만 반환합니다.

---

## Skill Gap

최종 Skill Gap의 기준 Skill 목록은
모델팀의 근거 수집 / 집계 파이프라인에서 생성합니다.

흐름:

Evidence 수집
→ Qwen 근거 분석
→ SkillRow
→ CareerQuest Skill DB의 skill_id 부여
→ GAP Service
→ StudentSkill 비교

백엔드 GAP Service는 다음만 담당합니다.

1. 전달받은 `skill_id`가 실제 Skill DB에 존재하는지 검증
2. StudentSkill에 동일한 `skill_id`가 존재하는지 확인
3. 미보유 Skill 판별
4. 요구 수준 정보가 존재하는 경우 수준부족 판별

백엔드는 최종 추천 순위나 추천 이유를 결정하지 않습니다.

---

## 최종 추천

SkillRow + Skill Gap + 학생 정보 등을
AI 모델이 종합적으로 판단합니다.

AI 모델 담당:

- 추천 대상 선택
- 추천 우선순위
- 추천 이유
- 학습 / 진로 로드맵

백엔드는 추천 결과를 결정하는 자체 점수식을 사용하지 않습니다.