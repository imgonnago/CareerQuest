
from __future__ import annotations

import argparse
import re
import time

from dataclasses import dataclass
from html import unescape
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup, Tag
from sqlalchemy import select

from src.db.database import SessionLocal
from src.db.models import (
    ProgramDetail,
    Resource,
    SourceDocument,
)
from src.loaders.common import sha256_text
from src.collectors.programs.notice_quality import classify_notice


SOURCE_GROUPS = {
    "departments": (
        "KONYANG_DEPARTMENT_PROGRAM_NOTICES",
    ),
    "college": (
        "KONYANG_MEDICAL_COLLEGE_NOTICES",
    ),
    "extra": (
        "KONYANG_OFFICIAL_EXTRACURRICULAR",
    ),
}

META_LABELS = {
    "제목", "작성자", "등록일", "조회",
    "첨부", "수정일", "조회수", "파일", "번호",
}

BAD_MARKERS = (
    "개인정보처리방침",
    "사이트맵",
    "TOP으로",
    "전체메뉴",
    "학과소개 >",
    "학사안내 >",
)

STRONG_ATTR = re.compile(
    r"(?:view[_-]?(?:cont|con|content|body|txt)"
    r"|bbs[_-]?(?:cont|con|content|body)"
    r"|board[_-]?(?:cont|con|content|body)"
    r"|article[_-]?(?:cont|content|body)"
    r"|post[_-]?(?:cont|content|body)"
    r"|detail[_-]?(?:cont|content|body))",
    re.I,
)

WEAK_ATTR = re.compile(
    r"(?:view|content|body|article|con_txt)",
    re.I,
)


def clean(value: str) -> str:
    value = value.replace("\xa0", " ")
    value = value.replace("\u200b", "")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n\s*\n+", "\n", value)
    return value.strip()


def own_text(tag: Tag) -> str:

    copy = BeautifulSoup(
        str(tag),
        "html.parser",
    )

    for junk in copy.select(
        "script,style,noscript,nav,header,"
        "footer,button,.sns,.share,.btn_wrap,"
        ".board_btn,.pagination"
    ):
        junk.decompose()

    return clean(
        copy.get_text("\n", strip=True)
    )


def is_attachment_link(a: Tag) -> bool:

    raw = " ".join(
        str(a.get(key, ""))
        for key in (
            "href", "onclick",
            "download", "title",
        )
    )

    raw += " " + a.get_text(
        " ",
        strip=True,
    )

    return bool(
        re.search(
            r"(filedown|download|fileid|첨부파일"
            r"|\.pdf\b|\.hwp[x]?\b"
            r"|\.docx?\b|\.xlsx?\b"
            r"|\.pptx?\b|\.zip\b)",
            raw,
            re.I,
        )
    )


def has_attachment(
    soup: BeautifulSoup,
) -> bool:

    for th in soup.find_all(["th", "dt"]):

        if "첨부" not in clean(
            th.get_text(" ", strip=True)
        ):
            continue

        parent = (
            th.find_parent("tr")
            or th.parent
        )

        if not parent:
            continue

        td = (
            parent.find("td")
            or parent.find("dd")
        )

        if not td:
            continue

        if any(
            is_attachment_link(a)
            for a in td.find_all("a")
        ):
            return True

        if (
            td.find(
                "input",
                {"type": "hidden"},
            )
            and "FILE_" in str(td)
        ):
            return True

    return any(
        is_attachment_link(a)
        for a in soup.find_all("a")
    )


@dataclass(frozen=True)
class Extracted:
    body: str
    method: str
    attachment: bool
    image_only: bool


