"""이미지 → 텍스트 (PaddleOCR, 로컬·오프라인).

스캔본 PDF 페이지와 업로드 이미지(PNG/JPG)에 쓴다. Tesseract는 교육과정 표를 크게 깨뜨려서
(예: "AI융합설계" → "AI8} ZI") PaddleOCR(PP-OCRv5, 한국어 인식 모델)을 쓴다.

- 모델은 처음 부를 때 한 번만 올린다(약 1~20초, 첫 실행은 모델 다운로드). 이후 페이지당 수 초(CPU).
- 검출 모델은 .env의 OCR_DET_MODEL로 바꿀 수 있다. 교육과정 PDF로 비교했을 때 품질은 비슷하고
  속도는 mobile 약 2초/쪽, server 약 6~7초/쪽(CPU)이라 실시간 업로드에 맞춰 mobile이 기본.
- 인식 모델이 자주 틀리는 단어는 OCR_FIXES로 고친다 (원문을 바꾸는 게 아니라 원문으로 되돌리는 것).
  보이면 추가할 것 (problem.txt 참고).
- 괘선이 있는 표는 engine/rag/table.py가 칸을 찾아 행마다 " | "로 잇는다. 빈 칸도 자리를 남기고(예:
  "61926A | Clinical Data Warehouse 분석 | 3 | 3 |  | 3 | 2 |"), 글자로 검출되지 않는 ●/○는 픽셀로 판단한다.
- 표 밖 글자는 상자를 줄 단위로 다시 묶는다. 같은 줄에서 간격이 넓은 상자 사이는 " | "로 이어
  괘선 없는 표의 칸 구분이라도 남게 한다. 표와 글 줄은 위에서부터 순서대로 놓는다.
- paddlepaddle 3.3.x는 CPU(oneDNN)에서 NotImplementedError가 나서 3.2.2로 고정했다 (requirements.txt).
"""
import logging
import os
import threading

import cv2
import numpy as np

import engine.config  # noqa: F401  .env 로딩
from engine.rag.table import find_tables, render_table

DET_MODEL = os.getenv("OCR_DET_MODEL", "PP-OCRv5_mobile_det")
REC_MODEL = os.getenv("OCR_REC_MODEL", "korean_PP-OCRv5_mobile_rec")
# 모델 받는 서버 연결 확인(수 초)을 건너뛴다. 모델이 없을 때는 그래도 받아 온다
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

# 인식 모델이 자주 틀리는 표기 → 바른 표기. 교육과정 PDF에서 "딥러닝" 33번 중 31번이 "답러닝"으로 나옴
OCR_FIXES = {
    "답러닝": "딥러닝",
    "증점교양": "중점교양",
    "캡스돈": "캡스톤",
}
MIN_SCORE = 0.5  # 인식 신뢰도가 이보다 낮은 상자는 버린다 (도장·장식 무늬 등)

log = logging.getLogger(__name__)

_ocr = None
# PaddleOCR 객체는 스레드 안전하지 않다 (업로드 API가 동시에 부를 수 있음). 만들 때도 잡는다 —
# 서버 시작 때 미리 올리는 스레드(backend/app.py)와 첫 요청이 동시에 만들지 않게. 다시 들어가도 되게 RLock
_lock = threading.RLock()


def _engine():
    global _ocr
    with _lock:
        return _create() if _ocr is None else _ocr


