"""OCR 이미지의 괘선 표 → 칸을 살린 행 텍스트 (problem.txt [47]).

OCR 글자 상자만으로 줄을 묶으면 빈 칸이 사라져 "3 | 3 | 3 | 2"가 학점·이론·실습·학년·학기 중 어느 칸인지 알 수 없고,
●/○ 같은 기호는 글자로 검출되지 않아 거의 다 빠진다. 그래서 이미지에서 표의 괘선을 찾아 칸(cell)을 만들고
OCR 상자를 칸에 넣는다. 글자가 없는 칸은 픽셀로 ●/○/빈 칸을 판단한다.

VLM(qwen3.5 vision)으로 표를 다시 쓰게 하는 방식도 비교했으나 그럴듯하게 틀려서(역량 매트릭스 13행 중 6행의
기호 위치가 밀림, "캡스톤" → "챗스톤") 코드로 검증할 수 없는 VLM 대신 괘선 기반으로 정했다.

- 괘선: 어두운 픽셀(LINE_THRESHOLD 미만 — 회색 머리글 배경(약 204)은 빠짐)을 가로·세로 긴 막대로 열기(open)해 남긴다.
- 표: 괘선 덩어리 중 페이지 폭 MIN_TABLE_W·높이 MIN_TABLE_H 이상인 것. 괘선이 없는 표(사진, 선 없는 표)는 못 찾고
  예전처럼 글자 상자 줄 묶기로 처리된다 (ocr.py).
- 칸: 표 안에서 괘선에 둘러싸인 흰 영역. 병합된 칸도 영역 하나로 나온다.
- 행: 칸의 위·아래 경계로 가로 띠를 나누고, 띠마다 걸친 칸을 왼쪽부터 " | "로 잇는다.
  세로로 병합된 칸(예: 이수시기 "1-1")은 짧은 글(REPEAT_MAX_CHARS 이하)이면 걸친 행마다 되풀이해 행만 봐도
  뜻이 통하게 하고, 긴 글은 첫 행에만 둔다(같은 문단이 행마다 반복되지 않게).
- 표 안에 표가 있으면(nested) 칸 구조가 겹쳐 행으로 펼 수 없다 → ocr.py가 예전처럼 글자 상자 줄 묶기로 처리.
"""
import cv2
import numpy as np

LINE_THRESHOLD = 200     # 괘선으로 볼 밝기 상한 (0 검정 ~ 255 흰색)
MIN_TABLE_W = 0.15       # 표로 볼 괘선 덩어리의 최소 폭 (페이지 폭 비율)
MIN_TABLE_H = 0.03
MIN_CELLS = 4
INK_THRESHOLD = 190      # 기호 판단에 쓸 잉크 밝기 상한. 저해상도 ○는 테두리가 회색으로 번져 140이면 고리가 끊김
REPEAT_MAX_CHARS = 20   # 세로 병합 칸을 행마다 되풀이할 글 길이 상한
SYMBOL_CHARS = {"O", "o", "0", "○", "◯", "●", "•", "C", "c", "Q", "◎"}  # OCR이 기호를 글자로 읽은 경우


