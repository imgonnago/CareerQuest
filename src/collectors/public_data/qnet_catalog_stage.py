
from __future__ import annotations

import argparse
import csv
import re

from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import select

from src.db.database import SessionLocal
from src.db.models import CertificateDetail, Resource
from src.loaders.common import upsert_source_document


CSV_PATH = Path(
    "data/raw/certificates/qnet_catalog.csv"
)

SOURCE_NAME = "QNET_CATALOG_CSV_20251231"

SOURCE_URL = (
    "https://www.data.go.kr/data/"
    "15082998/fileData.do"
)

REQUIRED = (
    "자격구분코드",
    "자격구분명",
    "계열명",
    "종목명",
)


def normalize_name(value: str) -> str:
    return re.sub(
        r"\s+", " ", value
    ).strip().casefold()


def load_csv(
    path: Path,
) -> list[dict[str, str]]:

    if not path.is_file():
        raise FileNotFoundError(
            f"CSV가 없습니다: {path}"
        )

    raw = path.read_bytes()
    content = None
    encoding_used = None

    for encoding in (
        "utf-8-sig",
        "cp949",
        "euc-kr",
    ):
        try:
            content = raw.decode(encoding)
            encoding_used = encoding
            break
        except UnicodeDecodeError:
            continue

    if content is None:
        raise ValueError(
            "CSV 인코딩을 식별하지 못했습니다."
        )

    reader = csv.DictReader(
        content.splitlines()
    )

    fields = tuple(
        reader.fieldnames or ()
    )

    if not all(
        name in fields
        for name in REQUIRED
    ):
        raise ValueError(
            f"예상하지 못한 헤더: {fields}"
        )

    rows = []
    seen = set()

    for number, row in enumerate(
        reader,
        start=2,
    ):

        record = {
            key: (row.get(key) or "").strip()
            for key in REQUIRED
        }

        if (
            not record["종목명"]
            or record["자격구분코드"]
            not in ("T", "S")
        ):
            raise ValueError(
                f"{number}행 값 오류: {record}"
            )

        key = (
            record["자격구분코드"],
            normalize_name(
                record["종목명"]
            ),
        )

        if key in seen:
            raise ValueError(
                f"중복 자격종목: {record['종목명']}"
            )

        seen.add(key)
        rows.append(record)

    print(
        f"[CSV] 인코딩={encoding_used}, "
        f"자격종목={len(rows)}건"
    )

    print(
        "[구분]",
        dict(
            Counter(
                row["자격구분명"]
                for row in rows
            )
        ),
    )

    return rows


def existing_qnet_names() -> dict[
    str, set[int]
]:

    index = defaultdict(set)

    with SessionLocal() as db:

        records = db.execute(
            select(
                Resource.resource_id,
                Resource.name,
            )
            .join(
                CertificateDetail,
                CertificateDetail.resource_id
                == Resource.resource_id,
            )
            .where(
                Resource.resource_type
                == "CERTIFICATE",

                Resource.organization
                == "한국산업인력공단",

                CertificateDetail.qualification_code
                .not_like("KUKSIWON:%"),
            )
        ).all()

    for resource_id, name in records:
        index[
            normalize_name(name)
        ].add(resource_id)

    return index


def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "Q-Net 공식 CSV 안전 적재"
        )
    )

    parser.add_argument(
        "--csv",
        type=Path,
        default=CSV_PATH,
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
    )

    parser.add_argument(
        "--apply",
        action="store_true",
    )

    args = parser.parse_args()

    if (
        args.limit is not None
        and args.limit < 1
    ):
        parser.error(
            "--limit는 1 이상"
        )

    rows = load_csv(args.csv)

    known = existing_qnet_names()

    linked = sum(
        len(
            known.get(
                normalize_name(
                    row["종목명"]
                ),
                (),
            )
        ) == 1
        for row in rows
    )

    ambiguous = sum(
        len(
            known.get(
                normalize_name(
                    row["종목명"]
                ),
                (),
            )
        ) > 1
        for row in rows
    )

    print(
        f"[매칭] 기존 자격증 연결={linked}, "
        f"이름 중복={ambiguous}, "
        f"코드 미확인="
        f"{len(rows) - linked - ambiguous}"
    )

    work = (
        rows[:args.limit]
        if args.limit
        else rows
    )

    if not args.apply:

        print(
            "[DRY RUN] DB 변경 없음"
        )

        for row in work[:10]:

            ids = known.get(
                normalize_name(
                    row["종목명"]
                ),
                (),
            )

            status = (
                "기존 자격증 연결 가능"
                if len(ids) == 1
                else "코드 확인 대기"
            )

            print(
                f"  {row['자격구분명']} / "
                f"{row['계열명']} / "
                f"{row['종목명']} "
                f"| {status}"
            )

        return

    total = 0

    # 100건마다 DB 커밋
    for offset in range(
        0,
        len(work),
        100,
    ):

        batch = work[
            offset:offset + 100
        ]

        with SessionLocal() as db:

            with db.begin():

                for row in batch:

                    ids = known.get(
                        normalize_name(
                            row["종목명"]
                        ),
                        set(),
                    )

                    resource_id = (
                        next(iter(ids))
                        if len(ids) == 1
                        else None
                    )

                    verified = (
                        resource_id is not None
                    )

                    metadata = {
                        "source_as_of": "2025-12-31",
                        "name": row["종목명"],
                        "qualification_class_code":
                            row["자격구분코드"],
                        "qualification_class_name":
                            row["자격구분명"],
                        "series": row["계열명"],
                        "official_item_code_in_csv":
                            False,
                        "linked_to_existing_code":
                            verified,
                        "status": (
                            "MATCHED_EXISTING"
                            if verified
                            else "AWAITING_ITEM_CODE"
                        ),
                    }

                    upsert_source_document(
                        db,

                        resource_id=resource_id,

                        source_name=SOURCE_NAME,
                        source_type="PUBLIC_DATA",
                        document_type="CERTIFICATE_INFO",

                        source_record_key=(
                            f"{row['자격구분코드']}:"
                            f"{row['종목명']}"
                        ),

                        source_url=SOURCE_URL,
                        title=row["종목명"],

                        content=(
                            "공식 국가자격 목록 "
                            "(2025-12-31): "
                            f"{row['자격구분명']} / "
                            f"{row['계열명']} / "
                            f"{row['종목명']}"
                        ),

                        raw_metadata=metadata,
                        discovery_channel="OFFICIAL_CSV",

                        fetch_status="SUCCESS",

                        validation_status=(
                            "ACCEPTED"
                            if verified
                            else "PENDING"
                        ),

                        trust_level="A",

                        evidence_eligible=False,
                    )

                    total += 1

        print(
            f"[COMMIT] {total}/{len(work)}건"
        )

    print(
        f"[완료] 공식 목록 {total}건 확보. "
        "개별 코드 미확인 자료는 "
        "새 CERTIFICATE로 생성하지 않았습니다."
    )


if __name__ == "__main__":
    main()
