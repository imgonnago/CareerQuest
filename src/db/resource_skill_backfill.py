from __future__ import annotations

import argparse
import csv
import hashlib
import re
import unicodedata
from pathlib import Path

from sqlalchemy import text

from src.db.database import engine

AMBIGUOUS = {
    "c", "js", "ts", "ml", "dl", "cv", "os", "db",
    "node", "rest", "network", "testing", "test",
    "spring", "statistics", "통계", "시각화",
    "협업", "container", "컨테이너", "문제해결",
    "문제 해결", "팀워크", "ai", "data", "네트워크",
    "테스트", "problem solving", "collaboration",
}

EXTRA = {
    "기본심폐소생술": ("BLS", "CPR"),
    "전문심장소생술 절차 이해": ("ACLS",),
    "DICOM 데이터 처리": ("DICOM",),
    "PACS 연동": ("PACS",),
    "HL7 FHIR 연계": ("FHIR",),
    "SQL": ("SQLD", "SQLP"),
    "Data Analysis": (
        "ADsP", "ADP", "빅데이터분석기사",
    ),
}

CERT_REVIEW = {
    "정보처리기사": (
        "Software Engineering", "Database",
    ),
    "보건의료정보관리사": (
        "의무기록정보 질관리",
        "질병 및 의료행위 분류코딩",
    ),
    "응급구조사": (
        "기본심폐소생술", "응급환자 초기평가",
    ),
    "간호사": (
        "간호사정", "간호중재 수행",
    ),
    "치과위생사": (
        "구강보건 교육", "치면세마 실무",
    ),
    "물리치료사": (
        "근골격계 기능평가", "운동치료 계획",
    ),
    "작업치료사": (
        "작업수행 분석", "일상생활활동 평가",
    ),
    "안경사": (
        "시력 측정", "굴절검사",
    ),
    "임상병리사": (
        "임상화학 검사", "혈액학 검사",
    ),
    "방사선사": (
        "일반 방사선 영상획득", "방사선 안전관리",
    ),
}

HEADINGS = re.compile(
    r"자격\s*요건|지원\s*자격|필수\s*요건"
    r"|필수\s*역량|자격\s*조건"
    r"|우대\s*사항|우대\s*조건|우대\s*역량"
    r"|우대\s*요건|직무\s*내용|담당\s*업무"
    r"|주요\s*업무|근무\s*조건|접수\s*방법"
    r"|전형\s*절차|복리후생"
    r"|교육\s*내용|교육\s*과정|강의\s*내용"
    r"|학습\s*내용|훈련\s*내용|커리큘럼"
    r"|프로그램\s*내용|참여\s*대상"
    r"|신청\s*기간",
    re.I,
)

TEACHING = (
    "교육", "강의", "특강", "과정", "부트캠프",
    "워크숍", "연수", "실습", "아카데미",
    "멘토링", "캠프", "훈련",
)

EXCLUDE_PROGRAM = (
    "수상결과", "수상자", "당선작",
    "시상식", "결과발표", "합격자명단",
)


def norm(s):
    return unicodedata.normalize(
        "NFKC", s or ""
    ).strip()


def redact(s):
    s = re.sub(
        r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
        "[EMAIL]", s,
    )
    return re.sub(
        r"(?<!\d)01[016789][- ]?\d{3,4}"
        r"[- ]?\d{4}(?!\d)",
        "[PHONE]", s,
    )


def patterns_for(skills, aliases):
    names = {
        s["name"]: int(s["skill_id"])
        for s in skills
    }
    terms = {
        int(s["skill_id"]): {s["name"]}
        for s in skills
    }

    for a in aliases:
        sid = int(a["skill_id"])
        if sid in terms:
            terms[sid].add(a["alias"])

    for name, extra in EXTRA.items():
        if name in names:
            terms[names[name]].update(extra)

    result = []

    for sid in sorted(terms):
        values = terms[sid]
        for term in sorted(
            values,
            key=lambda value: (
                -len(norm(value)),
                norm(value).casefold(),
                norm(value),
            ),
        ):
            term = norm(term)

            if (
                len(term) < 2
                or term.casefold() in AMBIGUOUS
            ):
                continue

            expr = re.escape(term).replace(
                r"\ ", r"\s*"
            )

            if (
                term[0].isascii()
                and term[0].isalnum()
            ):
                expr = (
                    r"(?<![A-Za-z0-9_])" + expr
                )

            if (
                term[-1].isascii()
                and term[-1].isalnum()
            ):
                expr += r"(?![A-Za-z0-9_])"

            result.append((
                sid, term, re.compile(expr, re.I)
            ))

    return result


