
from __future__ import annotations

from pydantic import ValidationError
from sqlalchemy import text

from src.db.database import engine
from src.gap_service import (
    GapInputError,
    GapRequest,
    calculate_gap_request,
)


def main() -> None:

    # 1. 실제 DB에서 테스트 학생과 Skill 선택
    with engine.connect() as conn:

        db_name = conn.execute(
            text("SELECT current_database()")
        ).scalar_one()

        # 가능하면 보유 Skill이 가장 많은 학생 사용
        student_id = conn.execute(text("""
            SELECT s.student_id
            FROM student s
            LEFT JOIN student_skill ss
                ON ss.student_id = s.student_id
            GROUP BY s.student_id
            ORDER BY COUNT(ss.skill_id) DESC,
                     s.student_id
            LIMIT 1
        """)).scalar()

        if student_id is None:
            print(
                "[BLOCK] 학생 데이터가 없습니다. "
                "테스트 학생 준비가 필요합니다."
            )
            return

        # 학생이 이미 보유한 활성 Skill
        owned = conn.execute(text("""
            SELECT k.skill_id, k.name
            FROM student_skill ss
            JOIN skill k
                ON k.skill_id = ss.skill_id
            WHERE ss.student_id = :student_id
              AND k.is_active = TRUE
            ORDER BY k.skill_id
            LIMIT 1
        """), {
            "student_id": student_id
        }).first()

        # 학생이 보유하지 않은 활성 Skill
        missing = conn.execute(text("""
            SELECT k.skill_id, k.name
            FROM skill k
            WHERE k.is_active = TRUE
              AND NOT EXISTS (
                  SELECT 1
                  FROM student_skill ss
                  WHERE ss.student_id = :student_id
                    AND ss.skill_id = k.skill_id
              )
            ORDER BY k.skill_id
            LIMIT 1
        """), {
            "student_id": student_id
        }).first()

        # 존재하지 않는 Skill ID
        unknown_id = conn.execute(text("""
            SELECT COALESCE(MAX(skill_id), 0) + 100000
            FROM skill
        """)).scalar_one()

    print(
        f"[DB] {db_name} / "
        f"student_id={student_id}"
    )

    # 2. 모델팀 SkillRow를 흉내 낸 입력
    inputs = []

    if owned:
        inputs.append({
            "skill_id": owned.skill_id,
            "skill": owned.name,
            "doc_count": 2,
        })

    if missing:
        inputs.append({
            "skill_id": missing.skill_id,
            "skill": missing.name,
            "doc_count": 3,
        })

    inputs.append({
        "skill_id": None,
        "skill": "사전 외 테스트 역량",
        "doc_count": 1,
    })

    # 3. 실제 GAP 서비스 실행
    request = GapRequest(
        student_id=student_id,
        skills=inputs,
    )

    results = calculate_gap_request(request)

    print(
        "\n[모델 SkillRow → Backend GAP]"
    )

    for row in results:
        print(
            f"  {row['skill_id']} | "
            f"{row['skill']} | "
            f"{row['gap']}"
        )

    by_id = {
        row["skill_id"]: row
        for row in results
    }

    # 4. 결과 검증
    assert (
        by_id[None]["gap"]
        == "매핑되지 않음"
    ), "null Skill ID 처리 실패"

    if missing:
        assert (
            by_id[missing.skill_id]["gap"]
            == "미보유"
        ), "미보유 판단 실패"

    if owned:
        assert (
            owned.skill_id not in by_id
        ), "보유 Skill이 GAP에 포함됨"

    print(
        "[PASS] 보유·미보유·미매핑 판단"
    )

    # 5. 존재하지 않는 ID 거부 검증
    try:
        calculate_gap_request(
            GapRequest(
                student_id=student_id,
                skills=[
                    {"skill_id": unknown_id}
                ],
            )
        )

    except GapInputError:
        print(
            "[PASS] 존재하지 않는 skill_id 거부"
        )

    else:
        raise AssertionError(
            "존재하지 않는 ID가 허용됐습니다."
        )

    # 6. 중복 ID 거부 검증
    if missing:

        try:
            GapRequest(
                student_id=student_id,
                skills=[
                    {"skill_id": missing.skill_id},
                    {"skill_id": missing.skill_id},
                ],
            )

        except ValidationError:
            print(
                "[PASS] 중복 skill_id 거부"
            )

        else:
            raise AssertionError(
                "중복 ID가 허용됐습니다."
            )

    print(
        "\n[PASS] "
        "SkillRow → StudentSkill → GAP "
        "읽기 전용 계약 테스트"
    )

    if not owned:
        print(
            "[참고] StudentSkill이 비어 있으므로 "
            "보유 판정은 별도 검증 필요"
        )

    if not missing:
        print(
            "[참고] 미보유 Skill이 없어 "
            "미보유 판정은 별도 검증 필요"
        )


if __name__ == "__main__":
    main()
