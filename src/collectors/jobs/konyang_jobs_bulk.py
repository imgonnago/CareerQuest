
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.collectors.jobs import konyang_jobs as collector


PROGRESS_PATH = Path(
    "data/raw/jobs/konyang_bulk_progress.json"
)


def read_progress(
    path: Path,
    start: int,
    end: int,
) -> dict:

    if not path.exists():
        return {
            "start": start,
            "end": end,
            "next_page": start,
            "created": 0,
            "updated": 0,
            "batches": 0,
        }

    data = json.loads(
        path.read_text(encoding="utf-8")
    )

    if (
        data.get("start") != start
        or data.get("end") != end
    ):
        raise RuntimeError(
            "진행 파일의 수집 범위가 이번 명령과 다릅니다. "
            "이전 범위를 마치거나 --progress로 "
            "새 경로를 지정하세요."
        )

    return data


def save_progress(
    path: Path,
    state: dict,
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp = path.with_suffix(
        path.suffix + ".tmp"
    )

    temp.write_text(
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    temp.replace(path)


def run(
    args: argparse.Namespace,
) -> int:

    if not args.policy_reviewed:
        print(
            "대량 수집 전 사이트 robots.txt/이용조건 "
            "및 본문 저장 범위를 확인한 뒤 "
            "--policy-reviewed를 붙이세요."
        )
        return 2

    if not (
        1 <= args.start_page <= args.end_page
    ):
        raise ValueError(
            "start-page와 end-page 범위를 확인하세요."
        )

    if not 1 <= args.chunk <= 30:
        raise ValueError(
            "chunk는 1~30 페이지로 지정하세요."
        )

    if args.delay < 1.0:
        raise ValueError(
            "delay는 1.0초 이상으로 지정하세요."
        )

    path = Path(args.progress)

    state = read_progress(
        path,
        args.start_page,
        args.end_page,
    )

    collector.REQUEST_DELAY = args.delay

    print(
        "CareerQuest 공식 게시판 대량 수집: "
        f"{args.start_page}~{args.end_page}"
    )

    print(
        f"모드={'APPLY' if args.apply else 'DRY-RUN'} "
        f"| 다음 페이지={state['next_page']}"
    )

    while state["next_page"] <= args.end_page:

        first = state["next_page"]

        pages = min(
            args.chunk,
            args.end_page - first + 1,
        )

        print(
            f"\n[배치] page "
            f"{first}~{first + pages - 1}"
        )

        # 선택된 페이지의 모든 후보 처리
        jobs = collector.collect_jobs(
            start_page=first,
            pages=pages,
            max_items=None,
            all_jobs=True,
        )

        if not jobs:
            print(
                "수집 0건: 마지막 페이지 또는 "
                "접근/파싱 문제 확인 필요. "
                "진행 기록은 유지합니다."
            )
            return 2

        if not args.apply:
            print(
                f"DRY-RUN 후보 {len(jobs)}건, "
                "저장하지 않았습니다."
            )
            return 0

        outcome = collector.save_jobs(jobs)

        if outcome["failed"]:
            print(
                "이번 배치의 일부 저장 실패. "
                "이미 성공한 건은 중복 키로 "
                "재실행 가능합니다. "
                "진행 포인터는 유지합니다."
            )
            return 1

        state["next_page"] = first + pages
        state["created"] += outcome["created"]
        state["updated"] += outcome["updated"]
        state["batches"] += 1

        save_progress(
            path,
            state,
        )

        print(
            f"[완료] 신규={outcome['created']} "
            f"갱신={outcome['updated']} "
            f"누적 신규={state['created']} "
            f"| 다음={state['next_page']}"
        )

    print(
        f"전체 완료: "
        f"누적 신규={state['created']} "
        f"갱신={state['updated']} "
        f"| 진행 파일={path}"
    )

    return 0


def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "CareerQuest 건양대 채용공고 배치 적재"
        )
    )

    parser.add_argument(
        "--start-page",
        type=int,
        default=21,
    )

    parser.add_argument(
        "--end-page",
        type=int,
        default=696,
    )

    parser.add_argument(
        "--chunk",
        type=int,
        default=10,
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=1.5,
    )

    parser.add_argument(
        "--progress",
        default=str(PROGRESS_PATH),
    )

    parser.add_argument(
        "--policy-reviewed",
        action="store_true",
    )

    parser.add_argument(
        "--apply",
        action="store_true",
    )

    sys.exit(
        run(parser.parse_args())
    )


if __name__ == "__main__":
    main()
