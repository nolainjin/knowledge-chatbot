"""docx 텍스트 추출 — mammoth (D06). raw text API 만 쓴다 (HTML/MD 변환 태그 불필요)."""

import mammoth


def extract(path) -> str:
    with open(path, "rb") as f:
        result = mammoth.extract_raw_text(f)
    return result.value
