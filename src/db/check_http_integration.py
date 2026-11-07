
from __future__ import annotations

import argparse
import sys

import requests


def fetch(
    session: requests.Session,
    base: str,
    path: str,
):
    response = session.get(
        base + path,
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def expect_status(
    response: requests.Response,
    status: int,
    label: str,
):
    if response.status_code != status:
        raise AssertionError(
            f"{label}: HTTP {response.status_code}, "
            f"expected {status}; "
            f"body={response.text[:500]}"
        )

    print(f"[PASS] {label}: HTTP {status}")


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--base",
        default="http://127.0.0.1:8000",
    )

    parser.add_argument(
        "--student-id",
        type=int,
        default=1,
    )

    parser.add_argument(
        "--frontend-origin",
        default="http://localhost:5173",
    )

    args = parser.parse_args()
    base = args.base.rstrip("/")

    with requests.Session() as http:

        # 1. FastAPI + DB 연결
        health = fetch(
            http, base, "/health"
        )

        assert (
            health["status"] == "ok"
            and health.get("database")
        )

        print(
            f"[PASS] 서버/DB: "
            f"{health['database']}"
        )

        # 2. DB와 Skill API 일치 확인
        summary_before = fetch(
            http, base, "/api/summary"
        )

        skills = fetch(
            http, base, "/api/skills"
        )

        assert isinstance(
            skills, list
        ) and skills

        assert (
            summary_before["skill_count"]
            == len(skills)
        )

        print(
            f"[PASS] Skill API: "
            f"{len(skills)}개"
        )

        # 3. 학생 Skill 및 Evidence
        sid = args.student_id

        owned_rows = fetch(
            http,
            base,
            f"/api/students/{sid}/skills",
        )

        evidence = fetch(
            http,
            base,
            f"/api/students/{sid}/skill-evidence",
        )

        assert isinstance(
            owned_rows, list
        ) and isinstance(evidence, list)

        active = {
            int(s["skill_id"]): s["name"]
            for s in skills
        }

        owned = {
            int(s["skill_id"])
            for s in owned_rows
        }

        missing_id = next(
            (
                i for i in active
                if i not in owned
            ),
            None,
        )

        assert missing_id is not None, (
            "테스트용 미보유 Skill이 없습니다."
        )

        rows = [
            {
                "skill_id": missing_id,
                "skill": active[missing_id],
                "doc_count": 3,
            },
            {
                "skill_id": None,
                "skill": "사전에 없는 테스트 역량",
                "doc_count": 1,
            },
        ]

        owned_id = next(
            (
                i for i in owned
                if i in active
            ),
            None,
        )

        if owned_id is not None:
            rows.insert(
                0,
                {
                    "skill_id": owned_id,
                    "skill": active[owned_id],
                    "doc_count": 2,
                },
            )

        print(
            f"[INFO] 학생 {sid}: "
            f"보유 {len(owned_rows)}개, "
            f"Evidence {len(evidence)}건"
        )

        # 4. 실제 HTTP GAP 호출
        payload = {
            "student_id": sid,
            "skills": rows,
        }

        response = http.post(
            base + "/api/gap/calculate",
            json=payload,
            timeout=15,
        )

        expect_status(
            response, 200,
            "SkillRow → GAP",
        )

        result = response.json()

        assert isinstance(result, list)

        by_id = {
            row["skill_id"]: row
            for row in result
        }

        assert (
            by_id[missing_id]["gap"]
            == "미보유"
        )

        assert (
            by_id[None]["gap"]
            == "매핑되지 않음"
        )

        if owned_id is not None:
            assert owned_id not in by_id

        print(
            "[PASS] 미보유/미매핑/보유 제외:",
            [
                (r["skill"], r["gap"])
                for r in result
            ],
        )

        # 5. 잘못된 Skill ID 거부
        bad_id = max(active) + 100000

        invalid = http.post(
            base + "/api/gap/calculate",
            json={
                "student_id": sid,
                "skills": [
                    {"skill_id": bad_id}
                ],
            },
            timeout=15,
        )

        expect_status(
            invalid, 422,
            "없는 Skill ID 거부",
        )

        # 6. 중복 Skill ID 거부
        duplicate = http.post(
            base + "/api/gap/calculate",
            json={
                "student_id": sid,
                "skills": [
                    {"skill_id": missing_id},
                    {"skill_id": missing_id},
                ],
            },
            timeout=15,
        )

        expect_status(
            duplicate, 422,
            "중복 Skill ID 거부",
        )

        # 7. DB에 데이터 변경이 없는지
        summary_after = fetch(
            http, base, "/api/summary"
        )

        for field in (
            "skill_count",
            "student_skill_count",
            "student_count",
        ):
            assert (
                summary_before[field]
                == summary_after[field]
            ), f"{field} 변경됨"

        print(
            "[PASS] 테스트 전후 "
            "DB 주요 건수 동일"
        )

        # 8. 프론트 CORS 사전 요청
        preflight = http.options(
            base + "/api/gap/calculate",
            headers={
                "Origin": args.frontend_origin,
                "Access-Control-Request-Method":
                    "POST",
                "Access-Control-Request-Headers":
                    "content-type",
            },
            timeout=15,
        )

        allowed_origin = (
            preflight.headers.get(
                "access-control-allow-origin"
            )
        )

        if (
            preflight.status_code in (200, 204)
            and allowed_origin in (
                "*",
                args.frontend_origin,
            )
        ):
            print(
                f"[PASS] CORS: "
                f"{args.frontend_origin}"
            )

        else:
            print(
                "[WARN] CORS 미설정/미허용: "
                f"HTTP {preflight.status_code}, "
                f"allow-origin={allowed_origin}"
            )

            print(
                "프론트가 다른 주소에서 호출하면 "
                "CORS 설정이 필요할 수 있습니다."
            )

    print(
        "\n[DONE] HTTP API 통합 "
        "스모크 테스트 완료 (LLM 미실행)"
    )


if __name__ == "__main__":
    try:
        main()

    except (
        requests.RequestException,
        ValueError,
        KeyError,
        AssertionError,
    ) as exc:
        print(
            f"[FAIL] {type(exc).__name__}: "
            f"{exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
