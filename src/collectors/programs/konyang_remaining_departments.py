
from __future__ import annotations

import argparse
import json
import re

import requests
from bs4 import BeautifulSoup

from src.collectors.programs import (
    konyang_department_programs as m
)


# ============================================================
# 검증된 학과별 공식 게시판
# key, 학과명, host, board_id
# ============================================================

REGISTRY = [
    (
        "nursing", "간호대학",
        "nursingcollege.konyang.ac.kr",
        "BBSMSTR_000000000559",
    ),
    (
        "occupational_therapy", "작업치료학과",
        "ot.konyang.ac.kr",
        "BBSMSTR_000000000460",
    ),
    (
        "hospital_management", "병원경영학과",
        "hospi.konyang.ac.kr",
        "BBSMSTR_000000000293",
    ),
    (
        "optometry", "안경광학과",
        "opt.konyang.ac.kr",
        "BBSMSTR_000000000305",
    ),
    (
        "clinical_laboratory", "임상병리학과",
        "biomed.konyang.ac.kr",
        "BBSMSTR_000000000321",
    ),
    (
        "radiology", "방사선학과",
        "radio.konyang.ac.kr",
        "BBSMSTR_000000000651",
    ),
    (
        "dental_hygiene", "치위생학과",
        "dental.konyang.ac.kr",
        "BBSMSTR_000000000487",
    ),
    (
        "physical_therapy", "물리치료학과",
        "exer.konyang.ac.kr",
        "BBSMSTR_000000000495",
    ),
    (
        "emergency_medical", "응급구조학과",
        "ems.konyang.ac.kr",
        "BBSMSTR_000000000526",
    ),
    (
        "medical_space_design", "의료공간디자인학과",
        "msdm.konyang.ac.kr",
        "BBSMSTR_000000000337",
    ),
    (
        "pharma_biotech", "제약생명공학과",
        "pharm.konyang.ac.kr",
        "BBSMSTR_000000000361",
    ),
    (
        "data_medicine", "데이터의학과",
        "dm.konyang.ac.kr",
        "BBSMSTR_000000002316",
    ),
    (
        "artificial_intelligence", "인공지능학과",
        "ai.konyang.ac.kr",
        "BBSMSTR_000000001658",
    ),
]


# 기존 필터를 보강하는 프로그램 관련 키워드
EXTRA_PROGRAM_TERMS = (
    "교육안내",
    "교육 안내",
    "실무과정",
    "실무 과정",
    "직무과정",
    "전문과정",
    "자격증 과정",
    "자격증 교육",
    "강좌",
    "초청강연",
    "초청 강연",
    "학술대회",
    "심포지엄",
    "진로체험",
    "기업견학",
    "산업체견학",
    "현장견학",
    "특별교육",
    "해외프로그램",
    "실습 특강",
    "역량강화",
    "BLS Provider",
    "ACLS Provider",
    "GMP 교육",
    "A-ONE",
)

# 실행 시 메모리에서만 확장
# 기존 파일의 내용은 수정하지 않음
m.PROGRAM_TERMS = tuple(
    dict.fromkeys(
        (
            *m.PROGRAM_TERMS,
            *EXTRA_PROGRAM_TERMS,
        )
    )
)


def find_total_pages(html: str) -> int:
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    page_text = soup.get_text(
        " ",
        strip=True,
    )

    # 예: 총게시물 :108 / 페이지 :1/11
    match = re.search(
        r"페이지\s*[:：]?\s*"
        r"[\d,]+\s*/\s*([\d,]+)",
        page_text,
    )

    if match is None:
        raise RuntimeError(
            "총 페이지 수를 확인하지 못했습니다."
        )

    total = int(
        match.group(1).replace(",", "")
    )

    if not 1 <= total <= 500:
        raise RuntimeError(
            f"비정상적인 페이지 수: {total}"
        )

    return total


def read_next_page(
    board: m.DepartmentBoard,
) -> int:

    path = m.progress_path(board)

    if not path.exists():
        return 1

    state = json.loads(
        path.read_text(
            encoding="utf-8",
        )
    )

    return int(
        state.get("next_page", 1)
    )


