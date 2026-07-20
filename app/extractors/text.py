"""텍스트 계열 추출 — md/txt 는 그대로 읽고, html 은 stdlib 로 태그를 벗긴다.

html 추출은 새 의존성 없이 `html.parser.HTMLParser` 로 script/style 내용을
버리고 나머지 텍스트만 모은다 (D11 — 실물 소스 폴더의 논문 원문 html 8개용,
CAP16 실증 항목).
"""

from html.parser import HTMLParser


def _read_text(path) -> str:
    return path.read_text(encoding="utf-8")


def extract_markdown(path) -> str:
    return _read_text(path)


def extract_plain(path) -> str:
    return _read_text(path)


class _TextOnlyHTMLParser(HTMLParser):
    """script/style 태그 내부 텍스트는 버리고 나머지 데이터만 모은다."""

    def __init__(self):
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth:
            self._chunks.append(data)

    def get_text(self) -> str:
        return "".join(self._chunks)


def extract_html(path) -> str:
    parser = _TextOnlyHTMLParser()
    parser.feed(_read_text(path))
    return parser.get_text()
