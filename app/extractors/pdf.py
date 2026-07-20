"""PDF 텍스트 추출 — pdfplumber (D06).

텍스트 레이어가 없는 스캔본은 여기서 정상적으로 빈 문자열을 돌려준다 — 그걸
실패로 볼지 판정하는 0자 분기는 호출자(`app/extractors/__init__.py::extract`)
몫이다(D07). 이 함수는 페이지별 추출 텍스트를 이어붙이기만 한다.
"""

import pdfplumber


def extract(path) -> str:
    with pdfplumber.open(path) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
    return "\n".join(pages)