def _create():
    global _ocr
    if _ocr is None:
        # 무거운 패키지라 OCR이 필요할 때만 import
        from paddleocr import PaddleOCR
        _ocr = PaddleOCR(
            text_detection_model_name=DET_MODEL,
            text_recognition_model_name=REC_MODEL,
            use_doc_orientation_classify=False,  # 스캔본은 대부분 바로 놓여 있다. 켜면 느려짐
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
    return _ocr


def _to_lines(items: list[tuple]) -> list[tuple[float, str]]:
    """글자 상자 → (줄 위쪽 y, 줄 텍스트). 세로 중심이 가까운 상자를 한 줄로, 줄 안에서는 왼쪽부터."""
    if not items:
        return []
    h = _char_height(items)
    items = sorted(items, key=lambda it: (it[2] + it[4]) / 2)
    rows: list[list[tuple]] = []
    for it in items:
        cy = (it[2] + it[4]) / 2
        if rows:
            last = rows[-1]
            last_cy = sum((r[2] + r[4]) / 2 for r in last) / len(last)
            if abs(cy - last_cy) <= h * 0.5:
                last.append(it)
                continue
        rows.append([it])

    lines = []
    for row in rows:
        row.sort(key=lambda it: it[1])
        parts = [row[0][0]]
        for prev, cur in zip(row, row[1:]):
            gap = cur[1] - prev[3]
            parts.append((" | " if gap > h * 1.2 else " ") + cur[0])
        lines.append((min(it[2] for it in row), "".join(parts)))
    return lines


def _char_height(items: list[tuple]) -> float:
    heights = sorted(y2 - y1 for _, _, y1, _, y2 in items)
    return max(heights[len(heights) // 2], 1.0)  # 글자 높이 중앙값


def _layout(texts: list[str], boxes, scores, gray: np.ndarray | None) -> str:
    """글자 상자 + 회색조 이미지 → 본문. 괘선 표는 칸 단위로, 나머지는 줄 단위로, 위에서부터."""
    items = []
    for t, b, s in zip(texts, boxes, scores):
        t = t.strip()
        if not t or s < MIN_SCORE:
            continue
        x1, y1, x2, y2 = (float(v) for v in b)
        items.append((t, x1, y1, x2, y2))
    if not items:
        return ""
    blocks: list[tuple[float, str]] = []
    tables = [t for t in find_tables(gray) if not t["nested"]] if gray is not None else []
    rest = items
    if tables:
        h = _char_height(items)
        rest = []
        inside: list[list[tuple]] = [[] for _ in tables]
        for it in items:
            cx, cy = (it[1] + it[3]) / 2, (it[2] + it[4]) / 2
            for k, tb in enumerate(tables):
                x1, y1, x2, y2 = tb["bbox"]
                if x1 <= cx <= x2 and y1 <= cy <= y2:
                    inside[k].append(it)
                    break
            else:
                rest.append(it)
        for tb, its in zip(tables, inside):
            rows = render_table(tb, its, gray, h)
            if rows:
                blocks.append((tb["bbox"][1], "\n".join(rows)))
    blocks += _to_lines(rest)
    blocks.sort(key=lambda b: b[0])
    return "\n".join(t for _, t in blocks)


def ocr_image(image) -> str:
    """이미지 1장(파일 경로 또는 RGB numpy 배열) → 줄 단위 텍스트."""
    if not isinstance(image, (str, np.ndarray)):
        image = np.asarray(image.convert("RGB"))
    gray = None
    if isinstance(image, np.ndarray):
        if image.ndim == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
            image = image[:, :, ::-1].copy()  # PaddleOCR은 OpenCV처럼 BGR 순서를 기대한다
        else:
            gray = image
    else:
        gray = cv2.imread(image, cv2.IMREAD_GRAYSCALE)
    with _lock:
        try:
            results = _engine().predict(image)
        except RuntimeError as e:
            # 업로드 API 시험 중 한 번 Paddle 내부 오류(std::exception)가 났고 같은 파일로 다시 하면 됐다 → 한 번만 다시
            log.warning("OCR 실패, 한 번 다시 시도: %s", e)
            results = _engine().predict(image)
    text = "\n".join(_layout(r["rec_texts"], r["rec_boxes"], r["rec_scores"], gray) for r in results).strip()
    for wrong, right in OCR_FIXES.items():
        text = text.replace(wrong, right)
    return text