def find_tables(gray: np.ndarray) -> list[dict]:
    """회색조 이미지 → 표 목록. 표마다 {"bbox": (x1,y1,x2,y2), "cells": [(x1,y1,x2,y2), …]}."""
    h, w = gray.shape
    ink = (gray < LINE_THRESHOLD).astype(np.uint8) * 255
    k = max(20, w // 60)  # 글자 획보다 길고 표 한 칸보다 짧은 막대 (300dpi A4 폭 2480px → 41px)
    horiz = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k, 1)))
    vert = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, k)))
    # 괘선은 가늘다. 두꺼운 어두운 면(쪽 옆 회색 탭, 머리띠, 로고)은 빼야 표에 붙지 않는다
    t = max(6, w // 250)  # 300dpi → 9px. 괘선 두께(2~5px)보다 두껍고 글자 높이보다 얇게
    solid = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (t, t)))
    solid = cv2.dilate(solid, np.ones((5, 5), np.uint8))
    horiz &= ~solid
    vert &= ~solid
    grid = cv2.dilate(horiz | vert, np.ones((3, 3), np.uint8))  # 끊긴 선 이음
    n, _, stats, _ = cv2.connectedComponentsWithStats(grid, connectivity=8)
    tables = []
    for x, y, bw, bh, _ in stats[1:]:
        if bw < w * MIN_TABLE_W or bh < h * MIN_TABLE_H:
            continue
        sub = grid[y:y + bh, x:x + bw]
        # 칸 = 괘선이 아닌 영역의 연결 요소 (4방향이라 대각선으로 새지 않음)
        m, _, cst, _ = cv2.connectedComponentsWithStats((sub == 0).astype(np.uint8), connectivity=4)
        cells = []
        for cx, cy, cw, ch, area in cst[1:]:
            if cw < 8 or ch < 8:
                continue
            # 표 바깥 테두리에 닿는 영역은 표 밖(괘선이 없는 귀퉁이)이다
            if cx == 0 or cy == 0 or cx + cw >= bw or cy + ch >= bh:
                continue
            cells.append((x + cx, y + cy, x + cx + cw, y + cy + ch))
        # 가로선만 있는 장식(머리띠 등)은 칸이 거의 안 나온다
        if len(cells) >= MIN_CELLS:
            tables.append({"bbox": (x, y, x + bw, y + bh), "cells": cells, "nested": _nested(cells)})
    return tables


def _nested(cells: list[tuple]) -> bool:
    """칸 안에 다른 칸이 들어 있으면(표 안의 표 — 현행/개정 비교표 등) True.
    바깥 칸의 흰 영역이 안쪽 표를 둘러싸 하나로 이어지므로 bbox가 다른 칸을 품는다."""
    for a in cells:
        for b in cells:
            if a is not b and a[0] <= b[0] and a[1] <= b[1] and b[2] <= a[2] and b[3] <= a[3]:
                return True
    return False


