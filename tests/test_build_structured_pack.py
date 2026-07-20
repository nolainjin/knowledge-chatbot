"""구조화 팩 빌더 — 메타 조인·DOI dedup·8주제 분류·계층·원본 무변경 (결정론 v1).

작은 fixture 로 빌더의 결정 규칙을 실측한다. 실팩 전체 빌드는 별도 수동 검증(스펙 §검증 2).
"""

import hashlib
from pathlib import Path

import yaml

from app.knowledge import load_documents
from app.knowledge_pack import validate_pack
from scripts.build_structured_pack import (
    build_structured_pack,
    classify,
    extract_doi,
    extract_keywords,
    norm_doi,
    norm_title,
    slugify,
)


def _hash_manifest(root: Path) -> dict:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# --- A1 DOI 추출 -----------------------------------------------------------------


def test_extract_doi_single_head():
    assert extract_doi("Title\nDOI: https://doi.org/10.3390/su13137281 blah") == "10.3390/su13137281"


def test_extract_doi_strips_url_suffix_for_join():
    # Frontiers URL 아티팩트(/full,/pdf)를 벗겨 MASTER_INDEX 와 조인되게 한다.
    assert norm_doi("10.3389/feduc.2025.1697554/full") == "10.3389/feduc.2025.1697554"


def test_extract_doi_none_when_absent():
    assert extract_doi("DOI 가 없는 본문입니다.") is None


def test_extract_doi_none_when_multiple_distinct():
    # 리스트/집계 문서(첫머리에 여러 DOI)는 '한 논문의 DOI'가 아니다 -> 키에서 제외.
    head = "논문리스트\n1. 10.3390/su13137281\n2. 10.1016/j.caeai.2023.100182\n"
    assert extract_doi(head) is None


# --- A3 주제 분류 -----------------------------------------------------------------


def test_classify_frontal_eeg_routes_to_topic_07():
    title = "Exploring Frontal EEG Activation in Online Learning"
    body = "EEG EEG EEG beta gamma brainwave neuroscience self-directed learning online"
    primary, tags = classify(title, body)
    assert primary == "07_체화-감각-멀티모달"
    assert "07_체화-감각-멀티모달" in tags


def test_classify_ai_is_whole_word_not_substring():
    # 'ai' 부분문자열(training, domain, available)은 topic 06 으로 오분류하면 안 된다.
    primary, _ = classify("Training in the domain", "available training obtained maintained detail")
    assert primary != "06_AI-에듀테크-학습분석"
    # 반면 표준 단어 'AI chatbot'은 06.
    primary2, _ = classify("AI chatbot for learning", "AI chatbot GPT digital 에듀테크")
    assert primary2 == "06_AI-에듀테크-학습분석"


def test_classify_no_hit_is_기타():
    primary, tags = classify("무관한 제목", "아무 키워드도 없는 본문 xyzzy")
    assert primary == "08_기타"


# --- A2 dedup + keywords ---------------------------------------------------------


def test_dedup_keeps_longest_and_reports_dropped(tmp_path):
    src = tmp_path / "flat"
    src.mkdir()
    short = "---\ntitle: A\nsource: a.md\n---\n\n# A\n\nDOI: 10.1234/x\n짧음"
    long = "---\ntitle: A2\nsource: b.md\n---\n\n# A2\n\nDOI: 10.1234/x\n" + ("길다 " * 200)
    (src / "a.md").write_text(short, encoding="utf-8")
    (src / "b.md").write_text(long, encoding="utf-8")
    for f in ("_persona.md", "_tone.md", "_safety_protocol.md"):
        (src / f).write_text("x\n", encoding="utf-8")

    dst = tmp_path / "structured"
    stats = build_structured_pack(src, dst, master_csv=None)

    assert stats["docs_before"] == 2
    assert stats["docs_after"] == 1
    dropped = "\n".join(stats["dropped_duplicates"])
    assert "b.md" in dropped and "a.md" in dropped  # 양쪽 병기(정본, 버린 것)


def test_extract_keywords_frequency_top_and_stopwords():
    text = "vocabulary vocabulary vocabulary feedback feedback the the the of a is 학습 학습"
    kws = extract_keywords(text, top=8)
    assert "vocabulary" in kws and "feedback" in kws
    assert "the" not in kws and "of" not in kws  # 불용어 제거


# --- A4 계층·카드·백링크 + 코칭 모드 불변식 -------------------------------------


def _mini_src(tmp_path) -> Path:
    src = tmp_path / "flat"
    src.mkdir()
    (src / "p1.md").write_text(
        "---\ntitle: p1\nsource: p1.md\n---\n\n# p1\n\n"
        "self-regulated learning framework metacognition\nDOI: 10.1000/aaa\n",
        encoding="utf-8",
    )
    (src / "p2.md").write_text(
        "---\ntitle: p2\nsource: p2.md\n---\n\n# p2\n\n"
        "online blended distance learning remote lms\nDOI: 10.1000/bbb\n",
        encoding="utf-8",
    )
    for f in ("_persona.md", "_tone.md", "_safety_protocol.md"):
        (src / f).write_text("x\n", encoding="utf-8")
    (src / "_ui.json").write_text('{"greeting": "hi", "chips": []}', encoding="utf-8")
    return src


