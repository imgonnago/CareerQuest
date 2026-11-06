from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


# ============================================================
# Source 전략
# ============================================================

SourceStrategy = Literal[
    "CENTRAL_PDF",
    "HTML",
    "REVIEW",
]


# ============================================================
# 메디컬캠퍼스 학과 정의
# ============================================================

@dataclass(
    frozen=True
)
class MedicalDepartment:

    name: str

    college: str

    source_strategy: SourceStrategy

    aliases: tuple[
        str,
        ...
    ] = ()

    html_url: (
        str
        | None
    ) = None

    note: (
        str
        | None
    ) = None

    @property
    def all_names(
        self,
    ) -> tuple[
        str,
        ...
    ]:

        return (
            self.name,
            *self.aliases,
        )


# ============================================================
# 공식 메디컬캠퍼스 18개 학과
#
# 기준:
# 건양대학교 캠퍼스별 대학현황 - 메디컬캠퍼스
# ============================================================

MEDICAL_DEPARTMENTS: tuple[
    MedicalDepartment,
    ...
] = (

    # --------------------------------------------------------
    # 의과대학
    # --------------------------------------------------------

    MedicalDepartment(
        name="의학과",

        college="의과대학",

        source_strategy=(
            "CENTRAL_PDF"
        ),
    ),

    MedicalDepartment(
        name="데이터의학과",

        college="의과대학",

        source_strategy=(
            "HTML"
        ),

        html_url=(
            "https://ems.konyang.ac.kr/"
            "dm/sub01_04.do"
        ),

        note=(
            "2026 중앙 연도별 교육과정에는 "
            "별도 PDF가 없으므로 "
            "공식 데이터의학과 홈페이지의 "
            "HTML 교육과정 표를 사용한다."
        ),
    ),

    # --------------------------------------------------------
    # 간호대학
    # --------------------------------------------------------

    MedicalDepartment(
        name="간호학과",

        college="간호대학",

        source_strategy=(
            "CENTRAL_PDF"
        ),
    ),

    # --------------------------------------------------------
    # 의과학계열
    # --------------------------------------------------------

    MedicalDepartment(
        name="작업치료학과",

        college="의과학계열",

        source_strategy=(
            "CENTRAL_PDF"
        ),
    ),

    MedicalDepartment(
        name="병원경영학과",

        college="의과학계열",

        source_strategy=(
            "CENTRAL_PDF"
        ),
    ),

    MedicalDepartment(
        name="안경광학과",

        college="의과학계열",

        source_strategy=(
            "CENTRAL_PDF"
        ),
    ),

    MedicalDepartment(
        name="임상병리학과",

        college="의과학계열",

        source_strategy=(
            "CENTRAL_PDF"
        ),
    ),

    MedicalDepartment(
        name="방사선학과",

        college="의과학계열",

        source_strategy=(
            "CENTRAL_PDF"
        ),
    ),

    MedicalDepartment(
        name="치위생학과",

        college="의과학계열",

        source_strategy=(
            "CENTRAL_PDF"
        ),
    ),

    MedicalDepartment(
        name="물리치료학과",

        college="의과학계열",

        source_strategy=(
            "CENTRAL_PDF"
        ),
    ),

    MedicalDepartment(
        name="응급구조학과",

        college="의과학계열",

        source_strategy=(
            "CENTRAL_PDF"
        ),
    ),

    # --------------------------------------------------------
    # 의료공과계열
    # --------------------------------------------------------

    MedicalDepartment(
        name="의공학과",

        college="의료공과계열",

        source_strategy=(
            "CENTRAL_PDF"
        ),
    ),

    MedicalDepartment(
        name="의료IT공학과",

        college="의료공과계열",

        source_strategy=(
            "CENTRAL_PDF"
        ),

        aliases=(
            "의료IT 공학과",
        ),
    ),

    MedicalDepartment(
        name="의료공간디자인학과",

        college="의료공과계열",

        source_strategy=(
            "CENTRAL_PDF"
        ),
    ),

    MedicalDepartment(
        name="의료신소재학과",

        college="의료공과계열",

        source_strategy=(
            "CENTRAL_PDF"
        ),
    ),

    MedicalDepartment(
        name="제약생명공학과",

        college="의료공과계열",

        source_strategy=(
            "CENTRAL_PDF"
        ),
    ),

    MedicalDepartment(
        name="의료인공지능학과",

        college="의료공과계열",

        source_strategy=(
            "REVIEW"
        ),

        note=(
            "현재 메디컬캠퍼스 학과 현황에는 "
            "의료인공지능학과가 존재하지만 "
            "2026 중앙 연도별 교육과정 목록에는 "
            "별도 학부 교육과정 PDF가 확인되지 않는다. "
            "과거 교육과정 경과 자료나 "
            "대학원 의료인공지능학과 자료를 "
            "2026 학부 교육과정으로 자동 사용하지 않는다."
        ),
    ),

    # --------------------------------------------------------
    # AI·SW융합대학
    # --------------------------------------------------------

    MedicalDepartment(
        name="인공지능학과",

        college="AI·SW융합대학",

        source_strategy=(
            "CENTRAL_PDF"
        ),
    ),
)