def _symbol(gray: np.ndarray, cell: tuple) -> str:
    """빈 칸 픽셀 → "●" / "○" / "". 칸 안 잉크 전체의 외곽이 정사각형에 가까울 때만 기호로 본다.
    저해상도·JPEG에서는 ○ 고리가 위아래 두 조각으로 끊기므로 가장 큰 덩어리가 아니라 잉크 전체를 본다."""
    x1, y1, x2, y2 = cell
    m = 4  # 괘선 번짐 제외
    crop = gray[y1 + m:y2 - m, x1 + m:x2 - m]
    if crop.size == 0:
        return ""
    ink = cv2.morphologyEx((crop < INK_THRESHOLD).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, _, st, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    keep = [s for s in st[1:] if s[cv2.CC_STAT_AREA] >= 4]  # 점 잡음 제외
    if not keep:
        return ""
    bx = min(s[0] for s in keep)
    by = min(s[1] for s in keep)
    bw = max(s[0] + s[2] for s in keep) - bx
    bh = max(s[1] + s[3] for s in keep) - by
    side = min(crop.shape)
    if min(bw, bh) < side * 0.2 or max(bw, bh) > side * 0.9 or not 0.75 <= bw / bh <= 1.33:
        return ""
    fill = ink[by:by + bh, bx:bx + bw].mean()  # 꽉 찬 원 ≈ 0.78, 고리 ≈ 0.2~0.4
    if fill >= 0.6:
        return "●"
    # 고리: 가운데가 비어 있어야 함
    cy, cx = by + bh // 2, bx + bw // 2
    r = max(1, min(bw, bh) // 6)
    if fill >= 0.12 and ink[cy - r:cy + r + 1, cx - r:cx + r + 1].mean() < 0.2:
        return "○"
    return ""


def _cell_text(items: list[tuple]) -> str:
    """칸에 든 글자 상자 → 텍스트. 줄(세로)마다 왼쪽부터."""
    if not items:
        return ""
    items = sorted(items, key=lambda it: (it[2] + it[4]) / 2)
    lines: list[list[tuple]] = []
    for it in items:
        cy = (it[2] + it[4]) / 2
        if lines and abs(cy - (lines[-1][0][2] + lines[-1][0][4]) / 2) <= (it[4] - it[2]) * 0.5:
            lines[-1].append(it)
        else:
            lines.append([it])
    return " ".join(" ".join(t for t, *_ in sorted(line, key=lambda it: it[1])) for line in lines)


def _assign(items: list[tuple], cells: list[tuple]) -> dict[int, list[tuple]]:
    """글자 상자를 칸에 넣는다. 한 상자가 여러 칸에 걸치면(OCR이 "29 25", "창의융합실무"처럼 이웃 칸을 한 상자로 읽음)
    낱말 수가 칸 수와 같으면 낱말을, 아니면 글자를 상자 안 위치(글자 폭이 같다고 봄)에 따라 칸마다 나눈다."""
    out: dict[int, list[tuple]] = {}
    for it in items:
        t, x1, y1, x2, y2 = it
        cy = (y1 + y2) / 2
        hit = sorted((i for i, (a, b, c, d) in enumerate(cells)
                      if b <= cy <= d and min(x2, c) - max(x1, a) > (c - a) * 0.3), key=lambda i: cells[i][0])
        if len(hit) > 1:
            words = t.split()
            if len(words) == len(hit):
                parts = dict(zip(hit, words))
            else:
                parts = {}
                step = (x2 - x1) / max(len(t), 1)
                for k, ch in enumerate(t):
                    x = x1 + (k + 0.5) * step
                    i = min(hit, key=lambda i: 0 if cells[i][0] <= x <= cells[i][2]
                            else min(abs(x - cells[i][0]), abs(x - cells[i][2])))
                    parts[i] = parts.get(i, "") + ch
            for i, part in parts.items():
                if part.strip():
                    a, _, c, _ = cells[i]
                    out.setdefault(i, []).append((part.strip(), a, y1, c, y2))
            continue
        cx = (x1 + x2) / 2
        inside = [i for i, (a, b, c, d) in enumerate(cells) if a <= cx <= c and b <= cy <= d]
        if inside:
            i = min(inside, key=lambda i: (cells[i][2] - cells[i][0]) * (cells[i][3] - cells[i][1]))
            out.setdefault(i, []).append(it)
    return out


def render_table(table: dict, items: list[tuple], gray: np.ndarray, char_h: float) -> list[str]:
    """표 하나 + 그 안의 글자 상자 → 행 텍스트 목록."""
    cells = table["cells"]
    by_cell = _assign(items, cells)
    texts = []
    for i, cell in enumerate(cells):
        t = _cell_text(by_cell.get(i, []))
        if t in SYMBOL_CHARS or not t:
            sym = _symbol(gray, cell)
            t = sym or ("" if t in SYMBOL_CHARS else t)
        texts.append(t)
    # 가로 띠: 칸의 위·아래 경계. 글자 높이의 절반보다 가까운 경계는 하나로 (괘선 두께·번짐)
    edges = sorted({c[1] for c in cells} | {c[3] for c in cells})
    merged: list[float] = []
    for e in edges:
        if merged and e - merged[-1] < char_h * 0.5:
            continue
        merged.append(e)
    rows = []
    for top, bottom in zip(merged, merged[1:]):
        mid = (top + bottom) / 2
        row = sorted((c[0], texts[i] if c[1] >= top - char_h * 0.5 or len(texts[i]) <= REPEAT_MAX_CHARS else "")
                     for i, c in enumerate(cells) if c[1] <= mid <= c[3])
        if not row or not any(t for _, t in row):
            continue
        line = " | ".join(t for _, t in row)
        if rows and rows[-1] == line:  # 세로로 병합된 칸만 있는 띠 (같은 행이 되풀이됨)
            continue
        rows.append(line)
    return rows
