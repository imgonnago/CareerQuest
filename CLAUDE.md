# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

CareerQuest AI — 대학생(건양대·대전 지역) 맞춤 취업 준비 로드맵을 만드는 LLM + RAG 에이전트. 학생 프로필과 희망 직무를 받아 요구 역량과 비교하고, 교과목·자격증·공모전·지역 프로그램을 **근거(출처)와 함께** 추천한다. 설계 전체와 파트별 TODO는 `README.md`(작업용 초안)에 있다.

- 구현된 것: `engine/`(검색 도구·크롤링·근거 풀/근거 카드 스키마·1~5단계 파이프라인과 결과 생성·RAG `engine/rag/`), `backend/app.py`(하나로 합친 API 서버: 분석·업로드·RAG 검색 + `main.py` 라우트), `main.py`(인호: DB 조회·Gap 계산 API, backend/app.py가 그대로 붙임), `src/`(인호: 수집기·로더·Skill 매핑·Gap·DB 모델), `alembic/`(DB 스키마). 프론트는 AWS 서버(`/var/www/html`)에서 따로 만들기로 해서 저장소의 `frontend/`는 비어 있다(재열 UI는 병합 충돌을 피하려고 일부러 지움 — 되살리지 말 것). `db/`는 비어 있고 `data/raw`·`data/processed`는 git에서 제외.
- 실행 환경: 로컬 Ollama(qwen3.5:9b Q4_K_M, bge-m3 임베딩 1024차원). 호출은 `engine/llm.py`의 `chat()`·`embed()`. **외부 LLM API는 사용하지 않는다.**
- 코드 주석·docstring·커밋 메시지는 한국어로 쓴다.

