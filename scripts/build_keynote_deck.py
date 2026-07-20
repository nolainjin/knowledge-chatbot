"""Generate a Keynote-openable PPTX deck from the slide source.

No external dependency is used; Keynote can open `.pptx` directly.
"""

from __future__ import annotations

import html
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = REPO_ROOT / "docs" / "slides" / "chatbot_phaseskill_keynote.md"
OUTPUT = REPO_ROOT / "docs" / "slides" / "chatbot_phaseskill_keynote.pptx"

P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _parse_slides(text: str) -> list[dict[str, list[str] | str]]:
    chunks = [chunk.strip() for chunk in text.split("---") if chunk.strip()]
    slides = []
    for chunk in chunks:
        lines = [line.rstrip() for line in chunk.splitlines() if line.strip()]
        title = None
        bullets: list[str] = []
        for line in lines:
            if line.startswith("# ") or line.startswith("## "):
                if title is None:
                    title = re.sub(r"^#+\s*", "", line)
            elif line.startswith("- "):
                bullets.append(line[2:])
            elif re.match(r"^\d+\.\s+", line):
                bullets.append(re.sub(r"^\d+\.\s+", "", line))
            elif line.startswith("> "):
                bullets.append(line[2:])
        if title:
            slides.append({"title": title, "bullets": bullets[:7]})
    return slides


def _tx_body(lines: list[str], font_size: int = 2500) -> str:
    if not lines:
        lines = [""]
    paragraphs = []
    for line in lines:
        safe = html.escape(line)
        paragraphs.append(
            f"""
            <a:p>
              <a:pPr marL="342900" indent="-171450"><a:buChar char="•"/></a:pPr>
              <a:r><a:rPr lang="ko-KR" sz="{font_size}"/><a:t>{safe}</a:t></a:r>
            </a:p>
            """
        )
    return "".join(paragraphs)


def _slide_xml(idx: int, title: str, bullets: list[str]) -> str:
    title_safe = html.escape(title)
    bullet_xml = _tx_body(bullets)
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}">
  <p:cSld>
    <p:bg><p:bgPr><a:solidFill><a:srgbClr val="F7F3ED"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>
    <p:spTree>
      <p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
      <p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
      <p:sp>
        <p:nvSpPr><p:cNvPr id="2" name="Title {idx}"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>
        <p:spPr><a:xfrm><a:off x="609600" y="457200"/><a:ext cx="7924800" cy="914400"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr>
        <p:txBody><a:bodyPr wrap="square"/><a:lstStyle/>
          <a:p><a:r><a:rPr lang="ko-KR" sz="3800" b="1"><a:solidFill><a:srgbClr val="2F3532"/></a:solidFill></a:rPr><a:t>{title_safe}</a:t></a:r></a:p>
        </p:txBody>
      </p:sp>
      <p:sp>
        <p:nvSpPr><p:cNvPr id="3" name="Body {idx}"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>
        <p:spPr><a:xfrm><a:off x="762000" y="1676400"/><a:ext cx="7620000" cy="4572000"/></a:xfrm><a:prstGeom prst="roundRect"><a:avLst/></a:prstGeom><a:solidFill><a:srgbClr val="FFFDFA"/></a:solidFill><a:ln><a:solidFill><a:srgbClr val="DED8CF"/></a:solidFill></a:ln></p:spPr>
        <p:txBody><a:bodyPr wrap="square" lIns="274320" tIns="228600" rIns="274320" bIns="228600"/><a:lstStyle/>{bullet_xml}</p:txBody>
      </p:sp>
      <p:sp>
        <p:nvSpPr><p:cNvPr id="4" name="Footer {idx}"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>
        <p:spPr><a:xfrm><a:off x="609600" y="6480000"/><a:ext cx="7924800" cy="365760"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr>
        <p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="ko-KR" sz="1400"><a:solidFill><a:srgbClr val="6F7D77"/></a:solidFill></a:rPr><a:t>chatbot_phaseskill · prompt-injection-aware intake demo</a:t></a:r></a:p></p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
  <p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sld>'''


def _content_types(slide_count: int) -> str:
    overrides = "".join(
        f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        for i in range(1, slide_count + 1)
    )
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
  <Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>
  <Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>
  <Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>
  {overrides}
</Types>'''


def _presentation_xml(slide_count: int) -> str:
    sld_ids = "".join(f'<p:sldId id="{255+i}" r:id="rId{i}"/>' for i in range(1, slide_count + 1))
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}">
  <p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId{slide_count + 1}"/></p:sldMasterIdLst>
  <p:sldIdLst>{sld_ids}</p:sldIdLst>
  <p:sldSz cx="9144000" cy="6858000" type="screen4x3"/>
  <p:notesSz cx="6858000" cy="9144000"/>