def parse_notice(
    html: str,
    expected_title: str = "",
) -> Extracted:

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    title = clean(
        unescape(expected_title)
    )

    page_text = clean(
        soup.get_text(" ", strip=True)
    )

    if title and title not in page_text:
        raise ValueError(
            "상세 페이지에서 저장된 제목을 "
            "찾을 수 없습니다."
        )

    attachment = has_attachment(soup)

    candidates = []
    seen = set()

    def offer(
        tag: Tag,
        method: str,
        priority: int,
    ) -> None:

        if id(tag) in seen:
            return

        seen.add(id(tag))

        value = own_text(tag)

        if not 30 <= len(value) <= 22000:
            return

        if title and value == title:
            return

        if any(
            word in value
            for word in BAD_MARKERS
        ):
            return

        if (
            len(set(value.split())) < 8
            and len(value) < 100
        ):
            return

        labels_in_text = sum(
            word in value[:300]
            for word in (
                "제목", "작성자",
                "등록일", "조회", "첨부",
            )
        )

        if labels_in_text >= 3:
            return

        candidates.append(
            (
                priority,
                len(value),
                value,
                method,
            )
        )

    # 1. 제목/등록일 표의 내용 셀
    for table in soup.find_all("table"):

        labels = {
            clean(
                th.get_text(
                    " ",
                    strip=True,
                )
            )
            for th in table.find_all("th")
        }

        if not (
            {"제목", "등록일"} <= labels
        ):
            continue

        for td in table.find_all("td"):

            tr = td.find_parent("tr")

            th = (
                tr.find("th")
                if tr else None
            )

            if th and clean(
                th.get_text(
                    " ",
                    strip=True,
                )
            ) in META_LABELS:
                continue

            offer(
                td,
                "게시물 표의 본문 셀",
                8,
            )

    # 2. 별도 본문 컨테이너
    for tag in soup.find_all(
        ["div", "section", "article", "td"]
    ):

        attr = (
            " ".join(
                tag.get("class", [])
            )
            + " "
            + str(tag.get("id", ""))
        )

        if STRONG_ATTR.search(attr):

            offer(
                tag,
                f"본문 영역 {attr[:60]}",
                7,
            )

        elif (
            tag.name == "td"
            and (
                tag.has_attr("colspan")
                or WEAK_ATTR.search(attr)
            )
        ):

            offer(
                tag,
                "본문 후보 셀",
                5,
            )

    if not candidates:

        return Extracted(
            "",
            "TEXT_NOT_FOUND",
            attachment,
            bool(soup.select("img")),
        )

    candidates.sort(
        key=lambda row: (
            row[0],
            min(row[1], 4000),
        ),
        reverse=True,
    )

    _, _, body, method = candidates[0]

    # 공개 공지의 연락처는 저장하지 않음
    body = re.sub(
        r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b",
        "[이메일 생략]",
        body,
    )

    body = re.sub(
        r"(?<!\d)01[016789][ -]?"
        r"\d{3,4}[ -]?\d{4}(?!\d)",
        "[휴대전화 생략]",
        body,
    )

    return Extracted(
        body[:16000],
        method,
        attachment,
        False,
    )


def missing(
    doc: SourceDocument,
) -> bool:

    metadata = doc.raw_metadata or {}
    content = clean(
        doc.content or ""
    )

    return (
        not content
        or content == clean(
            doc.title or ""
        )
        or len(content) < 80
        or metadata.get(
            "body_text_available"
        ) is False
        or metadata.get(
            "content_extracted"
        ) is False
    )


