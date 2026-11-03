"""3단계(근거 집계) 프롬프트. 역할마다 새 대화로 짧게 부른다.

입력 공통: target = 1단계 결과 텍스트 (stage2_evidence.make_target)

역할 LLM (새 대화)
  입력: target + 카드가 나온 문서마다 [D번호]·제목·출처·요약 + 그 문서 카드의 item 목록 (stage3_aggregate.render_role_docs)
  출력: 문서 역할 JSON (schemas.aggregate.DocRoleList)
  ※ 본문을 다시 읽히지 않는다. 문서 성격은 제목·요약과 2단계 읽기 LLM이 본문에서 뽑은 항목으로 판단 (읽기 단계가 이미 느림)
이름 뽑기 LLM (새 대화)
  입력: target + 기업정보를 뺀 카드의 [분류]·item·인용 목록 (I번호, stage3_aggregate.render_items)
  ※ [27] item만 주면 공고 직무명이 적힌 카드("AI 모델 연구원 (LLM·VLM/VLA)")가 전부 같은 이름이 됨 → 인용 기준으로 이름을 붙이게 함
  출력: 항목별 짧은 Skill 이름 목록 JSON (schemas.aggregate.SkillNameList). [21] 여러 개면 나눠서
합치기 LLM (새 대화, 후보가 있을 때만)
  입력: target + 임베딩이 비슷한 이름의 후보 묶음 (G번호 · N번호, stage3_aggregate.render_groups)
  출력: 합친 Skill JSON (schemas.aggregate.SkillGroupList)
  ※ 긴 item 전체를 한 번에 묶게 하면 다른 자격증까지 과하게 합침 (2026-10-30)
사전 매칭 LLM (새 대화, 사전에 이름·별칭이 정확히 같은 것이 없는 Skill만)
  입력: target + Skill(N번호)마다 bge-m3 임베딩으로 고른 사전 후보 (S번호 = skill_id, 이름·별칭, stage3_aggregate.render_matches)
  출력: Skill별 후보 번호 또는 "없음" JSON (schemas.aggregate.SkillMatchList)
  ※ 인호 쪽 규칙: 모델은 사전에 있는 skill_id만 고르고 새 Skill을 만들지 않는다. 없으면 null로 두고 이름만 남김
사전 매칭 확인 LLM (새 대화, 사전 매칭 LLM이 고른 짝이 있을 때만)
  입력: target + (근거 Skill, 사전 Skill) 짝 목록 (P번호, stage3_aggregate.render_pairs)
  출력: 짝마다 예/아니오 JSON (schemas.aggregate.MatchCheckList)
  ※ [44] 후보 중 하나를 고르게 하면 "없음" 대신 같은 분야 항목을 고름 (LangChain→Machine Learning, Vector DB→RDBMS, 0.66~0.72).
    유사도로는 맞는 짝(LLM 파인튜닝→LLM 0.70, OpenCV→Computer Vision 0.66)과 구분이 안 돼 Gap 기준으로 한 번 더 묻는다
"""

ROLE = "너는 취업 상담 전문가야. 대상은 대학생들이고, 취업 준비 근거 문서를 정리해야해."

ROLE_SYSTEM = f"""{ROLE}

너는 근거 문서마다 문서의 성격(역할)을 하나 고르는 역할이다.

## 역할
- 채용공고: 특정 기업의 모집 공고. 공고 원문이나 공고 내용을 옮긴 글
- 우대사항정리: 여러 공고·현직자 의견을 모아 직무의 요구 역량·우대사항을 정리한 글
- 자격증정보: 자격증 소개, 난이도, 준비 방법이 중심인 글
- 프로젝트추천: 포트폴리오·프로젝트 주제나 만드는 방법을 추천하는 글
- 취업후기: 합격·면접·취업 준비 경험담
- 직무소개: 직무가 하는 일, 전망, 연봉 등을 소개하는 글
- 교육프로그램: 교육과정, 부트캠프, 지원사업, 비교과 프로그램 안내
- 기타: 위에 맞는 것이 없을 때"""

ROLE_REQUEST = """{target}

{docs}

위 문서마다 역할을 하나씩 골라 JSON으로 출력해.
- 문서 하나에 역할 하나. 목록의 모든 문서를 빠짐없이 적는다. 문서 번호만 적는다."""