def test_build_writes_hierarchy_index_and_topic_backlinks(tmp_path):
    src = _mini_src(tmp_path)
    dst = tmp_path / "structured"
    build_structured_pack(src, dst, master_csv=None)

    assert (dst / "_00_INDEX.md").is_file()
    topic_dirs = [p for p in dst.iterdir() if p.is_dir()]
    assert topic_dirs, "주제 폴더가 없다"
    for td in topic_dirs:
        assert (td / "_topic.md").is_file(), f"{td.name} 백링크 없음"
    # _persona 등 복사 + 코칭 모드 불변식(_intake_schema 부재)
    assert (dst / "_persona.md").is_file()
    assert not (dst / "_intake_schema.md").exists()


def test_built_pack_passes_validate_and_loads_meta(tmp_path):
    src = _mini_src(tmp_path)
    dst = tmp_path / "structured"
    build_structured_pack(src, dst, master_csv=None)

    result = validate_pack(dst)
    assert result.valid, [e.as_dict() for e in result.errors]

    docs = load_documents(dst)
    # 마스터 인덱스(_00_INDEX.md)는 네비게이션 파일이라 검색 문서로 로드되지 않는다.
    assert not any("INDEX" in d.rel_path for d in docs), "마스터 인덱스가 검색 대상으로 새어들어옴"
    # 새 프론트매터가 meta 에 보존된다.
    assert docs
    for d in docs:
        assert "topic" in d.meta
        assert "keywords" in d.meta


def test_force_does_clean_rebuild_removing_stale_files(tmp_path):
    # 멱등성: --force 재빌드는 이전 산출물을 남기지 않는다(이름 바뀐 stale 파일이
    # 검색·검증을 오염시키던 실측 버그). build_structured_pack 은 dst 를 새로 쓴다.
    src = _mini_src(tmp_path)
    dst = tmp_path / "structured"
    build_structured_pack(src, dst, master_csv=None)
    stale = dst / "STALE_LEFTOVER.md"
    stale.write_text("---\ntitle: stale\n---\n\n# stale\n\nx\n", encoding="utf-8")

    # main() 의 --force 경로가 dst 를 정리하고 재빌드한다.
    from scripts.build_structured_pack import main

    code = main(["--src", str(src), "--dst", str(dst), "--force"])
    assert code == 0
    assert not stale.exists(), "stale 파일이 --force 재빌드 후에도 남음"


def test_card_frontmatter_has_required_fields(tmp_path):
    src = _mini_src(tmp_path)
    dst = tmp_path / "structured"
    build_structured_pack(src, dst, master_csv=None)

    cards = [p for p in dst.rglob("*.md") if not p.name.startswith("_") and p.name != "00_INDEX.md"]
    assert cards
    text = cards[0].read_text(encoding="utf-8")
    front = yaml.safe_load(text.split("---", 2)[1])
    for key in ("title", "year", "journal", "doi", "type", "topic", "tags", "keywords", "source", "related"):
        assert key in front, f"카드 프론트매터에 {key} 누락"


def test_build_does_not_mutate_master_csv(tmp_path):
    src = _mini_src(tmp_path)
    master = tmp_path / "MASTER_INDEX.csv"
    master.write_text(
        "collected_date,title,year,journal,doi,oa_pdf,status,type\n"
        "2026-01-01,Some Paper,2020,Nature,10.1000/aaa,url,OA,experiment\n",
        encoding="utf-8",
    )
    before = _hash_manifest(src)
    before_master = hashlib.sha256(master.read_bytes()).hexdigest()

    build_structured_pack(src, tmp_path / "structured", master_csv=master)

    assert _hash_manifest(src) == before, "flat 원본이 변경됨"
    assert hashlib.sha256(master.read_bytes()).hexdigest() == before_master, "MASTER_INDEX 변경됨"


def test_master_join_fills_metadata(tmp_path):
    src = _mini_src(tmp_path)
    master = tmp_path / "MASTER_INDEX.csv"
    master.write_text(
        "collected_date,title,year,journal,doi,oa_pdf,status,type\n"
        "2026-01-01,Joined Title,2019,Joined Journal,10.1000/aaa,url,OA,meta-analysis\n",
        encoding="utf-8",
    )
    dst = tmp_path / "structured"
    build_structured_pack(src, dst, master_csv=master)

    joined = None
    for p in dst.rglob("*.md"):
        if p.name.startswith("_") or p.name == "00_INDEX.md":
            continue
        front = yaml.safe_load(p.read_text(encoding="utf-8").split("---", 2)[1])
        if front.get("doi") == "10.1000/aaa":
            joined = front
    assert joined is not None
    assert joined["year"] == 2019
    assert joined["journal"] == "Joined Journal"
    assert joined["type"] == "meta-analysis"


def test_slugify_is_nfc_and_filesystem_safe():
    slug = slugify("Self-Regulated Learning: A Review (2021)")
    assert "/" not in slug and " " not in slug and ":" not in slug
    import unicodedata

    assert unicodedata.is_normalized("NFC", slug)


def test_norm_title_strips_nonalnum():
    assert norm_title("Self-Regulated  Learning!") == norm_title("selfregulatedlearning")
