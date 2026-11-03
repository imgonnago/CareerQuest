"""이미지 → 텍스트 (PaddleOCR, 로컬·오프라인).

스캔본 PDF 페이지와 업로드 이미지(PNG/JPG)에 쓴다. Tesseract는 교육과정 표를 크게 깨뜨려서
(예: "AI융합설계" → "AI8} ZI") PaddleOCR(PP-OCRv5, 한국어 인식 모델)을 쓴다.

- 모델은 처음 부를 때 한 번만 올린다(약 1~20초, 첫 실행은 모델 다운로드). 이후 페이지당 수 초(CPU).
- 검출 모델은 .env의 OCR_DET_MODEL로 바꿀 수 있다. 교육과정 PDF로 비교했을 때 품질은 비슷하고
  속도는 mobile 약 2초/쪽, server 약 6~7초/쪽(CPU)이라 실시간 업로드에 맞춰 mobile이 기본.
- 인식 모델이 자주 틀리는 단어는 OCR_FIXES로 고친다 (원문을 바꾸는 게 아니라 원문으로 되돌리는 것).
  보이면 추가할 것 (problem.txt 참고).
- 결과는 글자 상자를 줄 단위로 다시 묶은 텍스트. 같은 줄에서 간격이 넓은 상자 사이는 " | "로 이어
  표의 칸 구분이 남게 한다 (예: "61901A | 인공지능의 이해 | 2 | 2 | 1 | 1").
- paddlepaddle 3.3.x는 CPU(oneDNN)에서 NotImplementedError가 나서 3.2.2로 고정했다 (requirements.txt).
"""
import os
import threading

import numpy as np

import engine.config  # noqa: F401  .env 로딩

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

_ocr = None
_lock = threading.Lock()  # PaddleOCR 객체는 스레드 안전하지 않다 (업로드 API가 동시에 부를 수 있음)


def _engine():
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


def _to_lines(texts: list[str], boxes, scores) -> str:
    """글자 상자 → 줄 텍스트. 세로 중심이 가까운 상자를 한 줄로, 줄 안에서는 왼쪽부터."""
    items = []
    for t, b, s in zip(texts, boxes, scores):
        t = t.strip()
        if not t or s < MIN_SCORE:
            continue
        x1, y1, x2, y2 = (float(v) for v in b)
        items.append((t, x1, y1, x2, y2))
    if not items:
        return ""
    heights = sorted(y2 - y1 for _, _, y1, _, y2 in items)
    h = max(heights[len(heights) // 2], 1.0)  # 글자 높이 중앙값

    items.sort(key=lambda it: (it[2] + it[4]) / 2)
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
        lines.append("".join(parts))
    return "\n".join(lines)


def ocr_image(image) -> str:
    """이미지 1장(파일 경로 또는 RGB numpy 배열) → 줄 단위 텍스트."""
    if not isinstance(image, (str, np.ndarray)):
        image = np.asarray(image.convert("RGB"))
    if isinstance(image, np.ndarray) and image.ndim == 3:
        image = image[:, :, ::-1].copy()  # PaddleOCR은 OpenCV처럼 BGR 순서를 기대한다
    with _lock:
        results = _engine().predict(image)
    text = "\n".join(_to_lines(r["rec_texts"], r["rec_boxes"], r["rec_scores"]) for r in results).strip()
    for wrong, right in OCR_FIXES.items():
        text = text.replace(wrong, right)
    return text