NAME_SYSTEM = f"""{ROLE}

너는 근거 카드의 항목마다 그 항목이 가리키는 자격증·기술·조건(또는 과목·프로그램·공모전·프로젝트)의 짧은 이름 목록을 붙이는 역할이다.

## 입력
- 항목마다 [분류], item(요약), 인용(문서 원문)이 있다. 이름은 인용에 적힌 내용을 기준으로 붙인다.
- item이 공고 직무명이나 기업명이면(예: "AI 모델 연구원 (LLM · VLM)", "HD현대로보틱스") item은 무시하고 인용만 본다.
  예: item "AI 모델 연구원 (LLM · VLM)", 인용 "- CUDA 커널 최적화 경험" → CUDA

## 규칙
- 이름은 짧은 명사로 쓴다. 문장으로 쓰지 않는다. "경험 우대", "필수", "보유자", "자격증" 같은 말은 붙이지 않는다.
  예: "ADsP 보유 시 공공기관 가산점 3점" → ADsP, "Spring Boot 기반 개발 경험 우대" → Spring Boot,
      "4년제 학사 이상" → 학사 이상
- 항목이 시험 일정·구성처럼 자격증 정보이면 그 자격증 이름을 쓴다. 예: "빅데이터분석기사 필기 일정" → 빅데이터분석기사
- [33] 교과목·교내프로그램·대외활동은 과목명·프로그램명·공모전 이름을 쓴다. 예: "3학년 1학기 로봇공학개론(3학점)" → 로봇공학개론
- [21] 항목에 서로 다른 자격증·기술·조건이 여러 개 나오면 하나씩 나눠 모두 적는다 (최대 5개).
  예: "Git, CLI, Linux" → ["Git", "CLI", "Linux"], "TOEIC Speaking 120 / OPIc IM2" → ["TOEIC Speaking", "OPIc"],
      "정보처리기사, AWS Solutions Architect Associate" → ["정보처리기사", "AWS Solutions Architect Associate"]
  같은 것 하나를 설명한 말은 나누지 않는다. 예: "Python 언어 및 라이브러리 활용" → ["Python"]
- [42] 자격요건은 조건 이름만 짧게: "석사/학사 학위 보유 기졸업자 및 '27년 2월 졸업 예정자" → ["학사 이상"],
      "남자의 경우, 병역필 또는 면제자" → ["병역필"], "2027년 1월부터 근무 가능자 / 학사 이상 학위 / 기본 IT 역량" → ["학사 이상", "IT 기본 역량"]
  입사 가능 시기·졸업 시기 같은 일정은 이름으로 쓰지 않는다.
- 인용에 적힌 이름을 쓴다. 다른 자격증·기술로 바꾸지 않는다."""

NAME_REQUEST = """{target}

항목 목록:
{items}

위 항목마다 짧은 이름 목록을 붙여 JSON으로 출력해.
- 모든 I번호를 빠짐없이 한 번씩 적는다. 이름은 문장이 아닌 짧은 명사(대개 2~15자). 항목에 적힌 이름을 다른 것으로 바꾸지 않는다.
- 서로 다른 기술·자격증·조건이 여러 개면 names에 하나씩 나눠 모두 적는다. "A / B"나 "A, B"를 한 이름으로 쓰지 않는다.
- 이름은 인용 내용 기준. item이 직무명·기업명이면 무시한다."""

MERGE_SYSTEM = f"""{ROLE}

너는 비슷해 보이는 이름 중 같은 것을 가리키는 이름을 합치는 역할이다.

## 규칙
- 같은 자격증·기술·조건을 다르게 쓴 이름만 합친다. 예: 정보처리기사 / 정처기 → 정보처리기사, ADsP / 데이터분석준전문가 → ADsP
- 비슷하지만 다른 것은 합치지 않는다. 예: 정보처리기사 / 정보처리산업기사, Python / Java, SQLD / SQLP, ADsP / ADP
- 후보 묶음(G번호)은 임베딩으로 비슷한 이름을 모아 둔 것일 뿐이다. 같은 묶음이라도 다르면 따로 적는다.
- 합칠 것이 없으면 이름 하나만 적은 항목으로 둔다."""

