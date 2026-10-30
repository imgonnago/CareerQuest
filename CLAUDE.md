# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

CareerQuest AI — 대학생(건양대·대전 지역) 맞춤 취업 준비 로드맵을 만드는 LLM + RAG 에이전트. 학생 프로필과 희망 직무를 받아 요구 역량과 비교하고, 교과목·자격증·공모전·지역 프로그램을 **근거(출처)와 함께** 추천한다. 설계 전체와 파트별 TODO는 `README.md`(작업용 초안)에 있다.

- 현재 구현된 것은 `engine/`(검색 도구·크롤링·근거 풀/근거 카드 스키마·1~3단계 파이프라인)뿐이다. `backend/`, `frontend/`, `db/`, `data/`는 아직 비어 있다.
- 실행 환경: 로컬 Ollama(qwen3.5:9b Q4_K_M, bge-m3 임베딩 1024차원). 호출은 `engine/llm.py`의 `chat()`·`embed()`. **외부 LLM API는 사용하지 않는다.**
- 코드 주석·docstring·커밋 메시지는 한국어로 쓴다.

## 개발 환경

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # NAVER_CLIENT_ID / NAVER_CLIENT_SECRET / KAKAO_REST_API_KEY 등 채우기
```

- 빌드·린트·테스트 설정은 아직 없다. 모듈은 저장소 루트에서 `python -c "from engine.tools.kakao import search_kakao; ..."`처럼 `engine.` 절대 import로 실행한다.
- API 키는 `engine.config.require("KEY")`로 읽는다 (없으면 RuntimeError).
- `docs/samples/*.json`은 각 검색 API·본문 수집 결과의 실제 샘플이다. API를 호출하지 않고 형태를 확인할 때 쓴다.

## 엔진 아키텍처

README의 5단계 파이프라인: (1) 목표 직무 설정 → (2) 근거 추출(LLM ↔ 도구 루프) → (3) 근거 집계 → (4) 우선순위 판단 → (5) 검증(코드). 역할 분담 원칙: **LLM은 도구 선택·문서 읽기·판단, 코드는 도구 실행·저장·세기·검증**(8B 모델 약점 보완).

현재 코드는 (2) 근거 추출의 첫 부분인 **근거 풀** 구축이다.

- `engine/schemas/evidence.py` — `EvidenceDoc`: 검색 결과 1건 = 1개. `doc_id`는 정규화 URL의 SHA1 앞 16자(`text.make_doc_id`)라서 같은 문서는 같은 id. **본문(text)은 담지 않는다** — 본문은 `fetch_fulltext`가 별도로 반환하고, LLM이 읽어 만든 상세 근거 카드가 `doc_id`로 이 항목을 참조한다. `evidence_level`은 `fetch_status == "ok"`일 때만 `fulltext`.
- `engine/tools/naver.py` — 네이버(NCP API HUB) 검색. 네이버는 robots.txt로 RAG 수집을 금지하므로 snippet만 쓰고 `fetch_status="skipped"`.
- `engine/tools/kakao.py` — 카카오(Daum) 검색. blog는 기본적으로 티스토리만 남긴다(`tistory_only`). 생성 시 `crawl.precheck_status`로 초기 상태(`link_only`/`disallowed`/`pending`)를 정한다.
- `engine/tools/crawl.py` — `pending` 문서만 본문 수집: 도메인 정책(`LINK_ONLY_DOMAINS`, `NO_CRAWL_DOMAINS`) → robots.txt(`CareerQuestBot` 기준, 도메인별 캐시, 401/403·요청 실패는 차단 취급) → 도메인별 요청 간격(기본 2초 또는 crawl-delay) → 추출(티스토리는 스킨별 셀렉터, 그 외 trafilatura, 200자 미만이면 실패). 반환은 `(갱신된 EvidenceDoc, text | None)`.
- `engine/tools/requirements.py` — 요구사항 검색 도구. 카카오 소스끼리 순위대로 번갈아 섞어 앞에 두고 네이버(요약만)는 뒤에 보충으로 붙인 뒤 doc_id로 중복 제거(`collect_candidates`, **카카오 1순위**) → 앞에서부터 pending만 `max_fetch`개 본문 수집(`fetch_texts`). 두 단계 사이가 재정렬 모델이 들어갈 자리다. LLM용 `TOOL_SPEC`(Ollama·OpenAI function 형식) 포함.
- **단계별 LLM 분리**: 단계(및 단계 안 역할)마다 LLM 설정·시스템 프롬프트·입출력 스키마를 따로 둔다. 실행은 `engine/pipeline/stageN_*.py`, 프롬프트는 `engine/prompts/stageN_*.py`(입출력 형식을 docstring에 적음), 설정은 모듈 상단의 `LLMConfig`(`engine/llm.py`: model·temperature·think·num_ctx). 호출은 `chat(messages, config, tools=, format=)`.
- `engine/pipeline/stage1_job.py` — (1) 목표 직무 설정. 학생 입력(`schemas/job.py`의 `StudentInput`, 임시 형식) → 직무 설정 LLM이 `TargetJobDraft` JSON(직무명·신입/인턴/경력·세부 관심 분야(원문/고친 용어/검색 키워드)·학생이 말한 기업(원문/공식명/검색 키워드, `Company`)·보유 기술·자격증·프로젝트(원문/표준 이름, `Held`)·직무 검색 키워드) → 관심 분야 확인 LLM(새 대화, 관심 분야마다 관심/경험 `InterestCheck`, 경험이면 projects로 옮김) → 코드가 원문(관심 분야·기업·보유 항목)이 학생 입력에 실제로 있는지 대조, 폼 칸 보유 항목을 합치고, 희망 직무·기업명·보유 항목과 겹치는 관심 분야를 빼서 `TargetJob`. 2단계에는 기업 공식명을 넘긴다. 표준 직무 목록은 `catalog` 인자로 받을 자리만 있다.
- **프롬프트 작성 요령(9B 모델)**: 시스템 프롬프트 규칙을 놓치는 일이 잦다. 자주 틀리는 규칙은 사용자 메시지 끝(출력 직전)에 한 번 더 적는다. 선택·추출처럼 짧은 판단은 긴 대화를 이어 가지 말고 새 대화로 필요한 입력만 준다. 사고 기능(think)은 정확도가 오르지만 수십 초가 걸려 기본은 끈다.
- `engine/pipeline/stage2_evidence.py` — (2) 근거 추출. 입력은 1단계 `TargetJob`(`make_target`이 텍스트로 만듦). 학생에게 줄 정보를 **가이드 슬롯**(`SLOTS`: 교과목 3·교내 프로그램 2·채용 요건 4·자격증 3·공모전·대외활동 2·추천 포트폴리오 2, 수는 카드가 아니라 고유 근거 문서 수)으로 정해 두고, 한 바퀴마다 **부족한 슬롯마다 따로** 계획 LLM(새 대화, 슬롯 설명·검색 방향 `SLOT_TASKS`와 그 슬롯에서 쓴 검색어를 주고 `SearchPlan` JSON으로 검색어만 받음, 검색은 코드가 실행하고 문서마다 슬롯을 기록) → 선택 LLM(새 대화, 그 슬롯 검색 결과 중 안 읽은 문서 제목 목록에서 `SELECT_PER_SLOT`건을 `DocSelection` JSON으로) → 코드(고른 문서만 본문 수집) → 읽기 LLM(새 대화, 본문 전체를 `READ_BUDGET_CHARS` 단위로 문서째 묶고 문서마다 고른 슬롯을 참고로 적어 `CardDraftList` JSON) → 코드(`verify`: quote를 공백 무시하고 원문과 대조, 통과한 것만 `EvidenceCard`). 이 바퀴를 **추출 루프**로 슬롯이 다 찰 때까지 반복(`MAX_ROUNDS` 3·`MAX_SECONDS` 90, 바퀴 시작 전 확인). 한 번에 모든 슬롯을 계획·선택시키면 9B 모델이 일부 슬롯만 하거나 이전 계획을 되풀이해서 나눴다. 교내 슬롯(교과목·교내 프로그램)은 건양대 문서(`is_school_doc`)의 카드만 남긴다 — RAG가 없어 지금은 검색으로 채우고, RAG를 붙인 뒤에는 RAG 정보가 오래됐을 때 보충하는 경로. 공모전·대외활동은 건양대 교내 대회와 외부 공모전·해커톤·대외활동을 여러 방면으로 검색(`FIRST_QUERIES`). 검색어는 "대상(건양대·기업 공식명·직무·상위 분야) + 찾는 영역" 2~5단어. JSON 출력은 Ollama `format`에 pydantic 스키마를 넘겨 강제하고, D번호 칸은 정규식 패턴으로 묶는다(안 묶으면 제목까지 붙여 씀).
- `engine/pipeline/stage3_aggregate.py` — (3) 근거 집계. 입력은 2단계 `ExtractionResult`. 코드((doc_id, quote) 중복 제거, 근거 번호 C1…) → 역할 LLM(카드가 나온 문서마다 채용공고·우대사항정리·자격증정보·프로젝트추천 등 `DocRole`, 제목·요약·카드 item으로 판단) → 이름 뽑기 LLM(item마다 짧은 Skill 이름) → 코드(표기 병합, bge-m3 임베딩 후보 묶음) → 합치기 LLM(후보만) → 코드(Skill별 가이드 슬롯·고유 문서 수·분류별·역할별·기업·근거 번호 `SkillRow`, 슬롯 순 정렬). 기업정보 카드는 집계에서 빼고, Gap은 DB가 없어 아직 없음. `render_table`이 표 텍스트.
- **근거 풀 vs 근거 카드**: 근거 풀은 코드가 검색 결과를 전부 담는 원문 목록, 근거 카드는 LLM이 그중 근거만 골라 만든 것. "근거를 못 찾으면 넣지 않는다"는 카드 기준이다.
- `engine/tools/text.py` — 태그/엔티티 정리, URL 정규화(티스토리 `/m/` 제거, 추적 파라미터 제거), 날짜를 `YYYYMMDD` 정수로.

산출물 이름(README 기준): 근거 풀(원문 전체) → 상세 근거 카드(인용 문장) → 근거 집계 표(숫자·근거 번호) → 근거 순위 카드(순위·행동·근거 번호).

## 지켜야 할 규칙

- **LLM은 본문 전체를 읽는다.** 코드로 문단을 골라내거나 본문을 잘라 줄이지 말 것. 근거 풀 항목에 본문을 다시 넣지 말 것.
- **데이터 우선순위: RAG에 있는 건양대학교 자료(교육과정·로드맵·수업계획서·비교과)가 최우선이다.** 검색 API·크롤링 결과는 RAG에 없거나 부족한 근거를 보충하는 용도다. 하지만 검색 API 및 크롤링 결과도 적극 활용하여 최대한 많고 정확한 정보 제공을 목표로 한다. 
- 수집 방법 순서: API → 크롤링(robots.txt·이용약관 확인한 사이트만) → 수작업.
- 채용 플랫폼(잡코리아·사람인·링커리어·캐치·원티드 등) 상세 페이지는 크롤링하지 않고 링크만 제공한다. 합격 자소서는 수집하지 않는다.
- 날짜(작성일·마감일)는 정수 `YYYYMMDD`로 저장한다.
- 코드 수정·테스트 중 발견한 문제점이나 고쳐야 할 부분은 루트의 `problem.txt`에 번호를 붙여 추가한다. 고친 항목은 제목 옆에 `(해결)`을 붙이고 "해결" 블록(해결: 날짜·한 줄 요약 / 방법: 어디를 어떻게 바꿨고 왜 / 확인: 테스트 결과·남은 한계)으로 처리 방법을 요약한다. 작업 시작 전에 미해결 항목을 확인한다.
- 커밋은 사용자가 직접 한다. 파일 수정까지만 하고 commit/push 하지 않는다.
