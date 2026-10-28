# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

CareerQuest AI — 대학생(건양대·대전 지역) 맞춤 취업 준비 로드맵을 만드는 LLM + RAG 에이전트. 학생 프로필과 희망 직무를 받아 요구 역량과 비교하고, 교과목·자격증·공모전·지역 프로그램을 **근거(출처)와 함께** 추천한다. 설계 전체와 파트별 TODO는 `README.md`(작업용 초안)에 있다.

- 현재 구현된 것은 `engine/`(검색 도구·크롤링·근거 풀 스키마)뿐이다. `backend/`, `frontend/`, `db/`, `data/`, `engine/pipeline/`, `engine/prompts/`는 아직 비어 있다.
- 실행 환경: 로컬 Ollama(Llama 3.1 8B 4bit 기준, bge-m3 임베딩). **외부 LLM API는 사용하지 않는다.**
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
- `engine/tools/requirements.py` — 요구사항 검색 도구. 네이버·카카오 소스를 순위대로 번갈아 섞고 doc_id로 중복 제거(`collect_candidates`) → 앞에서부터 pending만 `max_fetch`개 본문 수집(`fetch_texts`). 두 단계 사이가 재정렬 모델이 들어갈 자리다. LLM용 `TOOL_SPEC`(Ollama·OpenAI function 형식) 포함.
- `engine/tools/text.py` — 태그/엔티티 정리, URL 정규화(티스토리 `/m/` 제거, 추적 파라미터 제거), 날짜를 `YYYYMMDD` 정수로.

산출물 이름(README 기준): 근거 풀(원문 전체) → 상세 근거 카드(인용 문장) → 근거 집계 표(숫자·근거 번호) → 근거 순위 카드(순위·행동·근거 번호).

## 지켜야 할 규칙

- **LLM은 본문 전체를 읽는다.** 코드로 문단을 골라내거나 본문을 잘라 줄이지 말 것. 근거 풀 항목에 본문을 다시 넣지 말 것.
- **데이터 우선순위: RAG에 있는 건양대학교 자료(교육과정·로드맵·수업계획서·비교과)가 최우선이다.** 검색 API·크롤링 결과는 RAG에 없거나 부족한 근거를 보충하는 용도다. 하지만 검색 API 및 크롤링 결과도 적극 활용하여 최대한 많고 정확한 정보 제공을 목표로 한다. 
- 수집 방법 순서: API → 크롤링(robots.txt·이용약관 확인한 사이트만) → 수작업.
- 채용 플랫폼(잡코리아·사람인·링커리어·캐치·원티드 등) 상세 페이지는 크롤링하지 않고 링크만 제공한다. 합격 자소서는 수집하지 않는다.
- 날짜(작성일·마감일)는 정수 `YYYYMMDD`로 저장한다.
- 코드 수정·테스트 중 발견한 문제점이나 고쳐야 할 부분은 루트의 `problem.txt`에 번호를 붙여 추가한다. 고친 항목은 제목 옆에 `(해결)`을 붙이고 어떻게 고쳤는지 한 줄 남긴다. 작업 시작 전에 미해결 항목을 확인한다.
- 커밋은 사용자가 직접 한다. 파일 수정까지만 하고 commit/push 하지 않는다.