# ============================================================
# 조회 함수
# ============================================================

def normalize_department_name(
    value: str,
) -> str:

    return (
        "".join(
            value.split()
        )
        .replace(
            "∙",
            "·",
        )
    )


def get_medical_department(
    name: str,
) -> (
    MedicalDepartment
    | None
):

    normalized = (
        normalize_department_name(
            name
        )
    )

    for department in (
        MEDICAL_DEPARTMENTS
    ):

        for candidate in (
            department
            .all_names
        ):

            if (
                normalize_department_name(
                    candidate
                )
                == normalized
            ):

                return (
                    department
                )

    return None


def central_pdf_departments(
) -> tuple[
    MedicalDepartment,
    ...
]:

    return tuple(
        department

        for department
        in MEDICAL_DEPARTMENTS

        if (
            department
            .source_strategy
            == "CENTRAL_PDF"
        )
    )


def html_departments(
) -> tuple[
    MedicalDepartment,
    ...
]:

    return tuple(
        department

        for department
        in MEDICAL_DEPARTMENTS

        if (
            department
            .source_strategy
            == "HTML"
        )
    )


def review_departments(
) -> tuple[
    MedicalDepartment,
    ...
]:

    return tuple(
        department

        for department
        in MEDICAL_DEPARTMENTS

        if (
            department
            .source_strategy
            == "REVIEW"
        )
    )


# ============================================================
# Registry 자체 검증
# ============================================================

def validate_registry(
) -> None:

    names = [
        department.name

        for department
        in MEDICAL_DEPARTMENTS
    ]

    # 학과 중복
    if (
        len(
            names
        )
        != len(
            set(
                names
            )
        )
    ):

        raise RuntimeError(
            (
                "메디컬캠퍼스 학과 registry에 "
                "중복 학과명이 있습니다."
            )
        )

    # 현재 공식 기준 18개
    if (
        len(
            MEDICAL_DEPARTMENTS
        )
        != 18
    ):

        raise RuntimeError(
            (
                "현재 메디컬캠퍼스 registry는 "
                "18개 학과를 기대합니다. "
                f"현재="
                f"{len(MEDICAL_DEPARTMENTS)}"
            )
        )

    # 2026 중앙 PDF 대상
    if (
        len(
            central_pdf_departments()
        )
        != 16
    ):

        raise RuntimeError(
            (
                "현재 2026 중앙 PDF 대상은 "
                "16개 학과를 기대합니다. "
                f"현재="
                f"{len(central_pdf_departments())}"
            )
        )

    # 데이터의학과 HTML 예외
    if (
        len(
            html_departments()
        )
        != 1
    ):

        raise RuntimeError(
            (
                "HTML 교육과정 예외 학과는 "
                "현재 1개를 기대합니다."
            )
        )

    # 의료인공지능학과 검토 예외
    if (
        len(
            review_departments()
        )
        != 1
    ):

        raise RuntimeError(
            (
                "수동 검토 대상 학과는 "
                "현재 1개를 기대합니다."
            )
        )


# import 되는 순간 registry 오류 확인
validate_registry()