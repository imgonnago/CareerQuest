"""가이드 슬롯 정의와 세기 (2~5단계·결과 생성이 같이 씀).

슬롯 = 학생에게 줄 정보의 칸: 채용 요건 · 자격증 · 활동·공모전·교과목 · 추천 포트폴리오.
2단계가 슬롯을 채우고(slot_docs·fill_counts로 셈), 3단계가 Skill 줄을 슬롯에 붙이고, 4단계가 슬롯마다 순위를 정한다.
"""
import re

from engine.helpers.school import course_key
from engine.helpers.text import norm_quote
from engine.schemas import EvidenceCard

# 추출 루프
# [33] 카드 총수(TARGET_CARDS 15) 대신 가이드 슬롯별 채울 문서 수. 슬롯 이름 → (세는 카드 분류, 채울 문서 수). 합계 16
ACTIVITY = "활동·공모전·교과목"
SLOTS: dict[str, tuple[tuple[str, ...], int]] = {
    "채용 요건": (("자격요건", "우대사항", "기술스택"), 4),  # 근거 문서 수
    "자격증": (("자격증",), 4),        # [57] 서로 다른 자격증 수 (어학 성적 TOEIC·OPIc·TOPIK 포함). 추천 상한도 4
    # [58] 교내 활동 + 외부 활동·공모전 + 교과목. 서로 다른 항목 수, 추천 상한도 8 (교내 → 외부 → 교과목 순으로 남김)
    ACTIVITY: (("교내프로그램", "대외활동", "교과목"), 8),
    "추천 포트폴리오": (("프로젝트",), 3),  # [60] 서로 다른 포트폴리오 수 (한 문서의 서로 다른 포트폴리오도 각각 1개). 추천 상한도 3
}
SLOT_OF = {cat: slot for slot, (cats, _) in SLOTS.items() for cat in cats}  # 카드 분류 → 슬롯 (기업정보·기타는 없음)
# [57] 항목(과목·활동·자격증) 수로 세는 슬롯. 나머지는 고유 근거 문서 수
ITEM_SLOTS = {ACTIVITY, "자격증", "추천 포트폴리오"}
# [57] 추천 상한
ITEM_CAPS = {ACTIVITY: 8, "자격증": 4, "추천 포트폴리오": 3}
# [56] 계획·선택·읽기 순서 (출력이 잘리거나 시간 상한에 닿으면 뒤쪽 슬롯이 먼저 빠짐)
SLOT_PRIORITY = ("채용 요건", "자격증", ACTIVITY, "추천 포트폴리오")
# [59] 루프에서 교과목은 활동·공모전·교과목 8칸 중 이만큼까지만 채운 것으로 센다. 교육과정 1건에서 과목 6개가 한 번에 나와
# 1바퀴에 6칸이 차서 활동 검색이 멈춘 일이 있음 (최종 8개는 apply_caps가 교내 → 외부 → 교과목 순으로 남김)
COURSE_FILL_MAX = 3
# [57] 활동(교내프로그램·대외활동)은 문서 하나에서 이만큼만 센다. 공모전 공고 1건의 세부(심사 기준·상 이름·혜택)를 활동 여러 개로 써서
# 문서 1건이 활동 6개를 채운 일이 있음 (모집 공고 모음 글이라도 2개까지). 교과목은 교육과정 1건에 과목이 많아 제한 없음
ACTIVITY_PER_DOC = 2
CERT_SPLIT = re.compile(r"\s*(?:[,/·]|및|또는)\s*")


def item_keys(card: EvidenceCard) -> list[str]:
    """[57] 항목 슬롯에서 카드가 채우는 항목. 교과목은 course_key, 자격증은 item을 쉼표·슬래시로 나눈 이름들
    ("ADsP / AICE Associate" → 2개), 활동은 공백 뺀 item."""
    if card.category == "교과목":
        return [course_key(card)]
    if card.category == "자격증":
        return [norm_quote(n).lower() for n in CERT_SPLIT.split(card.item) if norm_quote(n)] or [norm_quote(card.item).lower()]
    return [norm_quote(card.item).lower()]


def counted_keys(cards: list[EvidenceCard]) -> list[tuple[EvidenceCard, list[str]]]:
    """카드마다 세는 항목. [57] 활동(교내프로그램·대외활동)은 문서마다 처음 나온 ACTIVITY_PER_DOC개까지만 (나머지 카드는 빈 목록)."""
    per_doc: dict[str, list[str]] = {}
    out = []
    for c in cards:
        keys = item_keys(c)
        if c.category in ("교내프로그램", "대외활동"):
            seen = per_doc.setdefault(c.doc_id, [])
            keys = [k for k in keys if k in seen or (len(seen) < ACTIVITY_PER_DOC and not seen.append(k))]
        out.append((c, keys))
    return out


def slot_docs(cards: list[EvidenceCard]) -> dict[str, set[str]]:
    """[33] 슬롯별로 채운 것: 채용 요건·포트폴리오는 근거가 나온 고유 doc_id, [57] 항목 슬롯은 서로 다른 항목(item_keys).
    교과목 카드는 extract_evidence가 건양대 문서·학년 맞는 과목만 남겨 둔다."""
    docs = {slot: set() for slot in SLOTS}
    for card, keys in counted_keys(cards):
        if card.category in SLOT_OF:
            slot = SLOT_OF[card.category]
            if card.category == "교과목":  # [59] fill_counts가 따로 세도록 표시
                keys = [f"교과목:{k}" for k in keys]
            docs[slot].update(keys if slot in ITEM_SLOTS else [card.doc_id])
    return docs


def fill_counts(docs: dict[str, set[str]]) -> dict[str, int]:
    """슬롯별 채운 수. [59] 활동·공모전·교과목에서 교과목은 COURSE_FILL_MAX개까지만 센다."""
    filled = {slot: len(d) for slot, d in docs.items()}
    courses = sum(1 for k in docs[ACTIVITY] if k.startswith("교과목:"))
    filled[ACTIVITY] -= max(0, courses - COURSE_FILL_MAX)
    return filled
