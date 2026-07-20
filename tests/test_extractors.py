import zipfile

import pytest

from app import extractors

# 텍스트 레이어가 실제로 있는 최소 PDF (BT/Tj 컨텐츠 스트림 직접 기술 — reportlab 등
# 미설치 환경에서도 pdfplumber 가 진짜로 읽어낼 수 있는 실물 텍스트 확보용).
_PDF_WITH_TEXT = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>
endobj
4 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
5 0 obj
<< /Length 44 >>
stream
BT /F1 24 Tf 10 100 Td (Hello World) Tj ET
endstream
endobj
trailer
<< /Root 1 0 R >>
%%EOF
"""

# 텍스트 레이어가 없는 최소 PDF (스캔본 흉내 — content stream 자체가 없다).
_PDF_BLANK = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 72 72]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)


def _write_minimal_docx(path, body_text: str) -> None:
    """mammoth 가 읽을 수 있는 최소 OOXML docx (word/document.xml 만)."""
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:p><w:r><w:t>{body_text}</w:t></w:r></w:p></w:body>"
        "</w:document>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("word/document.xml", document_xml)


def test_registry_covers_md_txt_pdf_docx_html():
    assert set(extractors.EXTRACTORS) == {".md", ".txt", ".html", ".pdf", ".docx"}
    assert all(callable(fn) for fn in extractors.EXTRACTORS.values())


def test_markdown_and_plain_text_extraction(tmp_path):
    md = tmp_path / "doc.md"
    md.write_text("# 제목\n\n본문 내용.\n", encoding="utf-8")
    txt = tmp_path / "doc.txt"
    txt.write_text("그냥 텍스트.\n", encoding="utf-8")

    assert extractors.extract(md) == "# 제목\n\n본문 내용.\n"
    assert extractors.extract(txt) == "그냥 텍스트.\n"


def test_pdf_extraction_returns_real_text_layer(tmp_path):
    p = tmp_path / "sample.pdf"
    p.write_bytes(_PDF_WITH_TEXT)

    assert "Hello World" in extractors.extract(p)


def test_docx_extraction_returns_real_body_text(tmp_path):
    p = tmp_path / "sample.docx"
    _write_minimal_docx(p, "Hello docx world")

    assert "Hello docx world" in extractors.extract(p)


def test_zero_char_pdf_raises_explicit_error_not_silent_empty(tmp_path):
    # GM10: 텍스트 레이어 없는 PDF(스캔본 흉내) -> 빈 문서로 통과하지 않고 명시적 에러
    p = tmp_path / "scan.pdf"
    p.write_bytes(_PDF_BLANK)

    with pytest.raises(extractors.ExtractionError):
        extractors.extract(p)


def test_unsupported_extension_raises_explicit_error_not_silent_skip(tmp_path):
    p = tmp_path / "note.hwp"
    p.write_bytes(b"\x00\x01")

    with pytest.raises(extractors.ExtractionError):
        extractors.extract(p)


def test_html_extractor_registered_as_single_entry_and_strips_script_style(tmp_path):
    # GM15/CAP16: html 추출기가 레지스트리 항목 1개로 등록되어 있고, 실물 소스
    # 폴더의 논문 원문과 같은 형태(제목 + 본문 + script/style)에서 본문만 뽑는가.
    assert extractors.EXTRACTORS[".html"] is extractors.text.extract_html

    html = (
        "<html><head><style>p{color:red}</style><script>var x=1;</script></head>"
        "<body><h1>SRL Online L2</h1>"
        "<p>Self-regulated learning strategies in online courses.</p></body></html>"
    )
    p = tmp_path / "paper.html"
    p.write_text(html, encoding="utf-8")

    out = extractors.extract(p)

    assert "Self-regulated learning" in out
    assert "SRL Online L2" in out
    assert "var x=1" not in out
    assert "color:red" not in out


def test_non_knowledge_set_defined_and_disjoint_from_registry():
    # D12: 지식 아닌 파일(도구·색인·로그) 집합은 EXTRACTORS 와 서로소다.
    assert extractors.NON_KNOWLEDGE == {".py", ".csv", ".jsonl"}
    assert not (set(extractors.EXTRACTORS) & extractors.NON_KNOWLEDGE)


def test_corrupted_pdf_wrapped_as_extraction_error_with_filename(tmp_path):
    # 실물 소스 폴더 첫 빌드에서 실측된 회귀: pdfplumber/pdfminer 가 파싱 단계에서
    # 던지는 raw 예외("No /Root object! - Is this really a PDF?")가 그대로
    # 새어나가면 어느 파일인지도 안 나오는 크래시가 된다(D07 위반). extract() 가
    # 공유 관문에서 이를 ExtractionError 로 감싸고 파일 경로를 메시지에 포함해야
    # 한다.
    p = tmp_path / "corrupted.pdf"
    p.write_bytes(b"%PDF-1.4\n<html>not a real pdf")

    with pytest.raises(extractors.ExtractionError) as exc_info:
        extractors.extract(p)

    assert str(p) in str(exc_info.value)


def test_zero_char_branch_docstring_names_ocr_extension_premise():
    # RED02: 0자 분기가 향후 OCR(llm.py 비전 경로 재사용) 확장 지점이라는 전제가
    # docstring 에서 유실되지 않았는지 확인한다.
    doc = extractors.ExtractionError.__doc__ or ""
    assert "OCR" in doc
    assert "llm.py" in doc
