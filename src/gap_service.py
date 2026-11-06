from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from sqlalchemy import (
    bindparam,
    text,
)

from src.db.database import engine
from src.student_skill_service import (
    load_student_skills,
)


GapType = Literal[
    "미보유",
    "수준부족",
    # Skill 사전에 매핑되지 않은 근거 Skill.
    # 지우지 않고 그대로 돌려줘 화면·추천에서
    # "매핑되지 않음"으로 보여준다 (2026-11-03 합의)
    "매핑되지 않음",
]


class GapInputError(
    ValueError
):
    """
    모델팀 -> GAP 계산 입력 계약 오류.
    """


class SkillRowForGap(
    BaseModel
):
    """
    모델팀 SkillRow 중
    GAP 계산에 필요한 필드만 사용합니다.

    doc_count, card_count, by_role 등
    나머지 필드는 그대로 넘어와도 무시합니다.

    skill_id가 null이면 Skill 사전에 매핑되지
    않은 줄입니다.

    이 경우 skill 이름은 반드시 존재해야 하며,
    GAP 결과는 "매핑되지 않음"으로 반환합니다.
    """

    model_config = ConfigDict(
        extra="ignore"
    )

    skill_id: int | None = Field(
        default=None,
        gt=0,
    )

    # 모델팀 SkillRow.skill
    #
    # skill_id가 없는 경우에는
    # 어떤 Skill이 매핑되지 않았는지
    # 구분해야 하므로 필수입니다.
    skill: str | None = None

    # 수준 비교가 필요한 Skill에만 사용
    required_level: str | None = None

    @field_validator(
        "skill"
    )
    @classmethod
    def clean_skill(
        cls,
        value: str | None,
    ) -> str | None:

        if value is None:
            return None

        value = value.strip()

        return (
            value
            or None
        )

    @field_validator(
        "required_level"
    )
    @classmethod
    def clean_required_level(
        cls,
        value: str | None,
    ) -> str | None:

        if value is None:
            return None

        value = value.strip()

        return (
            value
            or None
        )

    @model_validator(
        mode="after"
    )
    def validate_unmapped_skill(
        self,
    ) -> "SkillRowForGap":

        if (
            self.skill_id is None
            and self.skill is None
        ):
            raise ValueError(
                (
                    "skill_id가 null인 "
                    "SkillRow에는 skill 이름이 "
                    "반드시 필요합니다."
                )
            )

        return self


class GapRequest(
    BaseModel
):

    student_id: int = Field(
        gt=0
    )

    skills: list[
        SkillRowForGap
    ] = Field(
        default_factory=list
    )

    # 낮은 수준 -> 높은 수준 순서
    #
    # 예:
    # ["IL", "IM", "IH"]
    #
    # required_level이 존재할 때만 필요
    level_order: list[str] | None = None

    @field_validator(
        "skills"
    )
    @classmethod
    def validate_unique_skill_ids(
        cls,
        value: list[
            SkillRowForGap
        ],
    ) -> list[
        SkillRowForGap
    ]:

        seen: set[int] = set()

        duplicates: set[int] = (
            set()
        )

        for row in value:

            # 매핑되지 않은 줄(skill_id null)은
            # 중복 검사 대상이 아니다
            if row.skill_id is None:
                continue

            if (
                row.skill_id
                in seen
            ):

                duplicates.add(
                    row.skill_id
                )

            seen.add(
                row.skill_id
            )

        if duplicates:

            duplicate_text = (
                ", ".join(
                    str(
                        skill_id
                    )
                    for skill_id
                    in sorted(
                        duplicates
                    )
                )
            )

            raise ValueError(
                (
                    "SkillRow에 중복 "
                    "skill_id가 있습니다: "
                    f"{duplicate_text}"
                )
            )

        return value

    @field_validator(
        "level_order"
    )
    @classmethod
    def clean_level_order(
        cls,
        value: list[str] | None,
    ) -> list[str] | None:

        if value is None:
            return None

        cleaned = [
            item.strip()

            for item in value

            if item.strip()
        ]

        if not cleaned:
            return None

        normalized = [
            normalize_level(
                item
            )

            for item in cleaned
        ]

        if (
            len(
                normalized
            )
            != len(
                set(
                    normalized
                )
            )
        ):

            raise ValueError(
                (
                    "level_order에 "
                    "중복 수준이 있습니다."
                )
            )

        return cleaned

    @model_validator(
        mode="after"
    )
    def validate_level_contract(
        self,
    ) -> "GapRequest":

        # 매핑되지 않은 줄은 수준 비교를 하지 않는다
        needs_level_compare = any(
            row.required_level
            is not None

            and row.skill_id
            is not None

            for row in self.skills
        )

        if (
            needs_level_compare
            and not self.level_order
        ):

            raise ValueError(
                (
                    "required_level을 사용하는 경우 "
                    "level_order도 함께 전달해야 합니다."
                )
            )

        return self


class GapResultItem(
    BaseModel
):

    skill_id: int | None

    # 매핑되지 않은 줄을 구분하는 이름
    skill: str | None = None

    student_level: str | None

    gap: GapType


def normalize_level(
    value: str,
) -> str:

    return (
        value
        .strip()
        .casefold()
    )


def ensure_student_exists(
    student_id: int,
) -> None:

    with engine.connect() as conn:

        exists = (
            conn.execute(
                text(
                    """
                    SELECT 1

                    FROM student

                    WHERE
                        student_id =
                            :student_id

                    LIMIT 1
                    """
                ),
                {
                    "student_id":
                        student_id,
                },
            )
            .scalar()
        )

    if exists is None:

        raise GapInputError(
            (
                "존재하지 않는 "
                "student_id입니다: "
                f"{student_id}"
            )
        )