def context_relation(body, position, headings, kind):
    previous = [
        h for h in headings
        if h.end() <= position
    ]

    if not previous:
        return None

    marker = previous[-1]
    distance = position - marker.end()
    label = re.sub(
        r"\s+", "", marker.group()
    )

    if kind == "JOB" and distance <= 400:
        if "우대" in label:
            return "PREFERRED"

        if (
            "자격" in label
            or "필수" in label
        ):
            return "REQUIRED"

    if kind == "PROGRAM" and distance <= 500:
        if any(
            word in label
            for word in (
                "교육내용", "교육과정",
                "강의내용", "학습내용",
                "훈련내용", "커리큘럼",
                "프로그램내용",
            )
        ):
            return "TAUGHT"

    return None


def build_candidates(
    resources, skills, aliases, existing
):
    patterns = patterns_for(skills, aliases)
    skill_names = {
        int(s["skill_id"]): s["name"]
        for s in skills
    }
    name_ids = {
        s["name"]: int(s["skill_id"])
        for s in skills
    }
    result = {}

    def add(
        row, sid, relation, decision,
        confidence, evidence, doc_id, method
    ):
        rid = int(row["resource_id"])

        if (rid, sid) in existing:
            return

        key = (rid, sid)

        candidate = {
            "resource_id": rid,
            "resource_type": row["resource_type"],
            "resource_name": norm(row["name"]),
            "skill_id": sid,
            "skill_name": skill_names[sid],
            "relation_type": relation,
            "decision": decision,
            "confidence": confidence,
            "evidence_text": redact(
                norm(evidence)
            )[:320],
            "source_document_id": doc_id,
            "mapping_method": method,
        }

        if key not in result or (
            decision == "AUTO", confidence
        ) > (
            result[key]["decision"] == "AUTO",
            result[key]["confidence"],
        ):
            result[key] = candidate

    for row in resources:
        kind = row["resource_type"]
        title = norm(row["name"])
        body = norm(
            row.get("source_content")
        )[:16000]

        doc_id = row.get("source_document_id")
        doc_id = int(doc_id) if doc_id else None

        trusted = (
            str(row.get(
                "evidence_eligible"
            )).lower() in ("true", "1")
            and row.get(
                "validation_status"
            ) == "ACCEPTED"
        )

        if kind == "PROGRAM" and any(
            word in title.replace(" ", "")
            for word in EXCLUDE_PROGRAM
        ):
            continue

        headings = (
            list(HEADINGS.finditer(body))
            if kind in ("JOB", "PROGRAM")
            else []
        )

        for sid, term, pattern in patterns:
            match_title = pattern.search(title)

            if match_title:
                relation = {
                    "COURSE": "TAUGHT",
                    "CERTIFICATE": "RELATED",
                    "JOB": "RELATED",
                    "PROGRAM": "RELATED",
                }[kind]

                if (
                    kind == "PROGRAM"
                    and any(
                        x in title for x in TEACHING
                    )
                ):
                    relation = "TAUGHT"

                add(
                    row, sid, relation, "AUTO",
                    0.92 if kind == "COURSE" else 0.88,
                    title,
                    doc_id if trusted else None,
                    "RULE_TITLE_ALL_V1",
                )
                continue

            if (
                kind not in ("JOB", "PROGRAM")
                or not body
            ):
                continue

            matches = list(
                pattern.finditer(body)
            )[:8]

            if not matches:
                continue

            selected = None

            for m in matches:
                relation = context_relation(
                    body, m.start(), headings, kind
                )

                if relation and trusted:
                    selected = (
                        m, relation, "AUTO"
                    )
                    break

            if selected is None:
                selected = (
                    matches[0], "RELATED", "REVIEW"
                )

            m, relation, decision = selected

            evidence = body[
                max(0, m.start() - 90):
                min(len(body), m.end() + 110)
            ]

            add(
                row, sid, relation, decision,
                0.82 if decision == "AUTO" else 0.60,
                evidence,
                doc_id if trusted else None,
                "RULE_CONTEXT_ALL_V1",
            )

        if (
            kind == "CERTIFICATE"
            and title in CERT_REVIEW
        ):
            for name in CERT_REVIEW[title]:
                sid = name_ids.get(name)

                if sid is not None:
                    add(
                        row, sid, "RELATED",
                        "REVIEW", 0.55,
                        "자격증명: " + title
                        + " (검토 필요)",
                        None,
                        "RULE_CERT_REVIEW_V1",
                    )

    return sorted(
        result.values(),
        key=lambda x: (
            x["resource_id"], x["skill_id"]
        ),
    )


def export_csv(path, rows):
    fields = [
        "resource_id", "resource_type",
        "resource_name",
        "skill_id", "skill_name",
        "relation_type", "decision",
        "confidence", "evidence_text",
        "source_document_id", "mapping_method",
    ]

    path.parent.mkdir(
        parents=True, exist_ok=True
    )

    with path.open(
        "w", encoding="utf-8-sig", newline=""
    ) as f:
        writer = csv.DictWriter(
            f, fieldnames=fields
        )
        writer.writeheader()
        writer.writerows(rows)


