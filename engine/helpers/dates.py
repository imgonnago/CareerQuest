"""날짜 읽기, 오늘·학기, 지난 시기 찾기. 날짜는 정수 YYYYMMDD."""
import re
from datetime import date

FULL_DATE = re.compile(r"(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")
SHORT_DATE = re.compile(r"(?<![\d.])(\d{1,2})\s*[./월]\s*(\d{1,2})\s*일?(?![\d.])")
# 할 일 시기: "2026년 1학기", "2026년 상반기" — 오늘보다 앞이면 판단 오류
TERM_TEXT = re.compile(r"(20\d{2})\s*년\s*(?:([12])\s*학기|(상반기|하반기))")
MONTH_TEXT = re.compile(r"(20\d{2})\s*년\s*(\d{1,2})\s*월")  # [64] "2026년 1월 겨울방학" (오늘 2026년 11월)


def today_int(today: int | None) -> int:
    return today or int(date.today().strftime("%Y%m%d"))


def term_of(day: int) -> tuple[int, int]:
    """YYYYMMDD → (학년도, 학기). 1학기 3~8월, 2학기 9~2월 (1·2월은 앞 해 2학기)."""
    y, m = day // 10000, day // 100 % 100
    return (y, 1) if 3 <= m <= 8 else (y, 2) if m >= 9 else (y - 1, 2)


def past_terms(text: str, today: int) -> list[str]:
    """할 일·이유에 적힌 시기 중 이미 지난 것 ("2026년 1학기"·"2026년 상반기")."""
    now = term_of(today)
    out = []
    for m in TERM_TEXT.finditer(text):
        y = int(m.group(1))
        term = int(m.group(2)) if m.group(2) else (1 if m.group(3) == "상반기" else 2)
        if (y, term) < now:
            out.append(m.group(0))
    for m in MONTH_TEXT.finditer(text):
        if (int(m.group(1)), int(m.group(2))) < (today // 10000, today // 100 % 100):
            out.append(m.group(0))
    return out


def today_text(today: int | None = None) -> str:
    """오늘 날짜와 지금·다음 학기. 예: 2026년 11월 4일 — 지금 2026년 2학기, 다음 학기 2027년 1학기"""
    day = today or int(date.today().strftime("%Y%m%d"))
    y, t = term_of(day)
    ny, nt = (y, 2) if t == 1 else (y + 1, 1)
    # [65] 방학을 학기에 붙여 "2027년 1학기 겨울방학"처럼 쓰는 일이 잦아 이번 방학을 날짜로 적어 준다
    vacation = (f"이번 여름방학 {y}년 7~8월" if t == 1 else f"이번 겨울방학 {y}년 12월~{y + 1}년 2월")
    return (f"{day // 10000}년 {day // 100 % 100}월 {day % 100}일 — 지금 {y}년 {t}학기, {vacation}, 다음 학기 {ny}년 {nt}학기 "
            f"(그 전 학기·연도는 이미 지남. 방학은 \"{y}년 겨울방학\"처럼 쓰고 \"1학기 겨울방학\"처럼 학기에 붙이지 않음)")


def find_dates(line: str, year_hint: int) -> list[int]:
    """줄에서 날짜(YYYYMMDD) 찾기. 연도 없는 "11.30"·"11월 30일"은 같은 줄의 연도, 없으면 문서 작성 연도."""
    out = []
    years = []
    for m in FULL_DATE.finditer(line):
        y, mo, d = map(int, m.groups())
        years.append(y)
        if 1 <= mo <= 12 and 1 <= d <= 31:
            out.append(y * 10000 + mo * 100 + d)
    rest = FULL_DATE.sub(" ", line)
    y = years[-1] if years else year_hint
    if y:
        for m in SHORT_DATE.finditer(rest):
            mo, d = map(int, m.groups())
            if 1 <= mo <= 12 and 1 <= d <= 31:
                out.append(y * 10000 + mo * 100 + d)
    return [v for v in out if 20150101 <= v <= 20351231]
