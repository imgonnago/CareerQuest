# CareerQuest 백엔드 API 계약 검증 v1.3

## 목적

기존 정상 연동을 변경하지 않고 `main.py`의 API 스키마와 오류 처리를 회귀 검증한다. 신규 Skill 제안 테이블/CLI는 인증·권한 설계가 끝날 때까지 HTTP에 노출하지 않는다.

## 검사 경계

- `GET /health`, `/api/skills`, `/api/students`, `/api/students/{student_id}/skills`, `/api/model/contract`, `/api/gap/contract`
- `POST /api/gap/calculate`: `GapRequest` / `list[GapResultItem]`, HTTP 200/422/500
- `skill_id: null`이면 Skill 이름 필요, GAP `매핑되지 않음`
- 보유 Skill은 GAP 결과에 나타나지 않는다. 모델팀이 응답에 없는 Skill을 `보유`로 처리한다.
- API 단위 테스트는 DB를 **모킹**하여 HTTP 핸들러·Pydantic 계약만 확인한다. 실제 GAP SQL/조회는 기존 `src.db.check_gap_contract`로 검증한다.

## 주의할 설계 제약

1. `/api/students` 등 학생 정보 조회는 실제 인증 계층이 확인되기 전까지 인터넷에 공개하지 않는다.
2. `/api/students/{student_id}/skills`는 원시 `student_skill` 조회이며 GAP 서비스의 신뢰 근거 필터와 다른 의미를 갖는다. 프론트에서 이 조회 결과만으로 보유 판정을 하지 않는다.
3. 제안 테이블 CLI의 `actor`는 인증 주체가 아니며 접근 권한 검증이 아니다.
4. `/api/model/experience/{experience_id}/input`은 현재 광범위한 예외를 404로 반환하는 경로가 있다. 향후 오류 분류/인증 설계에서 수정해야 한다.
5. 기존 `role_skill` 저장 결과의 매핑 출처가 검증됐다는 뜻은 아니다.

## 사용

```powershell
python -m src.db.check_backend_api_contract
python -m unittest discover -s tests -p "test_backend_api_contract.py" -v
```

DB 오류가 500 응답에 그대로 노출되는 것을 차단하는 작은 패치를 별도 미리보기로 제공한다:

```powershell
python backend_api_contract_v1_3/patch_api_error_leaks.py
python backend_api_contract_v1_3/patch_api_error_leaks.py --apply
python -m unittest discover -s tests -p "test_backend_api_contract.py" -v
python -m src.db.check_gap_contract
```

`--apply`는 `main.py` 세 곳의 DB 오류 응답만 일반 메시지로 바꾸고 백업한다. 실제 학생 데이터나 DB는 바꾸지 않는다. 로컬 `main.py`가 다르면 자동 중단한다.