def run(args):
    with engine.connect() as db:
        if not args.apply:
            db.execute(text(
                "SET TRANSACTION READ ONLY"
            ))

        skills = list(db.execute(text("""
            SELECT skill_id, name
            FROM skill
            WHERE is_active = TRUE
        """)).mappings())

        aliases = list(db.execute(text("""
            SELECT skill_id, alias
            FROM skill_alias
        """)).mappings())

        existing = {
            (int(x[0]), int(x[1]))
            for x in db.execute(text("""
                SELECT resource_id, skill_id
                FROM resource_skill
            """))
        }

        resources = list(db.execute(text("""
            SELECT
                r.resource_id,
                r.resource_type,
                r.name,
                sd.source_document_id,
                sd.content AS source_content,
                sd.evidence_eligible,
                sd.validation_status
            FROM resource r
            LEFT JOIN LATERAL (
                SELECT
                    source_document_id,
                    content,
                    evidence_eligible,
                    validation_status
                FROM source_document
                WHERE resource_id = r.resource_id
                ORDER BY
                    evidence_eligible DESC,
                    source_document_id DESC
                LIMIT 1
            ) sd ON TRUE
            WHERE r.resource_type IN (
                'COURSE',
                'CERTIFICATE',
                'JOB',
                'PROGRAM'
            )
            ORDER BY r.resource_id
        """)).mappings())

        proposed = build_candidates(
            resources, skills, aliases, existing
        )

        auto = [
            x for x in proposed
            if x["decision"] == "AUTO"
        ]
        review = [
            x for x in proposed
            if x["decision"] == "REVIEW"
        ]

        plan = [
            str(x["resource_id"])
            + "|" + str(x["skill_id"])
            + "|" + x["relation_type"]
            + "|" + hashlib.sha256(
                x["evidence_text"].encode("utf-8")
            ).hexdigest()[:12]
            for x in auto
        ]

        digest = hashlib.sha256(
            "\n".join(plan).encode("utf-8")
        ).hexdigest()[:16]

        print("\n[ALL-RESOURCE SKILL PLAN]")
        print("Resources scanned:", len(resources))
        print("Existing links protected:", len(existing))
        print("New automatic links:", len(auto))
        print("Review candidates:", len(review))
        print("Plan digest:", digest)

        for kind in (
            "COURSE", "CERTIFICATE",
            "JOB", "PROGRAM",
        ):
            matches = [
                x for x in auto
                if x["resource_type"] == kind
            ]
            print(
                kind,
                "links:", len(matches),
                "resources:", len({
                    x["resource_id"]
                    for x in matches
                }),
            )

        if args.apply:
            backup = Path(
                args.backup_file or ""
            )

            if not (
                backup.is_file()
                and backup.suffix.lower() == ".dump"
                and backup.stat().st_size > 1048576
            ):
                raise RuntimeError(
                    "Valid DB backup required"
                )

            if (
                not auto
                or args.expected_auto != len(auto)
                or args.plan_digest != digest
            ):
                raise RuntimeError(
                    "Plan mismatch; DB unchanged"
                )

            statement = text("""
                INSERT INTO resource_skill (
                    resource_id, skill_id,
                    relation_type, evidence_text,
                    mapping_method, confidence,
                    verified, source_document_id
                )
                VALUES (
                    :resource_id, :skill_id,
                    :relation_type, :evidence_text,
                    :mapping_method, :confidence,
                    FALSE, :source_document_id
                )
                ON CONFLICT (
                    resource_id, skill_id, relation_type
                ) DO NOTHING
            """)

            fields = (
                "resource_id", "skill_id",
                "relation_type", "evidence_text",
                "mapping_method", "confidence",
                "source_document_id",
            )

            for x in auto:
                db.execute(
                    statement,
                    {k: x[k] for k in fields},
                )

            db.commit()
            print(
                "[DB COMMITTED]",
                len(auto), "attempted",
            )

        else:
            db.rollback()
            output = Path(
                "data/reports/skill_mapping"
            )

            export_csv(
                output / "resource_skill_auto_plan.csv",
                auto,
            )
            export_csv(
                output / "resource_skill_review_queue.csv",
                review,
            )

            print("[REPORTS]", output)
            print("[READ ONLY] DB unchanged")


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--apply", action="store_true"
    )
    parser.add_argument("--backup-file")
    parser.add_argument(
        "--expected-auto", type=int
    )
    parser.add_argument("--plan-digest")

    run(parser.parse_args())


if __name__ == "__main__":
    main()