def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "CareerQuest 메디컬캠퍼스 "
            "나머지 학과 프로그램 통합 수집"
        )
    )

    parser.add_argument(
        "--department",
        default="all",
        choices=[
            "all",
            *(row[0] for row in REGISTRY),
        ],
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=1.5,
    )

    parser.add_argument(
        "--max-pages-per-department",
        type=int,
        default=None,
        help=(
            "한 번의 실행에서 학과당 "
            "최대 처리할 페이지 수"
        ),
    )

    parser.add_argument(
        "--apply",
        action="store_true",
    )

    parser.add_argument(
        "--policy-reviewed",
        action="store_true",
    )

    args = parser.parse_args()

    if args.delay < 1.0:
        parser.error(
            "--delay는 1초 이상이어야 합니다."
        )

    if (
        args.max_pages_per_department
        is not None
        and args.max_pages_per_department < 1
    ):
        parser.error(
            "학과당 페이지 수는 "
            "1 이상이어야 합니다."
        )

    if args.apply and not args.policy_reviewed:
        parser.error(
            "사이트 자동 수집과 DB 저장 조건을 "
            "확인한 뒤 --policy-reviewed 사용"
        )

    selected = [
        row for row in REGISTRY
        if (
            args.department == "all"
            or row[0] == args.department
        )
    ]

    session = requests.Session()

    session.headers.update({
        "User-Agent": (
            "CareerQuest-AcademicProject/0.1 "
            "(low-frequency university notices)"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9",
    })

    completed = []
    remaining = []
    failed = []

    mode = (
        "APPLY"
        if args.apply
        else "PROBE / DRY RUN"
    )

    print("=" * 65)
    print("CareerQuest 학과별 PROGRAM 통합 수집")
    print(f"모드: {mode}")
    print(f"선택 게시판: {len(selected)}개")
    print("=" * 65)

    try:

        for key, name, host, board_id in selected:

            print(
                f"\n{'=' * 65}"
                f"\n[{name}]"
            )

            # 최초에는 페이지 수가 미확인
            initial = m.DepartmentBoard(
                key,
                name,
                host,
                board_id,
                1,
            )

            try:
                first_html = m.fetch(
                    session,
                    initial.list_url,
                    host,
                    {"pageIndex": 1},
                )

                total_pages = find_total_pages(
                    first_html
                )

                board = m.DepartmentBoard(
                    key,
                    name,
                    host,
                    board_id,
                    total_pages,
                )

                first_notices = m.extract_notices(
                    first_html,
                    board,
                    1,
                )

                if not first_notices:
                    raise RuntimeError(
                        "첫 페이지 게시물 파싱 결과 0건"
                    )

                candidates = [
                    notice
                    for notice in first_notices
                    if m.program_title(notice.title)
                ]

                print(
                    f"전체 {total_pages}페이지"
                    f" | 첫 페이지 게시물 "
                    f"{len(first_notices)}건"
                    f" | PROGRAM 후보 "
                    f"{len(candidates)}건"
                )

                for candidate in candidates[:5]:
                    print(
                        f"  [후보] {candidate.title}"
                    )

                # DRY RUN에서는 상세·DB 작업 없이
                # 전체 학과의 게시판만 진단
                if not args.apply:
                    continue

                next_page = read_next_page(
                    board
                )

                if next_page > total_pages:
                    print(
                        "[이미 완료] "
                        f"다음 페이지={next_page}"
                    )
                    completed.append(name)
                    continue

                end_page = total_pages

                if args.max_pages_per_department:
                    end_page = min(
                        total_pages,
                        next_page
                        + args.max_pages_per_department
                        - 1,
                    )

                # 기존 검증된 수집기 재사용
                # 한 페이지씩 PostgreSQL에 저장
                m.collect_board(
                    board,
                    session,
                    start_page=1,
                    end_page=end_page,
                    delay=args.delay,
                    apply=True,
                )

                after = read_next_page(
                    board
                )

                if after > total_pages:
                    completed.append(name)
                    print(
                        f"[완료] {name}: "
                        f"{total_pages}페이지"
                    )
                else:
                    remaining.append(
                        (name, after, total_pages)
                    )
                    print(
                        f"[미완료] {name}: "
                        f"다음 {after}/{total_pages}"
                    )

            except (
                requests.RequestException,
                RuntimeError,
                ValueError,
                OSError,
                KeyError,
                json.JSONDecodeError,
            ) as exc:

                print(
                    f"[건너뜀] {name}: {exc}"
                )

                failed.append(
                    (name, str(exc))
                )

    except KeyboardInterrupt:

        print(
            "\n[중단] 지금까지 완료된 페이지는 "
            "DB와 진행 파일에 유지됩니다."
        )

    finally:
        session.close()

    print("\n" + "=" * 65)
    print("통합 수집 결과")
    print("=" * 65)

    if not args.apply:
        print(
            "PROBE 완료: DB를 변경하지 않았습니다."
        )
    else:
        print(
            f"완료된 학과: {len(completed)}개"
        )

        print(
            f"미완료 학과: {len(remaining)}개"
        )

        for name, next_page, total in remaining:
            print(
                f"  {name}: "
                f"next={next_page}/{total}"
            )

    print(
        f"확인 실패 학과: {len(failed)}개"
    )

    for name, reason in failed:
        print(
            f"  {name}: {reason}"
        )


if __name__ == "__main__":
    main()