</p:presentation>'''


def _presentation_rels(slide_count: int) -> str:
    rels = []
    for i in range(1, slide_count + 1):
        rels.append(
            f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{i}.xml"/>'
        )
    rels.append(
        f'<Relationship Id="rId{slide_count + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>'
    )
    rels.append(
        f'<Relationship Id="rId{slide_count + 2}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="theme/theme1.xml"/>'
    )
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{''.join(rels)}</Relationships>'''


ROOT_RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
</Relationships>'''

SLIDE_LAYOUT = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldLayout xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}" type="blank"><p:cSld name="Blank"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>'''

SLIDE_MASTER = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldMaster xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld><p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/><p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst></p:sldMaster>'''

MASTER_RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>
</Relationships>'''

LAYOUT_RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/>
</Relationships>'''

THEME = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:theme xmlns:a="{A_NS}" name="chatbot_phaseskill"><a:themeElements><a:clrScheme name="Calm"><a:dk1><a:srgbClr val="2F3532"/></a:dk1><a:lt1><a:srgbClr val="FFFDFA"/></a:lt1><a:dk2><a:srgbClr val="5F7F76"/></a:dk2><a:lt2><a:srgbClr val="F7F3ED"/></a:lt2><a:accent1><a:srgbClr val="5F7F76"/></a:accent1><a:accent2><a:srgbClr val="7B9189"/></a:accent2><a:accent3><a:srgbClr val="AD654F"/></a:accent3><a:accent4><a:srgbClr val="DED8CF"/></a:accent4><a:accent5><a:srgbClr val="6F7D77"/></a:accent5><a:accent6><a:srgbClr val="EDF3EF"/></a:accent6><a:hlink><a:srgbClr val="5F7F76"/></a:hlink><a:folHlink><a:srgbClr val="7B9189"/></a:folHlink></a:clrScheme><a:fontScheme name="System"><a:majorFont><a:latin typeface="Arial"/><a:ea typeface="Apple SD Gothic Neo"/></a:majorFont><a:minorFont><a:latin typeface="Arial"/><a:ea typeface="Apple SD Gothic Neo"/></a:minorFont></a:fontScheme><a:fmtScheme name="Simple"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst><a:lnStyleLst><a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst><a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst><a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme></a:themeElements></a:theme>'''


def build() -> Path:
    slides = _parse_slides(SOURCE.read_text(encoding="utf-8"))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="deck-") as tmp:
        root = Path(tmp)
        (root / "_rels").mkdir(parents=True)
        (root / "ppt" / "_rels").mkdir(parents=True)
        (root / "ppt" / "slides" / "_rels").mkdir(parents=True)
        (root / "ppt" / "slideMasters" / "_rels").mkdir(parents=True)
        (root / "ppt" / "slideLayouts" / "_rels").mkdir(parents=True)
        (root / "ppt" / "theme").mkdir(parents=True)

        (root / "[Content_Types].xml").write_text(_content_types(len(slides)), encoding="utf-8")
        (root / "_rels" / ".rels").write_text(ROOT_RELS, encoding="utf-8")
        (root / "ppt" / "presentation.xml").write_text(_presentation_xml(len(slides)), encoding="utf-8")
        (root / "ppt" / "_rels" / "presentation.xml.rels").write_text(_presentation_rels(len(slides)), encoding="utf-8")
        (root / "ppt" / "slideMasters" / "slideMaster1.xml").write_text(SLIDE_MASTER, encoding="utf-8")
        (root / "ppt" / "slideMasters" / "_rels" / "slideMaster1.xml.rels").write_text(MASTER_RELS, encoding="utf-8")
        (root / "ppt" / "slideLayouts" / "slideLayout1.xml").write_text(SLIDE_LAYOUT, encoding="utf-8")
        (root / "ppt" / "slideLayouts" / "_rels" / "slideLayout1.xml.rels").write_text(LAYOUT_RELS, encoding="utf-8")
        (root / "ppt" / "theme" / "theme1.xml").write_text(THEME, encoding="utf-8")

        for i, slide in enumerate(slides, start=1):
            (root / "ppt" / "slides" / f"slide{i}.xml").write_text(
                _slide_xml(i, str(slide["title"]), list(slide["bullets"])),
                encoding="utf-8",
            )
            (root / "ppt" / "slides" / "_rels" / f"slide{i}.xml.rels").write_text(
                '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
</Relationships>''',
                encoding="utf-8",
            )

        with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in root.rglob("*"):
                if path.is_file():
                    zf.write(path, path.relative_to(root).as_posix())
    return OUTPUT


if __name__ == "__main__":
    print(build())