def validate_skill_ids(
    skill_ids: list[int],
) -> None:

    if not skill_ids:
        return

    sql = (
        text(
            """
            SELECT
                skill_id

            FROM skill

            WHERE
                is_active = TRUE

                AND skill_id
                    IN :skill_ids
            """
        )
        .bindparams(
            bindparam(
                "skill_ids",
                expanding=True,
            )
        )
    )

    with engine.connect() as conn:

        rows = (
            conn.execute(
                sql,
                {
                    "skill_ids":
                        skill_ids,
                },
            )
            .scalars()
            .all()
        )

    valid_ids = {
        int(
            skill_id
        )

        for skill_id in rows
    }

    invalid_ids = sorted(
        set(
            skill_ids
        )
        - valid_ids
    )

    if invalid_ids:

        invalid_text = (
            ", ".join(
                str(
                    skill_id
                )
                for skill_id
                in invalid_ids
            )
        )

        raise GapInputError(
            (
                "Skill DB에 없거나 "
                "비활성인 skill_id가 "
                "전달됐습니다: "
                f"{invalid_text}"
            )
        )


def build_level_rank(
    level_order: list[str] | None,
) -> dict[str, int]:

    if not level_order:
        return {}

    return {
        normalize_level(
            level
        ):
            index

        for (
            index,
            level,
        ) in enumerate(
            level_order
        )
    }


def calculate_skill_gaps(
    student_id: int,

    skill_rows: list[
        dict[
            str,
            Any,
        ]
        | SkillRowForGap
    ],

    *,
    level_order: list[str] | None = None,
) -> list[
    dict[
        str,
        Any,
    ]
]:

    """
    모델팀의 SkillRow와
    학생 StudentSkill을
    skill_id 기준으로 비교합니다.

    이 함수는 추천을 수행하지 않습니다.

    GAP이 있는 Skill만 반환합니다.

    skill_id가 없는(Skill 사전에 매핑되지 않은)
    줄은 지우지 않고 gap="매핑되지 않음"으로
    함께 반환합니다.
    """

    request = (
        GapRequest(
            student_id=(
                student_id
            ),

            skills=[
                (
                    row

                    if isinstance(
                        row,
                        SkillRowForGap,
                    )

                    else
                    SkillRowForGap
                    .model_validate(
                        row
                    )
                )

                for row in skill_rows
            ],

            level_order=(
                level_order
            ),
        )
    )

    ensure_student_exists(
        request.student_id
    )

    skill_ids = [
        row.skill_id

        for row in request.skills

        if row.skill_id is not None
    ]

    validate_skill_ids(
        skill_ids
    )

    student_skills = {
        int(
            row[
                "skill_id"
            ]
        ):
            row

        for row
        in load_student_skills(
            request.student_id
        )
    }

    level_rank = (
        build_level_rank(
            request.level_order
        )
    )

    result: list[
        GapResultItem
    ] = []

    for row in request.skills:

        # ----------------------------------------------------
        # Skill 사전에 매핑되지 않은 근거 Skill
        # 보유 여부를 판단할 수 없으므로 그대로 명시
        # ----------------------------------------------------

        if row.skill_id is None:

            result.append(
                GapResultItem(
                    skill_id=None,

                    skill=row.skill,

                    student_level=None,

                    gap="매핑되지 않음",
                )
            )

            continue

        student_skill = (
            student_skills.get(
                row.skill_id
            )
        )

        # ----------------------------------------------------
        # 학생이 해당 Skill 자체를 가지고 있지 않음
        # ----------------------------------------------------

        if student_skill is None:

            result.append(
                GapResultItem(
                    skill_id=(
                        row.skill_id
                    ),

                    skill=row.skill,

                    student_level=None,

                    gap="미보유",
                )
            )

            continue

        # ----------------------------------------------------
        # required_level이 없으면
        # 보유 여부만 확인
        # ----------------------------------------------------

        if (
            row.required_level
            is None
        ):
            continue

        student_level = (
            student_skill[
                "level"
            ]
        )

        # 요구 수준이 있는데
        # 학생 Skill에 level 자체가 없다면
        # 임의로 부족하다고 판단하지 않음
        if student_level is None:

            raise GapInputError(
                (
                    "수준 비교가 필요한 "
                    "Skill의 student_level이 "
                    "없습니다: "
                    f"skill_id={row.skill_id}"
                )
            )

        student_key = (
            normalize_level(
                str(
                    student_level
                )
            )
        )

        required_key = (
            normalize_level(
                row.required_level
            )
        )

        if (
            required_key
            not in level_rank
        ):

            raise GapInputError(
                (
                    "required_level이 "
                    "level_order에 없습니다: "
                    f"skill_id={row.skill_id}, "
                    f"required_level="
                    f"{row.required_level}"
                )
            )

        if (
            student_key
            not in level_rank
        ):

            raise GapInputError(
                (
                    "student_level이 "
                    "level_order에 없습니다: "
                    f"skill_id={row.skill_id}, "
                    f"student_level="
                    f"{student_level}"
                )
            )

        if (
            level_rank[
                student_key
            ]
            <
            level_rank[
                required_key
            ]
        ):

            result.append(
                GapResultItem(
                    skill_id=(
                        row.skill_id
                    ),

                    skill=row.skill,

                    student_level=(
                        str(
                            student_level
                        )
                    ),

                    gap="수준부족",
                )
            )

    return [
        item.model_dump()

        for item in result
    ]


def calculate_gap_request(
    request: GapRequest,
) -> list[
    dict[
        str,
        Any,
    ]
]:

    return calculate_skill_gaps(
        request.student_id,

        request.skills,

        level_order=(
            request.level_order
        ),
    )