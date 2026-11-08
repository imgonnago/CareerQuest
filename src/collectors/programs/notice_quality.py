from __future__ import annotations

import re


def classify_notice(
    body: str | None,
    title: str | None = None,
) -> str:
    """Classify extracted notice text before evidence approval."""

    content = (body or "").replace("\\n", "\n")
    content = re.sub(r"\s+", " ", content).strip()
    expected = re.sub(r"\s+", " ", title or "").strip()

    if not content or content == expected:
        return "SHORT_OR_EMPTY"

    navigation = (
        "sitemap",
        "homepage guide",
        "login >",
    )
    if any(x in content[:300].lower() for x in navigation):
        return "NAVIGATION"

    if len(content) < 80:
        return "SHORT_OR_EMPTY"

    file_markers = re.findall(
        r"atchFileId=|fileSn=|"
        r"\.hwp(?:x)?\b|\.pdf\b|"
        r"\.zip\b|\.png\b|\.jpg\b",
        content,
        flags=re.I,
    )

    if len(file_markers) >= 2:
        return "ATTACHMENT_REVIEW"

    tokens = re.findall(r"\w{2,}", content)

    if len(content) < 150 or len(tokens) < 20:
        return "MANUAL_REVIEW"

    return "BODY_REVIEW"



def classify_program_notice(title: str) -> str:
    """Classify notice purpose; never grants automatic approval."""
    import re

    name = "".join((title or "").split())

    result_terms = (
        "수상자", "수상결과", "수상소식",
        "입상", "당선작", "결과발표",
        "선정결과", "최종합격자",
        "참여후기", "개최결과", "시상식",
    )

    unrelated_terms = (
        "등록금", "장학금", "휴학", "복학",
        "성적정정", "학사일정", "공결",
        "수강신청", "졸업사정", "주차안내",
        "신편입생", "입학전형",
    )

    program_terms = (
        "모집", "신청", "교육", "프로그램",
        "특강", "워크숍", "세미나", "캠프",
        "경진대회", "공모전", "멘토링",
        "튜터링", "현장실습", "인턴십",
        "아카데미", "부트캠프",
        "포트폴리오", "역량강화",
        "프로젝트", "취업", "진로",
        "연수", "실습", "교육과정",
    )

    if any(term in name for term in result_terms):
        return "RESULT_NOTICE"

    if any(term in name for term in unrelated_terms):
        return "NON_PROGRAM"

    if any(term in name for term in program_terms):
        return "PROGRAM_CANDIDATE"

    return "MANUAL_REVIEW"