## 개발 환경

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # NAVER_CLIENT_ID / NAVER_CLIENT_SECRET / KAKAO_REST_API_KEY 등 채우기
```

- DB: `docker compose up -d` → `alembic upgrade head`. pgvector 확장은 마이그레이션이 만든다.
- 평가: `python -m eval.agent_eval --suite students|truthfulqa|compare|info|all --repeats N --limit M` (파이프라인 전체를 for 문으로, 결과는 `eval/results/`, [68]). `--suite info`는 건양대·자격증 한국어 20문항(`eval/data/info_questions.json`)을 파이프라인과 모델 단독으로 돌림([75]), `--limit`는 학생 수에 안 걸림(`--students-limit`). 평가 전에 DB를 켤 것 — 꺼져 있으면 RAG·Skill 사전 없이 돈다. TruthfulQA 판정은 Claude Opus 5.5가 Claude Code에서 `truthfulqa_review.jsonl`을 읽고 `judgments.jsonl`에 써서 `--score`로 집계한다(평가 전용, 서비스 실행은 외부 LLM 없음). `verdict`는 참조 목록 기준, `fabricated`(지어낸 내용)는 판정자 지식 기준.
- 빌드·린트·테스트 설정은 아직 없다. 모듈은 저장소 루트에서 `python -c "from engine.tools.kakao import search_kakao; ..."`처럼 `engine.` 절대 import로 실행한다.
- API 키는 `engine.config.require("KEY")`로 읽는다 (없으면 RuntimeError).
- `docs/samples/*.json`은 각 검색 API·본문 수집 결과의 실제 샘플이다. API를 호출하지 않고 형태를 확인할 때 쓴다.

## 엔진 아키텍처

맨 앞에 [66] **0단계 입력 판단**(`engine/pipeline/stage0_route.py`)이 있다. 순서: 위기 판단(`check_crisis`, [70] LLM 주도, 명백한 표현은 `CRISIS_CERTAIN` 안전망, LLM 실패 시 `CRISIS_FALLBACK`) → [71] 대화 정리(`resolve_history`) → 후속 판단(`check_followup`) → 입력 판단 LLM(취업 가이드 / 정보 질문 / 진로 고민 / 일반 대화 + `has_goal`) → 위기 안내 뒤 흐름 유지(`keep_crisis`). 취업 가이드는 아래 1~5단계(목표가 없으면 되묻기 `clarify_answer`, LLM이 쓰고 실패 시 고정 문구), 정보 질문은 `answer_info`([75] `InfoAgent`: 한 대화에서 LLM이 `search_rag`·`search_requirements`·`read_documents`를 직접 호출하고 본문을 읽어 답변 [S번호], 답을 내보내기 전 검토 LLM(`review_answer`, [78])이 다른 대상·과정 설명을 돌려보냄, 읽은 문서가 없으면 `no_docs_answer`), 진로 고민은 `answer_counsel`(검색 없음), 후속 질문은 `answer_followup`(이전 추천·근거만 읽음, 검색 안 함), 일반 대화는 `answer_chat`(인사·개념 설명·코드·글 다듬기, [72] 도구 없이 모델이 바로 답함, 출처 없음. [83] 도구를 물으면 답하도록 프롬프트에 도구 목록·한계를 `_tool_lines()`가 TOOL_SPEC에서 넣음). **위기 답변(`crisis_answer`)은 코드가 가진 문장만 나간다**: LLM은 공감 문장을 고르기만 하고 안전 확인·상담 번호는 고정. LLM이 쓰면 위험한 문장이 검사를 빠져나갔다([70][71]). 어느 경로든 `GuideResult.answer`·`sources`·`kind`.

채팅([71]): 대화는 서버에 저장하지 않고 프론트가 `/analyze`의 `history`(`engine/schemas/chat.py`의 `ChatTurn`, assistant 차례에 받은 `guide` 그대로)로 매번 보낸다. `student.question`이 지금 메시지. [87] LLM에 줄 때 도우미 차례에는 `answer_memo`가 guide에서 만든 "만든 방법 + 출처(제목·구분·링크)" 메모를 붙인다(일반 대화·진로 고민·되묻기, 대화 정리 LLM은 제외). [86] 출처를 묻는 말(`SOURCE_QUESTION`)은 코드가 후속 질문으로 보낸다.

README의 5단계 파이프라인: (1) 목표 직무 설정 → (2) 근거 추출(LLM ↔ 도구 루프) → (3) 근거 집계 → (4) 우선순위 판단 → (5) 검증(코드). 역할 분담 원칙: **LLM은 도구 선택·문서 읽기·판단, 코드는 도구 실행·저장·세기·검증**(8B 모델 약점 보완).

(1)~(5)·결과 생성·RAG(`engine/rag/`)가 다 있다. 전체 실행은 `engine/pipeline/run.py`의 `run_pipeline`(`python -m engine.pipeline.run student.json [--student-id N] [--out x.json]`). 2단계 끝 `apply_caps`가 근거 카드를 상한으로 줄이고, 4단계가 그 안에서 학생에게 줄 순서·할 일을 정한다.

- `engine/schemas/evidence.py` — `EvidenceDoc`: 검색 결과 1건 = 1개. `doc_id`는 정규화 URL의 SHA1 앞 16자(`text.make_doc_id`)라서 같은 문서는 같은 id. **본문(text)은 담지 않는다** — 본문은 `fetch_fulltext`가 별도로 반환하고, LLM이 읽어 만든 상세 근거 카드가 `doc_id`로 이 항목을 참조한다. `evidence_level`은 `fetch_status == "ok"`일 때만 `fulltext`.
- `engine/tools/naver.py` — 네이버(NCP API HUB) 검색. 네이버는 robots.txt로 RAG 수집을 금지하므로 snippet만 쓰고 `fetch_status="skipped"`.
- `engine/tools/kakao.py` — 카카오(Daum) 검색. blog는 기본적으로 티스토리만 남긴다(`tistory_only`). 생성 시 `crawl.precheck_status`로 초기 상태(`link_only`/`disallowed`/`pending`)를 정한다.
- `engine/tools/crawl.py` — `pending` 문서만 본문 수집: 도메인 정책(`LINK_ONLY_DOMAINS`, `NO_CRAWL_DOMAINS`) → robots.txt(`CareerQuestBot` 기준, 도메인별 캐시, 401/403·요청 실패는 차단 취급) → 도메인별 요청 간격(기본 2초 또는 crawl-delay) → 추출(티스토리는 스킨별 셀렉터, 그 외 trafilatura, 200자 미만이면 실패). 반환은 `(갱신된 EvidenceDoc, text | None)`.
- `engine/tools/requirements.py` — 요구사항 검색 도구. 카카오 소스끼리 순위대로 번갈아 섞어 앞에 두고 네이버(요약만)는 뒤에 보충으로 붙인 뒤 doc_id로 중복 제거(`collect_candidates`, **카카오 1순위**) → 앞에서부터 pending만 `max_fetch`개 본문 수집(`fetch_texts`). 두 단계 사이가 재정렬 모델이 들어갈 자리다. LLM용 `TOOL_SPEC`(Ollama·OpenAI function 형식) 포함.
- **단계별 LLM 분리**: 단계(및 단계 안 역할)마다 LLM 설정·시스템 프롬프트·입출력 스키마를 따로 둔다. 실행은 `engine/pipeline/stageN_*.py`, 프롬프트는 `engine/prompts/stageN_*.py`(입출력 형식을 docstring에 적음), 설정은 모듈 상단의 `LLMConfig`(`engine/llm.py`: model·temperature·think·num_ctx). 호출은 `chat(messages, config, tools=, format=)`.
- `engine/pipeline/stage1_job.py` — (1) 목표 직무 설정. 학생 입력(`schemas/job.py`의 `StudentInput`, 임시 형식. `documents` = 업로드 본문, 전체를 붙여 읽히고 문서 속 기업은 목표 기업으로 안 받음) → 직무 설정 LLM이 `TargetJobDraft` JSON(직무명·신입/인턴/경력·세부 관심 분야(원문/고친 용어/검색 키워드)·학생이 말한 기업(원문/공식명/검색 키워드, `Company`)·보유 기술·자격증·프로젝트(원문/표준 이름, `Held`)·직무 검색 키워드) → 관심 분야 확인 LLM(새 대화, 관심 분야마다 관심/경험 `InterestCheck`, 경험이면 projects로 옮김) → 코드가 원문(관심 분야·기업·보유 항목)이 학생 입력에 실제로 있는지 대조, 폼 칸 보유 항목을 합치고, 희망 직무·기업명·보유 항목과 겹치는 관심 분야를 빼서 `TargetJob`. 2단계에는 기업 공식명을 넘긴다(그룹을 말하면 name은 그룹명, keywords에 직무 관련 계열사 공식명). 표준 직무 목록은 `catalog` 인자로 받고, 안 주면 `engine/tools/job_catalog.py`의 `load_job_catalog()`가 DB(`src/db/database.py`, docker-compose의 PostgreSQL+pgvector)에서 읽는다. `JOB_QUERY`는 `job_role`의 활성 직무 `name`. DB가 꺼져 있거나 테이블이 비어 있으면 None → 목록 없이 진행.
- **프롬프트 작성 요령(9B 모델)**: 시스템 프롬프트 규칙을 놓치는 일이 잦다. 자주 틀리는 규칙은 사용자 메시지 끝(출력 직전)에 한 번 더 적는다. 추출처럼 짧은 판단은 긴 대화를 이어 가지 말고 새 대화로 필요한 입력만 준다. 사고 기능(think)은 정확도가 오르지만 수십 초가 걸려 기본은 끈다.
- `engine/pipeline/stage2_evidence.py` — (2) 근거 추출. 입력은 1단계 `TargetJob`(`make_target`이 텍스트로 만듦, 학생 학년 포함). 학생에게 줄 정보를 **가이드 슬롯**(`SLOTS`)으로 정해 두고, 슬롯마다 **LLM이 도구를 직접 호출하는 에이전트**(`SlotAgent`, [74])를 돌린다:
  - 슬롯: 채용 요건 4(고유 근거 문서 수) · 자격증 4(어학 성적 TOEIC·OPIc·TOPIK 포함) · 활동·공모전·교과목 8(`ACTIVITY`) · 추천 포트폴리오 3. 채용 요건 외에는 `ITEM_SLOTS` — 서로 다른 항목 수(`item_keys`: 자격증은 item을 쉼표·슬래시로 나눔, 교과목은 과목 코드). 활동(교내프로그램·대외활동)은 문서당 `ACTIVITY_PER_DOC` 2개까지, 교과목은 `COURSE_FILL_MAX` 3칸까지만 센다(`fill_counts`). 교과목 슬롯은 따로 없다(학생이 보고 고르는 것이고 해마다 바뀜).
  - 순서 `SLOT_PRIORITY`: 채용 요건 → 자격증 → 활동·공모전·교과목 → 포트폴리오. 이미 찬 슬롯은 건너뜀. 시간 상한 `MAX_SECONDS` 205를 채울 수에 비례해 슬롯에 나누고 남은 시간은 뒤로 넘어감. **필수 슬롯**(`REQUIRED_SLOTS`: 채용 요건·자격증)이 끝까지 0건이면 그 슬롯 대화를 한 번 더(`REQUIRED_EXTRA_SECONDS`).
  - 슬롯 대화 하나(`run_slot`, 새 대화, Ollama tool calling): 에이전트 LLM이 도구를 고른다 — `search_rag`(슬롯별 `SLOT_DOC_KINDS`만, 활동 슬롯은 `RAG_K_SCHOOL` 6건) / `search_requirements`(네이버·카카오) / `read_documents`(D번호). 검색 결과는 이 대화에 처음 나온 문서의 [D번호]·제목·요약 앞 80자 목록(`AGENT_LIST_MAX` 20건)만, 읽기 결과는 문서별 통과 항목과 슬롯 현황만 대화에 돌아온다([9] 본문을 대화에 쌓지 않음). 어떤 도구를 몇 번 쓸지·언제 끝낼지는 LLM이 정하고, 슬롯이 부족한 채 끝내려 하면 코드가 되묻는다(`AGENT_NUDGE`, `AGENT_NUDGES` 2번, 두 번째는 카드가 늘었을 때만). 루프와 검색 기록은 `engine/pipeline/agent_loop.py`의 `run_tool_loop`·`SearchGuard`를 정보 질문과 같이 쓴다([78]). 코드 쪽 상한: 같은 도구·같은 검색어 차단, 새 문서 없는 검색 2번 이어지면 그 도구를 대화에서 뺌, `AGENT_QUERY_MAX_WORDS` 넘는 검색어 거부, 한 턴 도구 3번, [81] 읽지 않고 검색 `SEARCH_BEFORE_READ` 3번이면 안 읽은 문서가 있는 동안 검색을 막고, 검색이 막히거나 새 문서가 없으면 안 읽은 문서 목록을 다시 보여 줌, `read_documents` 한 번에 읽는 수(기본 `SELECT_PER_SLOT` 2, 활동 슬롯은 웹 `ACTIVITY_WEB_SELECT` 2건·RAG 6건), `AGENT_MAX_TURNS` 12, 새 실행이 없는 턴 `AGENT_IDLE_TURNS` 2.
  - `read_documents` 안: 본문 수집 → 읽기 LLM(새 대화, 본문 전체를 `READ_BUDGET_CHARS` 25,000자 단위로 문서째 묶음, `num_predict` 8192, 잘리면 `_salvage_cards`, 카드 칸은 quote → item → category, 문서당 `MAX_CARDS_PER_DOC` 8장) → 코드(`verify`, `_accept`).
  - `verify`: quote를 공백 무시하고 원문과 대조, 적힌 문서에 없으면 같이 읽은 문서 중 한 곳에만 있을 때 그 문서로 고침, 교과목은 같은 과목 코드·과목명이 있는 원문 행으로 인용을 바로잡음(`_course_row`), 전형 절차(`SELECTION_STEP`)는 기업정보로. 교과목의 개설 학년·교양/전공은 LLM이 아니라 코드(`course_info`)가 원문 표 머리글의 학년 칸·표 제목에서 읽는다.
  - 카드 걸러내기(`SlotAgent._accept`): 교과목은 건양대 문서(`is_school_doc`)이면서 RAG 문서이거나 과목 코드가 있을 때만, 학생 학년보다 낮은 과목은 버림(`past_grade`). 건양대 문서가 아닌 교내프로그램은 대외활동으로, 건양대 문서의 대외활동 중 item·quote에 "건양대"가 있으면 교내프로그램으로. 활동 카드는 item에 `ACTIVITY_WORD`(대회·공모·해커톤·캠프·프로그램·인턴·특강 …)가 없으면 버림.
  - 끝에 `apply_caps`가 추천 상한으로 줄임: 활동·공모전·교과목 8은 `ACTIVITY_RANK` 순(**[92] 교내프로그램 → 대외활동 → 교과목**, 교과목 안에서는 교양 먼저), 자격증 4는 채용 공고(채용 요건 카드가 나온 문서)에 많이 나온 순, 포트폴리오 3은 여러 문서에 나온 순. 활동 슬롯 문서 목록도 `ACTIVITY_KIND_RANK`·`_activity_tier`로 건양대 활동 → 건양대 교육과정 → 외부 순(같은 단계는 RAG 먼저). [92] 활동 슬롯은 `search_rag`를 `SCHOOL_FIRST_SEARCHES`(2)번 하고 한 번 읽기 전에는 웹·잡알리오 검색을 열지 않음(`_school_first`, RAG가 꺼졌거나 새 문서가 없으면 바로 엶) — 외부는 못 채운 칸만. 다른 슬롯 목록은 RAG(`(DB 자료)`) → 웹 순.
  - DB 오류가 나면 `RagState`가 그 실행에서 RAG를 끄고 `search_rag`를 도구 목록에서 뺀다(`ExtractionResult.rag_error`). 한 대화에서 모든 슬롯을 시키면 9B 모델이 일부 슬롯만 하거나 이전 계획을 되풀이해서 슬롯마다 대화를 나눴다. 검색어는 "대상(건양대·기업 공식명·직무·상위 분야) + 찾는 영역" 2~5단어. 도구 호출 기록은 `RoundLog.tool_calls`·`stop`.
- `engine/pipeline/stage3_aggregate.py` — (3) 근거 집계. 입력은 2단계 `ExtractionResult`. 코드((doc_id, quote) 중복 제거, 근거 번호 C1…) → 역할 LLM(카드가 나온 문서마다 채용공고·우대사항정리·자격증정보·프로젝트추천 등 `DocRole`, 제목·요약·카드 item으로 판단) → 이름 뽑기 LLM(item마다 짧은 Skill 이름 목록 — 여러 개면 나눠서, [82] 교내프로그램·대외활동 카드는 나누지 않고 item 하나, 카드 하나가 표 여러 줄에 들어감. 카드의 `skills`·`skill_ids`·`skill_names`는 같은 순서로 짝) → 코드(표기 병합, bge-m3 임베딩 후보 묶음) → 합치기 LLM(후보만) → 사전 매칭(DB Skill 사전 `skill`·`skill_alias`를 `engine/tools/skill_dict.py`가 읽음, 이름·별칭 표기 대조 → 나머지는 임베딩 후보(`MATCH_MIN_SIM` 이상) 중 사전 매칭 LLM이 고르거나 "없음" → 확인 LLM이 Gap 기준 예/아니오, 자격증·교과목·교내프로그램·대외활동 카드에서만 나온 이름은 정확히 같을 때만, 새 Skill은 만들지 않음) → 코드(skill_id(없으면 이름)별 가이드 슬롯·고유 문서 수·분류별·역할별·기업·근거 번호 `SkillRow`, 슬롯 순 정렬). 기업정보 카드는 집계에서 빼고, Gap(보유/미보유)은 집계 표의 `skill_id`로 인호 쪽 코드가 판단한다. DB를 못 읽으면 skill_id는 모두 null. `render_table`이 표 텍스트.
- `engine/tools/gap.py` — Gap(집계 표 줄마다 보유/미보유/수준부족/매핑되지 않음). `student_id`가 있으면 인호 `src/gap_service.calculate_skill_gaps`(돌아오지 않은 매핑 줄 = 보유), 없거나 실패하면 1단계 보유 기술·자격증을 3단계 `match_skills`로 사전에 붙여 비교(자격증은 이름이 같을 때만). DB 결과에도 폼 보유 항목을 보유로 올린다.
- `engine/pipeline/stage4_rank.py` — (4) 우선순위 판단. 슬롯마다 새 대화로 우선순위 LLM(보유한 줄은 코드가 빼고, 후보 R번호·보유 여부·근거 수·필수/우대·역할·기업·근거 항목 + 오늘 날짜·학기 → `RankDraftList` R번호·이유·할 일). 근거 번호(C번호)는 코드가 집계 표에서 붙인다. 판단 오류면 그 슬롯만 `RANK_RETRIES` 1번 다시, 그래도 0개면 빈도 순(`by_code`). 상한 `RANK_CAPS`(채용 요건 5, 나머지는 `ITEM_CAPS`).
- `engine/pipeline/stage5_verify.py` — (5) 검증(코드). `verify_slot`(판단 오류: 없는 R번호·빈 칸·근거와 다른 숫자·이유에 그 후보 근거에 없는 기업·지난 학기) → `check_ranks`(인용 본문 재대조, 교과목 학년, 활동 대상·마감일 — 활동 카드이거나 채용 요건·자격증 외 슬롯에서 이름이 행사·모집(`is_event`)인 카드, 인용·제목 → 본문 순으로 접수·마감 날짜, 초·중·고생 대상은 뺌. **마감된 회차는 추천에서 빼 `RankingResult.past`(지난 회차 참고)로**, [65]). 본문은 `ExtractionResult.texts`/`AggregationResult.texts`(카드가 나온 문서만, JSON 출력 제외)로 받는다. "근거 부족 → (2)" 재추출은 없고 `lacking`으로 알림.
- `engine/pipeline/answer.py` — 결과 생성. 항목(순위·이름·할 일·이유·마감·학년·근거 인용 3개·출처 링크·출처 구분, `sections`)은 코드가 만들고, 답변 LLM이 그 목록으로 학생 질문에 상담하듯 답하는 **대화형 글**(`answer`, JSON 아닌 글, [C번호])을 쓴다([64]). 형식([67]): 대화로 시작 → 슬롯마다 `### 소제목` + `- **추천 이름**: 할 일. 이유. (일정)` → 대화로 마무리(사용자가 두 형식 다 좋다고 함). 일정은 5단계 `upcoming_dates`가 인용·제목·본문(이름 근처 일정 줄)에서 찾은 오늘 이후 날짜. `check_answer`가 없는 [C번호] 지우기·R번호·지난 학기/달·학기-방학 불일치·목록에 없는 기업·추천 절반 넘게 빠뜨림·슬롯 통째로 빠뜨림·지난 회차를 할 일로 권함·반말 끝맺음("~해 보라")·자리표시자(○○)·일정에 없는 날짜를 보고 한 번 다시. 지난 회차는 따로 주고 "다음 모집 확인"으로만 쓰게 함, 두 번 다 걸리면 총평 LLM + 코드 글(`answer_by_code`). 글에 나온 근거의 출처는 `sources`. 출력 `GuideResult`(`engine/schemas/rank.py`)가 프론트 JSON, `render_chat`(기본)·`render_markdown`(`--cards`)은 확인용.
- `engine/rag/` — RAG. **업로드(실시간)와 수집 데이터 적재(일괄)가 같은 추출기를 쓴다.**
  - `extract.py`: 파일 → 본문 전체(`ExtractedDoc`). PDF(텍스트 층, 30자 미만 쪽은 OCR, 쪽마다 `[페이지 N]`)·이미지(OCR, HEIC·GIF 포함)·DOCX(글상자 포함, 표는 행을 ` | `로)·DOC(Word 97 조각 표)·HWP(OLE BodyText 문단, 배포용·암호는 `UnsupportedFile`)·HWPX(zip XML)·TXT(UTF-8→CP949). **문서 속 그림**(DOCX·HWP·HWPX·PDF 글자 쪽의 텍스트 층 없는 큰 그림)도 OCR해 그 자리에 `[그림 N]`으로 넣음(짧은 변 300px 미만·중복은 건너뜀, 문서당 `OCR_MAX_EMBEDDED`(40)개까지).
  - `ocr.py`: PaddleOCR(PP-OCRv5 mobile 검출 + korean 인식, CPU, 약 2초/쪽). 괘선 표는 `table.py`가 칸 단위로, 나머지는 상자를 줄로 묶고 넓은 간격은 ` | `. 자주 틀리는 단어는 `OCR_FIXES`. paddlepaddle은 3.2.2 고정(3.3은 CPU 버그).
  - `table.py`: 이미지에서 괘선(밝기 200 미만의 가는 선)으로 칸을 찾아 OCR 상자를 넣고 행마다 ` | `로 이음. 빈 칸 자리를 남기고, 글자 없는 칸은 픽셀로 ●/○ 판단, 세로 병합 칸의 짧은 글은 행마다 되풀이. 표 안의 표는 예전 줄 묶기로. **VLM(qwen3.5 vision)으로 표를 다시 쓰는 방식은 그럴듯하게 틀려서(problem.txt [47]) 쓰지 않는다.**
  - `chunk.py`: 줄 단위 800자·겹침 150자, PDF는 쪽을 넘지 않음. **조각은 문서 찾기용** — LLM에게는 `get_document`로 본문 전체를 준다.
  - `store.py`: 인호 스키마의 `source_document`(본문 전체·`raw_metadata`) + `document_chunk`(조각·bge-m3 `vector(1024)`·`metadata`). `ingest`(같은 source_name+record_key면 덮어씀, 해시 같으면 건너뜀) / `search`(코사인, 문서별로 묶음, `filters`·`doc_kinds`는 조각 `metadata` 기준, `min_score`) / `get_document`.
  - `index.py`: **인호 DB → 벡터 검색**. 인호 로더가 넣은 `source_document` 중 조각이 없거나 본문(`content_hash`)·종류가 바뀐 행에 조각·임베딩을 붙임(`python -m engine.rag.index [--dry-run]`). 인호 행은 고치지 않고 `doc_kind`·`school`·`department`는 조각 `metadata`에 둠(인호 upsert가 `raw_metadata`를 덮어써서). `doc_kind_of`: 자원 COURSE·CERTIFICATE·JOB·PROGRAM(건양대면 비교과, 아니면 지역프로그램) 우선, 없으면 SYLLABUS·CERTIFICATE_INFO·NOTICE. 검색 결과 요약(SEARCH_RESULT)·대학학과 목록(KCUE)은 안 넣음. 인호 로더를 돌린 뒤 실행.
  - `ingest.py`: 일괄 적재 CLI. `python -m engine.rag.ingest <파일|폴더> --doc-kind 교육과정 --department ... --year 2022 [--dry-run]` 또는 `--manifest`. 추출 본문은 `data/processed/<이름>.txt`.
- `backend/app.py` — FastAPI 서버 하나(`uvicorn backend.app:app`, worker 1개 — 작업 기록이 메모리에 있음). `POST /analyze`(202, job_id) → `GET /analyze/{job_id}`(대기/실행 중/완료/실패·단계 1~5·대기 순번·로그·결과 `guide`, `?detail=true`면 `PipelineResult` 전체). Ollama가 하나라 분석은 한 번에 하나씩. `POST /upload`(최대 5개·20MB, 본문을 바로 돌려줌. **학생 개인 자료라 공용 벡터 DB에 넣지 않는다** — 프론트가 `student.documents`로 다시 보내면 1단계만 읽음), `POST /rag/search`, `GET /rag/documents/{id}`, `GET /health`(DB 이름 포함). 끝의 `_include_db_api`가 `main.py`(인호) 라우트를 고치지 않고 붙인다(겹치는 경로는 이쪽 것).
- **근거 풀 vs 근거 카드**: 근거 풀은 코드가 검색 결과를 전부 담는 원문 목록, 근거 카드는 LLM이 그중 근거만 골라 만든 것. "근거를 못 찾으면 넣지 않는다"는 카드 기준이다.
- `engine/tools/jobalio.py` — [84] 잡알리오(공공기관·공공병원 채용 공고) 도구 `search_public_jobs`. `opendata.alio.go.kr/new/v1/recruit/list.do`에 **POST 폼**(serviceKey = `.env`의 `JOBALIO_API_KEY`), 진행 중 공고 전체를 30분 캐시하고 검색어 단어 점수로 고름(서버에 제목 검색 없음). 본문은 API 칸을 이은 글(크롤링 아님), 링크는 `job.alio.go.kr/recruitview.do?idx=`. 키가 있을 때만 2단계·정보 질문 도구 목록에 들어감. 건양대는 병원·의과·간호학과가 있어 병원 취업 근거로 씀.
- `engine/tools/rag_search.py` — RAG 검색 결과(`store.search` → `get_document` 본문 전체)를 근거 풀 형식(`RequirementSearchResult`·`EvidenceDoc`)으로. doc_id는 `rag://source_document/<id>` 해시(16자), `MIN_SCORE` 0.55.
- `engine/tools/text.py` — 태그/엔티티 정리, URL 정규화(티스토리 `/m/` 제거, 추적 파라미터 제거), 날짜를 `YYYYMMDD` 정수로.

산출물 이름(README 기준): 근거 풀(원문 전체) → 상세 근거 카드(인용 문장) → 근거 집계 표(숫자·근거 번호) → 근거 순위 카드(순위·행동·근거 번호).

## 현재 상태 (2026-11-04)

- **결정됨([92], 2026-11-10)**: 2단계 활동·공모전·교과목은 검색·읽기는 건양대 우선(RAG 연결됨), 외부 검색은 못 채운 칸만. 추천 순서는 교내프로그램 → 대외활동 → 교과목. [61] 외부 우선 실험은 끝남. 스냅샷 DB로 학생 실행 시간·슬롯 채움은 아직 안 잼. 남은 것: 같은 대회 다른 이름("중앙 해커톤"/"멋쟁이사자처럼 13기 중앙 해커톤") 묶기.
- **도구 호출([74][75])**: 2단계와 0단계 정보 질문은 LLM이 도구를 직접 호출한다. 2단계 시험 표본은 학생 3명·1회뿐이라 실제 데이터로 다시 잴 것. 정보 질문은 전후 평가에서 참 답변이 한국어 19 → 17/20, TruthfulQA 14 → 11/30으로 줄고 입력 토큰이 늘었다(표본이 작아 단정은 못 함). [78]에서 공통 루프·답변 검토 등으로 고쳐 실패 문항은 맞게 답하지만 정식 수치는 아직 없다. DB를 채운 뒤 새 문항을 함께 잴 것. 2단계는 활동 칸이 늘었지만 시간·토큰도 늘었다([78]).
- **남은 2단계 문제**(problem.txt [58]~[61]): 포트폴리오에 교육 과정(아카데미·부트캠프)이 섞임(사용자: 포트폴리오로 볼 수도 있어 애매 — 보류), 이름이 불분명한 활동("특강"), 백엔드 학생에게 의료 AI 과목이 붙음, 읽기 출력 잘림([36])이 한 바퀴를 150초로 늘리는 경우.
- **합의된 것**: 3단계 Skill 사전에 없는 항목은 지우지 않고 `SkillRow.mapping` = "매핑되지 않음", 인호 `src/gap_service.py`도 `skill_id` null 줄을 `gap: "매핑되지 않음"`으로 돌려줌([49]). 인호 파일을 고쳤으니 인호에게 공유할 것.
- **인호와 정할 것**: `engine/rag/index.py`의 `doc_kind_of` 대응표(인호 resource_type·document_type → doc_kind, [53]), index 실행 시점(로더 끝에서 부를지), Skill 사전에 어학 Skill·별칭 추가(자격증은 3단계에서 정확히 같을 때만 매핑).
- **채팅 전환([71])**: 엔진·API는 `history`로 이어짐. 위기 판단·문구는 상담 전문가 검토 전이다.
- **프론트 연동(2026-11-10)**: `connect_with_front/index.html`이 받은 UI를 `/cq-api`(`/analyze` 진행 확인·`/upload`·`/health`)에 붙인 것. 대화는 서버 세션 없이 화면 JS의 `chatHistory`에만 있고 매번 `history`로 보냄(새로고침하면 사라짐). 배포 위치는 사용자가 정함. 확인용 서버는 `connect_with_front/serve.py`(저장소 루트에서 `uvicorn --app-dir connect_with_front serve:root --port 8010`, 백엔드를 `/cq-api`에 붙임). 학교·학점·현재 고민 칸은 `StudentInput`에 없어 안 보냄. **다음에 정할 것: 대화를 DB에 저장할지**(지금은 브라우저 메모리만). 서버 DB(`careerquest`, 통합 DB 모두)에 `document_chunk`가 0개라 RAG가 비어 있음 — 한 번 실행에 약 6분(2단계가 시간 상한까지 돎, T4 출력 약 33 토큰/초).
- **API 서버**: `backend/app.py` 하나로 합침([50] 일부 해결). 남은 것: 프론트 입력을 DB `student`에 저장하는 API(지금은 student_id를 알 때만 인호 Gap, 없으면 1단계 보유 항목으로 판단 — 프론트 입력·저장 형식은 인호·재열이 정함, 용재 쪽은 정해지면 `student_id`만 받아 쓰면 됨), 업로드 문서를 공용 RAG에 넣을지(지금은 1단계만 읽음).
- **4·5단계 남은 것**([62]·[63]): 실제 입력 시험에서 "기업 주최 공모전" 같은 학생 조건이 활동 추천에 반영 안 됨, 희망 직무 칸이 비면 직무명이 일반적("AI Engineer"). "근거 부족 → (2)" 재추출 없음, 모델 순위 vs 빈도 순위(`freq_rank`) 비교 실험 안 함, 후보 질은 2·3단계 그대로(채용 요건에 엉뚱한 매핑·기사). 시험 DB의 Skill 사전(`python -m src.db.seed_master`)이 작아 집계 줄의 70% 넘게 "매핑되지 않음".
- **시험 데이터**: 실험은 conda 별도 환경 `cq-pgtest`(PostgreSQL 16 + pgvector)의 시험 DB(포트 55432, `careerquest_test`, 사용자 `cqtest`)로 함. 내용은 의료인공지능학과 교육과정 PDF 1건 + 인호 로더 형식 시험 행 8건(비교과·지역 프로그램 각 1건)뿐이라 활동 슬롯 수치는 실제 데이터로 다시 재야 함. 켜는 법: `~/miniconda3/envs/cq-pgtest/bin/initdb -D <dir> -U cqtest --auth=trust` → `pg_ctl -D <dir> -o "-p 55432 -k ''" start` → `createdb -h localhost -p 55432 -U cqtest careerquest_test` → `POSTGRES_DB=careerquest_test POSTGRES_USER=cqtest POSTGRES_PASSWORD=cqtest POSTGRES_PORT=55432 alembic upgrade head` (환경 변수가 .env보다 우선). Skill 사전·직무 목록은 `python -m src.db.seed_master`, 교육과정은 `python -m engine.rag.ingest "data/raw/…pdf" --doc-kind 교육과정 --department 의료인공지능학과 --year 2022`. 측정 스크립트(학생 3명, 시간 상한 없이 6바퀴)는 저장소에 없음.

## 지켜야 할 규칙

- **LLM은 본문 전체를 읽는다.** 코드로 문단을 골라내거나 본문을 잘라 줄이지 말 것. 근거 풀 항목에 본문을 다시 넣지 말 것.
- **데이터 우선순위: RAG에 있는 건양대학교 자료(교육과정·로드맵·수업계획서·비교과)가 최우선이다.** 검색 API·크롤링 결과는 RAG에 없거나 부족한 근거를 보충하는 용도다. 하지만 검색 API 및 크롤링 결과도 적극 활용하여 최대한 많고 정확한 정보 제공을 목표로 한다.
- 수집 방법 순서: API → 크롤링(robots.txt·이용약관 확인한 사이트만) → 수작업.
- 채용 플랫폼(잡코리아·사람인·링커리어·캐치·원티드 등) 상세 페이지는 크롤링하지 않고 링크만 제공한다. 합격 자소서는 수집하지 않는다.
- 날짜(작성일·마감일)는 정수 `YYYYMMDD`로 저장한다.
- 코드 수정·테스트 중 발견한 문제점이나 고쳐야 할 부분은 루트의 `problem.txt`에 번호를 붙여 추가한다. 고친 항목은 제목 옆에 `(해결)`을 붙이고 "해결" 블록(해결: 날짜·한 줄 요약 / 방법: 어디를 어떻게 바꿨고 왜 / 확인: 테스트 결과·남은 한계)으로 처리 방법을 요약한다. 작업 시작 전에 미해결 항목을 확인한다.
- 커밋은 사용자가 직접 한다. 파일 수정까지만 하고 commit/push 하지 않는다.
