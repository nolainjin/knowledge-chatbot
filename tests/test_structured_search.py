"""구조 인지 검색 — 필드 가중 밴드 + topic 스코프 + 스코어러 seam.

기존 relevance floor·NFC·조사 정규화는 유지하며 그 위에 keywords/tags/topic 10x 밴드를
얹는다. 작은 fixture 로 결정 규칙만 실측한다.
"""

from app.knowledge import Document, score_document, search
from pathlib import Path


def _doc(title="", body="", tags=None, meta=None):
    return Document(
        title=title,
        tags=tags or [],
        body=body,
        path=Path("x.md"),
        rel_path="x.md",
        meta=meta or {},
    )


def test_field_band_boosts_keyword_over_body_noise():
    # keywords(10x) 에만 든 문서가, 본문에 1회만 든 문서보다 위로 온다.
    kw_doc = _doc(title="문서 A", body="관계 없는 본문", meta={"keywords": ["전두엽"], "topic": "07"})
    body_doc = _doc(title="문서 B", body="전두엽 한 번 언급")
    hits = search("전두엽", [body_doc, kw_doc], top_n=2)
    assert hits and hits[0] is kw_doc


def test_tags_and_topic_participate_in_mid_band():
    tag_doc = _doc(title="무관 제목", body="무관 본문", tags=["메타인지"], meta={"topic": "02_메타인지"})
    hits = search("메타인지", [tag_doc], top_n=1)
    assert hits and hits[0] is tag_doc


def test_topic_scope_narrows_to_topic():
    d07 = _doc(title="뇌 문서", body="eeg 전두엽", meta={"topic": "07_체화-감각-멀티모달", "keywords": ["eeg"]})
    d01 = _doc(title="기초 문서", body="eeg 언급", meta={"topic": "01_자기조절-자기주도-기초", "keywords": ["eeg"]})
    scoped = search("eeg", [d01, d07], top_n=5, topic="07_체화-감각-멀티모달")
    assert [d.meta["topic"] for d in scoped] == ["07_체화-감각-멀티모달"]
    # topic=None(기본)은 전체 스코프.
    everyone = search("eeg", [d01, d07], top_n=5)
    assert len(everyone) == 2


def test_relevance_floor_still_blocks_single_common_token():
    # 흔한 2자 토큰 하나(본문 1회)는 floor 미달 -> 0건 유지.
    d = _doc(title="제목", body="학습 한 번")
    assert search("학습", [d]) == []


def test_title_band_still_dominates():
    title_doc = _doc(title="역산 사고 정돈", body="역산")
    noise_doc = _doc(title="일반", body="사고 " * 30, meta={"keywords": ["사고"]})
    hits = search("역산 사고 정돈", [title_doc, noise_doc], top_n=1)
    assert hits[0] is title_doc


def test_score_document_is_the_replaceable_seam():
    # 임베딩 seam: search 는 모듈 상수 _SCORER 를 통해 점수를 낸다.
    import app.knowledge as k

    doc = _doc(title="맞음", body="본문")
    sentinel = object()

    def fake(words, d):
        return (999, {"맞음"}) if d is doc else (0, set())

    original = k._SCORER
    try:
        k._SCORER = fake
        hits = k.search("맞음", [doc], top_n=1)
        assert hits and hits[0] is doc
    finally:
        k._SCORER = original

    # score_document 계약: (int, set[str]) 반환.
    score, matched = score_document(["전두엽"], _doc(meta={"keywords": ["전두엽"]}))
    assert isinstance(score, int) and isinstance(matched, set)