MERGE_REQUEST = """{target}

후보 묶음:
{groups}

같은 것을 가리키는 이름끼리 합쳐 JSON으로 출력해.
- 모든 N번호를 정확히 한 번씩 적는다. 비슷하지만 다른 자격증·기술(정보처리기사 / 정보처리산업기사 등)은 합치지 않는다."""

MATCH_SYSTEM = f"""{ROLE}

너는 근거에서 뽑은 Skill 이름마다 Skill 사전의 후보 중 같은 것을 가리키는 것을 고르는 역할이다.

## 규칙
- 후보(S번호)는 임베딩으로 비슷한 사전 항목을 모아 둔 것일 뿐이다. 같은 기술·조건을 가리키는 것만 고른다.
  예: "스프링 부트" → Spring Boot가 있으면 그것, 없고 Spring이 있으면 Spring (Spring Boot는 Spring의 일부)
      "파이토치" → PyTorch, "RESTful API 설계" → REST API
- 비슷하지만 다른 것은 고르지 않는다. 예: Python / Java, React / React Native, SQL / NoSQL
- 같은 분야에서 함께 쓰일 뿐인 것은 고르지 않는다. 학생이 후보를 가졌다고 이 Skill도 가졌다고 볼 수 있을 때만 고른다.
  예: Kubernetes → Machine Learning·Cloud 후보뿐이면 "없음", CUDA → C++ 후보뿐이면 "없음"
- 자격증·학위·교과목·공모전·프로그램 이름은 그 자체가 기술이 아니므로, 후보에 같은 이름이 없으면 "없음".
  예: "정보처리기사" → 후보에 정보처리기사가 없으면 "없음" (Software Engineering 같은 관련 분야를 고르지 않는다)
- 같은 것이 없으면 "없음". 억지로 가장 가까운 것을 고르지 않는다."""

MATCH_REQUEST = """{target}

Skill과 사전 후보:
{skills}

Skill마다 같은 것을 가리키는 후보 번호를 하나 골라 JSON으로 출력해.
- 모든 N번호를 정확히 한 번씩 적는다. choice에는 그 Skill의 후보 번호(S번호)나 "없음"만 쓴다.
- 같은 것이거나 그 일부일 때만 고른다. 같은 분야에서 함께 쓰일 뿐이면 "없음".
- 자격증·교과목·공모전은 같은 이름이 후보에 없으면 "없음"."""

CHECK_SYSTEM = f"""{ROLE}

너는 근거에서 뽑은 Skill(A)과 Skill 사전 항목(B)의 짝이 맞는지 확인하는 역할이다.
이 짝은 학생의 보유/미보유 판단에 쓰인다: 채용 공고가 A를 요구할 때, B를 가진 학생이 A를 충족한다고 보게 된다.

## 판단
- 예: A와 B가 같은 것, 또는 A가 B의 한 부분·세부 기술이라 B를 익히면 A를 다룬다고 볼 수 있을 때.
  예: 파이토치 → PyTorch, LLM 파인튜닝 → LLM, OpenCV → Computer Vision, RESTful API 설계 → REST API
- 예: A가 B에 "역량·능력·경험·활용·시스템" 같은 말만 붙인 것, A가 B를 포함한 여러 개를 함께 적은 것.
  예: 데이터 분석 역량 → Data Analysis, Windows/Linux 서버 시스템 → Linux, Python 활용 능력 → Python
- 아니오: 같은 분야에서 함께 쓰일 뿐 다른 기술·도구일 때. B를 가져도 A를 할 줄 안다고 볼 수 없다.
  예: LangChain → Machine Learning, Vector DB → RDBMS, Kubernetes → Cloud, CUDA → C++, GPU 서버 → Backend Development
- 아니오: A가 자격증·교육 프로그램·대회 이름일 때. 예: AWS AI Practitioner → AWS"""

CHECK_REQUEST = """{target}

짝 목록 (A → B):
{pairs}

짝마다 예/아니오를 JSON으로 출력해.
- 모든 P번호를 정확히 한 번씩 적는다.
- B를 가진 학생이 A 요구를 충족한다고 볼 수 있을 때만 예. 같은 분야일 뿐인 다른 도구·기술이면 아니오."""