def main() -> None:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--scope",
        choices=[
            "departments",
            "college",
            "extra",
            "all",
        ],
        default="all",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=1.5,
    )

    parser.add_argument(
        "--refresh-all",
        action="store_true",
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

    if args.delay < 1:
        parser.error(
            "delay는 1초 이상이어야 합니다."
        )

    if (
        args.limit is not None
        and args.limit < 1
    ):
        parser.error(
            "limit는 1 이상이어야 합니다."
        )

    if (
        args.apply
        and not args.policy_reviewed
    ):
        parser.error(
            "자동 수집·원문 저장 허용 확인 후 "
            "--policy-reviewed를 사용하세요."
        )

    groups = (
        SOURCE_GROUPS.values()
        if args.scope == "all"
        else (
            SOURCE_GROUPS[args.scope],
        )
    )

    sources = tuple(
        name
        for group in groups
        for name in group
    )

    # 수정할 문서 ID/URL만 메모리에 보관
    with SessionLocal() as db:

        docs = db.scalars(
            select(SourceDocument)
            .join(
                Resource,
                Resource.resource_id
                == SourceDocument.resource_id,
            )
            .where(
                SourceDocument.source_name.in_(
                    sources
                ),
                Resource.resource_type
                == "PROGRAM",
            )
            .order_by(
                SourceDocument.source_document_id
            )
        ).all()

        targets = [
            (
                doc.source_document_id,
                doc.source_url,
                doc.title,
            )
            for doc in docs
            if (args.refresh_all or missing(doc))
            and not (doc.raw_metadata or {}).get(
                "program_review_approved", False
            )
        ]

    if args.limit:
        targets = targets[:args.limit]

    print(
        f"[{'APPLY' if args.apply else 'DRY RUN'}] "
        f"복구 대상: {len(targets)}건"
    )

    print(
        f"대상 출처: {sources}"
    )

    session = requests.Session()

    session.headers["User-Agent"] = (
        "CareerQuest-AcademicProject/0.1 "
        "(university notice repair)"
    )

    success = 0
    weak = 0
    errors = 0

    try:

        for index, (
            document_id,
            url,
            title,
        ) in enumerate(targets, 1):

            try:

                if not url:
                    raise ValueError(
                        "원문 URL 없음"
                    )

                parsed_url = urlsplit(url)

                if (
                    parsed_url.scheme != "https"
                    or not parsed_url.hostname
                    or not parsed_url.hostname.endswith(
                        ".konyang.ac.kr"
                    )
                ):
                    raise ValueError(
                        f"허용되지 않은 URL: {url}"
                    )

                response = session.get(
                    url,
                    timeout=(10, 30),
                )

                response.raise_for_status()

                final_url = urlsplit(
                    response.url
                )

                if (
                    final_url.scheme != "https"
                    or final_url.hostname
                    != parsed_url.hostname
                ):
                    raise ValueError(
                        "예상 밖의 사이트 이동"
                    )

                if (
                    "sitemap" in final_url.path.lower()
                    or "login" in final_url.path.lower()
                ):
                    raise ValueError(
                        "Non-article redirect blocked"
                    )

                response.encoding = (
                    response.apparent_encoding
                    or response.encoding
                )

                extracted = parse_notice(
                    response.text,
                    title or "",
                )

                quality = classify_notice(
                    extracted.body, title or ""
                )
                usable = quality == "BODY_REVIEW"

                print(
                    f"[{index}/{len(targets)}] "
                    f"{'BODY' if usable else 'CHECK'} "
                    f"{len(extracted.body)}자 "
                    f"첨부={extracted.attachment}"
                    f" | {title}"
                    f" | {extracted.method}"
                )

                if not args.apply:

                    print(
                        "  미리보기:",
                        repr(
                            extracted.body[:320]
                        ),
                    )

                elif usable:

                    with SessionLocal() as db:

                        doc = db.get(
                            SourceDocument,
                            document_id,
                        )

                        if doc is None:
                            raise ValueError(
                                "저장 문서를 찾지 못함"
                            )

                        metadata = dict(
                            doc.raw_metadata or {}
                        )

                        metadata.update({
                            "body_quality_status": quality,
                            "body_repaired": usable,
                            "body_text_available": usable,
                            "content_extracted": usable,
                            "attachment_present":
                                extracted.attachment,
                            "content_extract_method":
                                extracted.method,
                            "attachment_content_parsed":
                                False,
                        })

                        doc.raw_metadata = metadata

                        if usable:

                            doc.content = (
                                extracted.body
                            )

                            doc.content_hash = (
                                sha256_text(
                                    extracted.body
                                )
                            )

                            doc.validation_status = "PENDING"

                            doc.evidence_eligible = False
                            doc.fetch_status = "SUCCESS"

                            resource = db.get(
                                Resource,
                                doc.resource_id,
                            )

                            if resource is not None:

                                current = (
                                    resource.description
                                    or ""
                                )

                                if (
                                    not current
                                    or clean(current)
                                    == clean(
                                        doc.title or ""
                                    )
                                    or len(current) < 80
                                ):
                                    resource.description = (
                                        extracted.body[:1500]
                                    )

                            detail = db.get(
                                ProgramDetail,
                                doc.resource_id,
                            )

                            if detail is not None:

                                detail.raw_metadata = {
                                    **(
                                        detail.raw_metadata
                                        or {}
                                    ),
                                    **metadata,
                                }

                        else:

                            doc.evidence_eligible = False
                            doc.validation_status = (
                                "PENDING"
                            )

                        db.commit()

                if usable:
                    success += 1
                else:
                    weak += 1

            except Exception as exc:

                errors += 1

                print(
                    f"  [ERROR] {title}: {exc}"
                )

            time.sleep(args.delay)

    except KeyboardInterrupt:

        print(
            "[중단] 이미 복구한 자료는 "
            "DB에 유지됩니다."
        )

    finally:
        session.close()

    print(
        f"[결과] 본문확보={success}, "
        f"미확보={weak}, 오류={errors}"
    )


if __name__ == "__main__":
    main()
