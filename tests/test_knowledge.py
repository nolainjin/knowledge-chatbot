from pathlib import Path

import pytest

from app.knowledge import KnowledgeSourceError, load_documents, search


def test_frontmatter_parsing_and_title_fallback(tmp_path):
    # title 키 없음 -> 본문 H1으로 폴백
    (tmp_path / "with-h1.md").write_text(
        "---\n"
        "type: concept\n"
        'author: "테스터"\n'
        "date: 2026-01-01\n"
        "tags: [foo, bar]\n"
        "---\n"
        "# H1 제목\n\n"
        "본문 내용입니다.\n",
        encoding="utf-8",
    )
    # title도 H1도 없음 -> 파일명 stem으로 폴백
    (tmp_path / "no-h1-stem.md").write_text(
        "---\ntype: event\n---\n본문만 있음.\n",
        encoding="utf-8",
    )

    docs = load_documents(tmp_path)
    by_name = {d.path.name: d for d in docs}

    assert by_name["with-h1.md"].title == "H1 제목"
    assert by_name["with-h1.md"].tags == ["foo", "bar"]
    assert by_name["with-h1.md"].meta["type"] == "concept"
    assert by_name["with-h1.md"].meta["author"] == "테스터"

    assert by_name["no-h1-stem.md"].title == "no-h1-stem"
    assert by_name["no-h1-stem.md"].tags == []


def test_directory_swap(tmp_path):
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    (dir_a / "doc.md").write_text(
        "---\ntype: concept\n---\n# A 문서\n\nA 내용\n", encoding="utf-8"
    )
    (dir_b / "doc.md").write_text(
        "---\ntype: concept\n---\n# B 문서\n\nB 내용\n", encoding="utf-8"
    )

    docs_a = load_documents(dir_a)
    docs_b = load_documents(dir_b)

    assert [d.title for d in docs_a] == ["A 문서"]
    assert [d.title for d in docs_b] == ["B 문서"]


def test_search_ranks_by_keyword_frequency(tmp_path):
    (tmp_path / "one.md").write_text(
        "---\ntags: [커피]\n---\n# 원두 로스팅\n\n원두 원두 로스팅 정보.\n",
        encoding="utf-8",
    )
    (tmp_path / "two.md").write_text(
        "---\ntags: [상담]\n---\n# 초기 면담\n\n면담 절차 안내.\n",
        encoding="utf-8",
    )

    docs = load_documents(tmp_path)
    results = search("원두", docs, top_n=1)

    assert len(results) == 1
    assert results[0].title == "원두 로스팅"


def test_search_no_match_returns_empty(tmp_path):
    (tmp_path / "one.md").write_text(
        "---\n---\n# 아무 문서\n\n내용.\n", encoding="utf-8"
    )
    docs = load_documents(tmp_path)
    assert search("전혀다른단어", docs) == []


def test_search_prioritizes_title_terms_over_body_noise(tmp_path):
    (tmp_path / "target.md").write_text(
        "# 역산 사고 정돈\n\n역산을 활용합니다.\n", encoding="utf-8"
    )
    (tmp_path / "noise.md").write_text(
        "# 일반 설명\n\n" + ("사고 " * 30), encoding="utf-8"
    )

    docs = load_documents(tmp_path)

    assert search("역산 사고 정돈", docs, top_n=1)[0].title == "역산 사고 정돈"


def test_missing_directory_raises_knowledge_source_error():
    with pytest.raises(KnowledgeSourceError):
        load_documents("__no_such_folder__")


def test_empty_directory_raises_knowledge_source_error(tmp_path):
    with pytest.raises(KnowledgeSourceError):
        load_documents(tmp_path)


def test_directory_with_only_reserved_files_raises_knowledge_source_error(tmp_path):
    (tmp_path / "_persona.md").write_text("페르소나만 있음.\n", encoding="utf-8")
    with pytest.raises(KnowledgeSourceError):
        load_documents(tmp_path)


def test_recursive_load_finds_nested_documents(tmp_path):
    (tmp_path / "top.md").write_text(
        "---\ntags: []\n---\n# Top\n\n본문.\n", encoding="utf-8"
    )
    nested = tmp_path / "sub" / "deep"
    nested.mkdir(parents=True)
    (nested / "deep.md").write_text(
        "---\ntags: []\n---\n# Deep\n\n본문.\n", encoding="utf-8"
    )

    docs = load_documents(tmp_path)

    assert {d.title for d in docs} == {"Top", "Deep"}


def test_recursive_load_excludes_nested_reserved_files(tmp_path):
    (tmp_path / "real.md").write_text(
        "---\ntags: []\n---\n# Real\n\n본문.\n", encoding="utf-8"
    )
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "_draft.md").write_text(
        "---\ntags: []\n---\n# Draft\n\n본문.\n", encoding="utf-8"
    )

    docs = load_documents(tmp_path)

    assert {d.title for d in docs} == {"Real"}


def test_rel_path_distinguishes_same_basename_docs_in_different_folders(tmp_path):
    for sub in ("a", "b"):
        folder = tmp_path / sub
        folder.mkdir()
        (folder / "x.md").write_text(
            f"---\ntags: []\n---\n# X in {sub}\n\n본문 {sub}.\n", encoding="utf-8"
        )

    docs = load_documents(tmp_path)

    assert {d.rel_path for d in docs} == {"a/x.md", "b/x.md"}


def test_sample_knowledge_sets_have_min_five_docs():
    knowledge_docs = load_documents(Path("knowledge"))
    alt_docs = load_documents(Path("knowledge-alt"))

    assert len(knowledge_docs) >= 5
    assert len(alt_docs) >= 5
    # 두 지식셋의 도메인이 확연히 달라야 스왑 검증(Phase 6)이 의미가 있다
    knowledge_titles = {d.title for d in knowledge_docs}
    alt_titles = {d.title for d in alt_docs}
    assert knowledge_titles.isdisjoint(alt_titles)


def test_search_matches_nfd_title_with_nfc_query(tmp_path):
    """macOS 실팩 실측(2026-07-18): 파일명 stem 폴백 title이 NFD로 남아 NFC 질의와
    토큰이 어긋나면 title 가중치(100x)가 통째로 죽는다(86/522 문서). _tokenize가
    NFC 정규화해야 한다."""
    import unicodedata

    stem_nfd = unicodedata.normalize("NFD", "논문리스트")
    (tmp_path / f"{stem_nfd}.md").write_text("본문만 있음.\n", encoding="utf-8")

    docs = load_documents(tmp_path)
    hits = search(unicodedata.normalize("NFC", "논문리스트"), docs)

    assert len(hits) == 1
