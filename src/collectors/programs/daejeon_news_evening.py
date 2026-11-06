
from __future__ import annotations

import argparse
import json
import time

from datetime import datetime, time as clock_time
from pathlib import Path
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from src.collectors.programs import (
    daejeon_programs as m
)


SEOUL = ZoneInfo("Asia/Seoul")

PROGRESS = Path(
    "data/raw/programs/daejeon_news_progress.json"
)

DELAY = 1.5


def current_time() -> datetime:
    return datetime.now(SEOUL)


def write_progress(
    path: Path,
    next_page: int,
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp = path.with_suffix(".tmp")

    temp.write_text(
        json.dumps(
            {
                "next_page": next_page,
                "updated_at": current_time().isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    temp.replace(path)


def main() -> None:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--start-page",
        type=int,
        default=1,
    )

    parser.add_argument(
        "--max-pages",
        type=int,
        default=200,
    )

    parser.add_argument(
        "--stop-at",
        default="23:50",
    )

    parser.add_argument(
        "--progress",
        default=str(PROGRESS),
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

    if args.start_page < 1 or args.max_pages < 1:
        parser.error(
            "start-page/max-pages는 1 이상이어야 합니다."
        )

    if args.apply and not args.policy_reviewed:
        parser.error(
            "게시판 자동 수집 및 DB 저장 조건 확인 후 "
            "--policy-reviewed를 사용하세요."
        )

    try:
        hour, minute = map(
            int,
            args.stop_at.split(":"),
        )
        stop_clock = clock_time(hour, minute)
    except (ValueError, TypeError):
        parser.error(
            "--stop-at 형식은 HH:MM입니다."
        )

    now = current_time()

    deadline = datetime.combine(
        now.date(),
        stop_clock,
        tzinfo=SEOUL,
    )

    if now >= deadline:
        print(
            "오늘 종료 시각이 지났으므로 "
            "수집하지 않습니다."
        )
        return

    path = Path(args.progress)
    next_page = args.start_page

    if path.exists():
        state = json.loads(
            path.read_text(encoding="utf-8")
        )
        next_page = int(state["next_page"])

    end_page = next_page + args.max_pages - 1

    print(
        f"[대전 청년소식] {next_page}~{end_page}페이지, "
        f"종료 {args.stop_at} KST"
    )

    print(
        f"[모드] {'APPLY' if args.apply else 'DRY_RUN'} "
        f"/ 진행 파일: {path}"
    )

    session = m.create_session()

    try:
        for page in range(
            next_page,
            end_page + 1,
        ):

            if current_time() >= deadline:
                print(
                    "[시간 종료] 다음 페이지는 "
                    "다음 실행에서 계속합니다."
                )
                break

            try:
                html = m.get_html(
                    session,
                    m.NEWS_LIST_URL,
                    params={
                        "commonMenuNo": "180_35",
                        "pageIndex": page,
                    },
                )

                soup = BeautifulSoup(
                    html,
                    "html.parser",
                )

                link_count = sum(
                    m.candidate_url_from_news_tag(tag)
                    is not None
                    for tag in soup.find_all(
                        ["a", "button"]
                    )
                )

                if link_count == 0:
                    print(
                        f"[중단] {page}페이지 게시물 링크 0개. "
                        "끝/접근 문제 확인 필요."
                    )
                    break

                candidates = m.parse_news_list(html)

            except Exception as exc:
                print(
                    f"[목록 오류] {page}페이지: {exc}"
                )
                break

            print(
                f"[page {page}] 게시물 링크={link_count}, "
                f"관련 후보={len(candidates)}"
            )

            records = []
            complete = True

            for candidate in candidates:

                if current_time() >= deadline:
                    complete = False
                    break

                title = candidate.title

                # 대학원 입학 모집은 비교과에서 제외
                if "대학원" in title and any(
                    token in title
                    for token in (
                        "신입생",
                        "신(편)입생",
                        "모집요강",
                    )
                ):
                    print(
                        f"  [제외: 대학원 입학] {title}"
                    )
                    continue

                try:
                    detail = m.get_html(
                        session,
                        candidate.detail_url,
                    )

                    parsed = m.parse_program(
                        candidate,
                        detail,
                        include_closed=True,
                        verbose=False,
                    )

                except Exception as exc:
                    print(
                        f"  [상세 오류] {title}: {exc}"
                    )
                    complete = False
                    break

                if parsed is not None:
                    records.append(parsed)

                time.sleep(DELAY)

            # 완료된 부분은 즉시 적재
            if args.apply and records:

                result = m.save_programs(records)

                print(
                    f"  [DB] {result}"
                )

                if result["failed"]:
                    print(
                        "[중단] 저장 실패. "
                        "현재 페이지를 다시 시도합니다."
                    )
                    break

            elif not args.apply:
                print(
                    f"  [DRY_RUN] "
                    f"저장 예정={len(records)}건"
                )

            if not complete:
                print(
                    f"[중단] {page}페이지 미완료. "
                    "다음 실행에서 같은 페이지를 재시도합니다."
                )
                break

            # 페이지가 온전히 처리된 경우만 진도 기록
            if args.apply:
                write_progress(
                    path,
                    page + 1,
                )

            time.sleep(DELAY)

        else:
            print(
                "[완료] 설정한 최대 페이지 "
                "범위를 처리했습니다."
            )

    except KeyboardInterrupt:
        print(
            "\n[사용자 중단] 마지막 완료 페이지까지 "
            "DB와 진행 기록이 유지됩니다."
        )

    finally:
        session.close()


if __name__ == "__main__":
    main()
