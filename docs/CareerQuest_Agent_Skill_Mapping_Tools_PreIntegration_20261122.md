# CareerQuest Agent Skill Mapping — Tool Adapter 준비 (2026-11-22)

## 1. 작업 목적 및 현재 상태

DB에 이미 생성된 `agent_skill_mapping_*` 4개 테이블을 활용할 수 있도록 **모델 비종속 도구 인터페이스**를 먼저 준비합니다. 모델팀의 LLM 에이전트와 아직 연결하지 않았습니다.

- **구현됨:** 기존 `skill` / `skill_alias` 조회, 기존 `ResourceSkill` 제안 서비스의 읽기 전용 검증 호출, 제한된 재시도 흐름, 사람 검토 분기, 모의 LLM 응답으로 실행하는 오프라인 테스트, 선택적 모의 실행 이력 DB 기록.
- **미구현:** 실제 LLM 도구 호출, 모델이 피드백을 읽고 스스로 재시도 후보 생성, 실제 LLM 결과의 대기열 자동 등록, 검토자 인증/권한, 자동 승인·적용.
- **안전 경계:** `resource_skill`, `student_skill`, `role_skill`, `main.py`, `engine/*` 변경 없음. 기존 `resource_skill_proposal_service`가 유일한 제안 검증/영구 적용 경로입니다.

## 2. 새로운 파일

| 파일 | 역할 |
|---|---|
| `src/mapping/agent_skill_mapping_tools.py` | 에이전트가 나중에 호출할 DB 읽기/검증/대기열 제출 도구 정의 |
| `src/mapping/agent_skill_mapping_flow.py` | Skill 후보 공급자 콜백을 받는 제한 재시도·검토 분기 제어 흐름 |
| `src/db/agent_skill_mapping_demo.py` | 완전 모의 시연, 선택적 실제 DB 읽기 전용 조회/검증, 선택적 MOCK 기록 |
| `src/db/agent_skill_mapping_shadow_record.py` | 별도 확인을 받을 경우 **신규 에이전트 이력 테이블에만** 기록 |
| `tests/test_agent_skill_mapping_flow.py` | DB/LLM 없는 안전한 오프라인 테스트 |

기존 미추적 `src/mapping/resource_skill_llm_*.py` 파일은 덮어쓰거나 삭제하지 않습니다.

## 3. 도구 계약

1. `lookup_skill_candidates(query, limit)` — 활성 표준 Skill/별칭에서 문자열 후보 조회. 문자열 유사도는 의미적 정확도가 아닙니다. 모델은 새로운 ID를 만들 수 없습니다.
2. `validate_skill_mapping(candidate)` — 기존 `Candidate.from_json_dict()` + `preview()`를 호출하는 **읽기 전용 검증**. 실패 시 `validation_code`와 `route`를 반환. `PASSED`라도 **`HUMAN_REVIEW`** 로 이동합니다.
3. `submit_skill_proposal(candidate)` — 기본 **DISABLED**. 신뢰된 실행 호스트가 `allow_submit=True` + `submit_actor`를 설정한 경우에만 기존 `submit()`을 호출해 **PENDING 제안만** 저장. AI가 요청 인자로 이를 활성화할 수 없습니다. 해당 모델팀 연결은 아직 구현하지 않았습니다.

`approve` / `apply` 도구는 전혀 제공하지 않습니다. 자동 승인 및 적용은 정책에서 모두 OFF 상태여야 합니다.

## 4. 설치 및 실행 명령 (프로젝트 루트의 `.venv`에서)

ZIP을 CareerQuest 폴더에서 압축 해제하세요. 모든 새 파일은 기존 코드와 이름이 겹치지 않습니다.

```powershell
# 새 어댑터 오프라인 테스트. DB 쓰기 X, LLM 실행 X
python -m unittest discover -s tests -p "test_agent_skill_mapping_flow.py" -v

# 완전 모의 실행 시연. 가상 Skill ID 999999(실패) → 101(성공), DB 액세스 X
python -m src.db.agent_skill_mapping_demo

# 실제 DB Skill 사전 읽기 전용 후보 조회. DB 쓰기 X
python -m src.db.agent_skill_mapping_demo --lookup Python

# 옵션: 실제 Resource+원문+Skill ID가 있는 제안 JSON을 읽기 전용 검증
python -m src.db.agent_skill_mapping_demo --preview-json .\path\to\candidate.json
```

**선택 사항 (발표 직전에 꼭 할 필요 없음):** 모의 이력을 실제 `agent_skill_mapping_run`, `attempt`, `tool_event` 3개 테이블에 기록하고 싶을 경우:

```powershell
python -m src.db.agent_skill_mapping_demo --record-mock
```

이 명령은 로컬 `careerquest`만 허용하며, 확인 문구를 직접 입력해야 합니다. **가상 데이터**가 남으므로 불필요하면 실행하지 마세요. 기존 ResourceSkill이나 `resource_skill_proposal`에는 일절 쓰지 않습니다. 기록된 모의 시도에는 외래키 오류를 피하기 위해 가짜 Skill ID가 아니라 `candidate_skill_id = NULL`을 기록합니다. 모의 로그의 `decision_origin`은 `BACKEND`이며 실제 AI의 자율적인 도구 선택 로그가 아닙니다.

## 5. 모델팀 연결 시 해야 할 일

- 모델팀의 `engine/pipeline/agent_loop.py` 도구 루프에서 `TOOL_DEFINITIONS`를 추가하는 것부터 시작합니다. **기존 검색 에이전트 전체를 다시 만들 필요는 없습니다.**
- Tool dispatcher의 실제 호출 인자를 `SkillMappingAgentTools.dispatch_tool(name, arguments)`로 넘깁니다.
- 검증 결과가 `FAILED/RETRY`면 에이전트가 문서/Skill 후보를 다시 검토하도록 프롬프트를 구성합니다. 재시도 상한 2회(총 3회)를 넘기지 않습니다.
- 성공해도 반드시 `HUMAN_REVIEW`. 대기열 자동 제출은 실제 운영자 인증/권한 확인, 정책 점검, 팀 합의 이후에만 활성화합니다.
- 실제 LLM 테스트는 모델팀 PC/서버에서 수행합니다. 이 파일들은 Ollama 모델을 다운로드하거나 실행하지 않습니다.
- 실제 LLM의 자율적 도구 선택, 오류 피드백에 따른 자기 수정 성공 여부는 **아직 검증되지 않았습니다.** 모의 테스트와 별도 기록으로 구분해서 보고합니다.

## 6. 발표에 사용할 수 있는 정확한 표현

> "Skill DB 조회·검증·승인대기 등록 도구의 백엔드 인터페이스와 재시도·검토 분기 흐름을 구현했으며, 모의 응답으로 LLM 없이 테스트할 수 있습니다. 실제 에이전트 자율 호출과의 연결은 다음 단계입니다. 자동 승인과 DB 영구 반영은 현재 금지되어 있습니다."
