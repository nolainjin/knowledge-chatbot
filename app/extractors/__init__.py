"""확장자 → 추출기 레지스트리 (D06).

`app/llm.py:217` 의 `_ask_single_backend` 가 이름으로 provider 를 디스패치하는
것과 같은 모양이다 — 확장자를 키로 순수 함수(경로 -> 텍스트)를 찾아 호출할
뿐, `extract()` 는 각 포맷의 내부 사정을 몰라도 된다. 새 포맷 추가는 아래
`EXTRACTORS` 에 항목 1개(+ 순수 추출 함수 1개)면 끝난다(CAP16).

추출기는 팩 규약(frontmatter·H1 요구)을 전혀 모른다 — 그 적합화는 phase-09
(빌드) 책임이다. 여기서 보장하는 것은 "경로 -> 원문 텍스트"뿐이다.
"""

from pathlib import Path

from app.extractors import docx, pdf, text


class ExtractionError(Exception):
    """미지원 포맷이거나 추출 결과가 0자일 때 raise (D07 — 조용한 통과 금지).

    0자는 텍스트 레이어가 없는 스캔 PDF 에서 흔히 나온다. 여기서 에러 대신
    빈 문서로 조용히 통과시키면 사용자는 "문서를 넣었는데 없다고 답한다"는
    최악의 혼란을 겪는다 — G2 와 같은 종류의 No Silent Fallback 위반이다.

    OCR 확장 지점(RED02, 이번 범위 밖): 이 0자 분기는 장차 OCR 을 붙일 때
    에러 대신 `app/llm.py` 의 비전 경로(이미지 -> 텍스트)로 위임하도록 바뀔
    자리다. 지금은 그 전제만 남겨 둔다 — 구현하지 않는다.
    """


EXTRACTORS = {
    ".md": text.extract_markdown,
    ".txt": text.extract_plain,
    ".html": text.extract_html,  # stdlib html.parser — D11, CAP16 실증 항목
    ".pdf": pdf.extract,
    ".docx": docx.extract,
    # hwp·이미지 OCR 은 여기 한 줄 추가로 끝난다 (CAP16 판정 기준, RED02 — 구현은 범위 밖)
}

# 지식이 아닌 파일(소스 폴더의 도구·색인·로그) — 빌드가 제외하되 --report 에 남긴다(D12).
# 미지원 "지식" 포맷(.hwp 등)과는 다르다 — 그쪽은 여전히 extract() 에서 명시적 에러(D07).
# 전례: app/knowledge.py:60 의 "_" 접두 예약 파일 제외.
NON_KNOWLEDGE = {".py", ".csv", ".jsonl"}


def extract(path) -> str:
    path = Path(path)
    fn = EXTRACTORS.get(path.suffix.lower())
    if fn is None:
        raise ExtractionError(f"미지원 포맷: {path.suffix or '(확장자 없음)'} ({path})")
    try:
        extracted = fn(path)
    except ExtractionError:
        raise
    except Exception as e:
        # 손상 파일 등 개별 추출기 내부 파서 예외(예: pdfminer 의
        # "No /Root object!")를 파일 경로가 포함된 ExtractionError 로
        # 통일해 명시적으로 실패시킨다(D07) — 모든 포맷이 이 관문을
        # 지나므로 여기 한 곳이면 전부 커버된다.
        raise ExtractionError(f"추출 실패 — {path}: {e}") from e
    if not extracted.strip():
        raise ExtractionError(
            f"추출 결과 0자 — 텍스트 레이어가 없는 스캔본일 수 있습니다: {path}"
        )
    return extracted
