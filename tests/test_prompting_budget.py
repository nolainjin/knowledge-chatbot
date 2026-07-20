"""Phase 11: 토큰 예산·발췌 회귀.

app/prompting.py:build_doc_section 이 doc.body 전문 대신 예산 내 발췌를 싣는지,
발췌가 결정론적인지, 기존 [untrusted_knowledge] payload 구조(title/path/body)와
작은 문서(기존 4개 팩 수준)의 무동작 회귀를 함께 확인한다.
"""

import json
import pathlib

from app import knowledge, prompting


def _doc(body: str, title: str = "D", rel_path: str = "d.md") -> knowledge.Document:
    return knowledge.Document(
        title=title, tags=[], body=body, path=pathlib.Path(rel_path), rel_path=rel_path, meta={}
    )


def _payload(section: str) -> list:
    """[untrusted_knowledge] 섹션 문자열에서 JSON payload만 파싱해 돌려준다."""
    return json.loads(section.split(prompting._CITATION_INSTRUCTION, 1)[1])


def test_oversized_body_is_excerpted_within_budget():
    big = _doc("가" * 200_000, title="Big", rel_path="big.md")

    section = prompting.build_doc_section([big])

    assert len(section) < 200_000, "예산 미적용 -- 전문이 그대로 실렸다"


def test_excerpt_is_deterministic():
    d = _doc("내용 " * 50_000)

    a = prompting.build_doc_section([d])
    b = prompting.build_doc_section([d])

    assert a == b, "동일 입력인데 발췌 결과가 달라졌다"


def test_empty_docs_still_yields_empty_section():
    assert prompting.build_doc_section([]) == ""


def test_small_docs_pass_through_unclipped():
    """기존 4개 팩(위키 ~550바이트) 수준은 발췌가 사실상 무동작이어야 한다."""
    small = _doc("짧은 위키 본문입니다. " * 10, rel_path="small.md")

    payload = _payload(prompting.build_doc_section([small]))

    assert payload[0]["body"] == small.body


def test_marker_and_payload_keys_survive_excerpt():
    big = _doc("본문. " * 100_000, rel_path="pack/big.md")

    section = prompting.build_doc_section([big])

    assert section.startswith("[untrusted_knowledge]\n")
    payload = _payload(section)
    assert set(payload[0]) == {"title", "path", "body"}
    assert payload[0]["path"] == "pack/big.md"
    assert len(payload[0]["body"]) < len(big.body), "예산 초과 본문이 발췌되지 않았다"


def test_excerpt_prefers_sentence_boundary_when_available():
    sentence = "이것은 근거 문장입니다. "
    d = _doc(sentence * 5000, rel_path="s.md")

    payload = _payload(prompting.build_doc_section([d]))

    assert payload[0]["body"].endswith("."), "문장 경계 대신 중간에서 잘렸다"
