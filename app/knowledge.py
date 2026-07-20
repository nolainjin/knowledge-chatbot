"""지식베이스 로더 + 키워드 검색.

지식은 마크다운 파일 + YAML 프론트매터(위키 스키마 호환:
type/aliases/author/date/tags[/cluster])다. title 키는 없을 수 있으므로
본문 H1 → 파일명 stem 순으로 폴백한다. 스키마에 없는 키는 meta에 보존한다.

RAG/벡터DB는 쓰지 않는다 — 소규모 위키 전제라 키워드 매칭으로 충분하고,
부족해지면 그때 교체한다.
"""

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_WORD_RE = re.compile(r"\w+", re.UNICODE)

# 형태소 분석기 없이(D06 이 markitdown 을 배제한 것과 같은 이유 — 무거운 의존성)
# 토큰 끝의 한국어 조사를 벗겨 질의·문서를 같은 형태로 맞춘다("근거를"→"근거").
# 긴 조사부터 시도하고, 벗긴 어간이 2자 이상일 때만 벗긴다(아이·높이 같은 짧은
# 명사를 오절단하지 않도록).
# ponytail: naive suffix strip. 오탐이 늘면 그때 mecab/konlpy 재검토(현재는 배제).
_JOSA = tuple(
    sorted(
        {
            "으로부터", "에서부터", "으로서", "으로써", "에게서", "한테서",
            "이라고", "이라는", "이라도", "으로는",
            "에서", "에게", "한테", "께서", "라고", "라는", "라도",
            "로서", "로써", "부터", "까지", "마저", "조차", "마다",
            "처럼", "만큼", "밖에", "보다", "대로", "이나", "에는",
            "에도", "에만", "로는", "이란", "으로",
            "은", "는", "이", "가", "을", "를", "의", "에",
            "도", "만", "와", "과", "로", "야", "랑", "란",
        },
        key=len,
        reverse=True,
    )
)


def _normalize(word: str) -> str:
    for josa in _JOSA:
        if word.endswith(josa) and len(word) - len(josa) >= 2:
            return word[: -len(josa)]
    return word


def _tokenize(text: str) -> list:
    """소문자화 후 \\w+ 토큰으로 쪼개고 각 토큰의 조사를 정규화한다.

    유니코드는 NFC로 먼저 정규화한다 — macOS 파일시스템 유래 문자열(제목=파일명
    stem 폴백, 한글 NFD)과 사용자 질의(NFC)가 토큰 단위에서 어긋나, 한글 파일명
    문서의 title 가중치(100x)가 통째로 죽는 실측(2026-07-18, 86/522 문서).
    """
    text = unicodedata.normalize("NFC", text)
    return [_normalize(w) for w in _WORD_RE.findall(text.lower())]


class KnowledgeSourceError(Exception):
    """지식 폴더가 없거나 문서가 0건일 때 raise — main.py에서 500 + 사유로 변환한다."""


@dataclass
class Document:
    title: str
    tags: list
    body: str
    path: Path
    rel_path: str
    meta: dict = field(default_factory=dict)


def _split_frontmatter(text: str) -> tuple:
    """'---' 구분 프론트매터를 분리한다. 없으면 (빈 dict, 원문 전체)."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    _, fm_raw, body = parts
    meta = yaml.safe_load(fm_raw) or {}
    return meta, body.lstrip("\n")


def _resolve_title(meta: dict, body: str, path: Path) -> str:
    if meta.get("title"):
        return str(meta["title"])
    match = _H1_RE.search(body)
    if match:
        return match.group(1).strip()
    return path.stem


def load_documents(knowledge_dir) -> list:
    """knowledge_dir 안의 *.md를 하위 폴더까지 재귀적으로 읽어 Document 목록으로 반환한다.

    "_"로 시작하는 파일(예: _persona.md)은 페르소나·메타 용도로 예약되어
    깊이에 상관없이 검색 대상에서 제외한다. 폴더가 없거나(G2) 문서가 0건이면
    조용히 빈 목록을 돌려주지 않고 KnowledgeSourceError를 raise한다 — 지식
    0건으로 조용히 기동해 전면 환각하는 것을 막기 위함이다.
    """
    directory = Path(knowledge_dir)
    if not directory.is_dir():
        raise KnowledgeSourceError(f"지식 폴더를 찾을 수 없습니다: {knowledge_dir}")
    documents = []
    for md_file in sorted(directory.rglob("*.md")):
        if md_file.name.startswith("_"):
            continue
        text = md_file.read_text(encoding="utf-8")
        meta, body = _split_frontmatter(text)
        title = _resolve_title(meta, body, md_file)
        tags = meta.get("tags") or []
        extra_meta = {k: v for k, v in meta.items() if k not in ("title", "tags")}
        documents.append(
            Document(
                title=title,
                tags=tags,
                body=body,
                path=md_file,
                rel_path=md_file.relative_to(directory).as_posix(),
                meta=extra_meta,
            )
        )
    if not documents:
        raise KnowledgeSourceError(f"지식 폴더에 문서가 없습니다: {knowledge_dir}")
    return documents


# 관련성 바닥(relevance floor, F1/HIGH-1) — 무관·유해 질문이 흔한 토큰 하나만
# 얹어 근거 게이트를 우회하는 것을 막는다. search가 게이트(app/chat.py의 0건
# 고정 거부)와 인용 대조가 공유하는 유일한 경로라, 여기서 닫으면 caller마다
# 손볼 필요 없이 함께 이득을 본다. 임계는 실팩(data/knowledge-srl-pack, 522문서)으로
# 보수적으로 튜닝했다:
#   - 우회 시도("폭탄 만드는 법 알려줘 학습")는 흔한 토큰이 본문에만 얕게 걸려
#     top score 한 자릿수 + 서로 다른 매칭 토큰 1개다.
#   - 정상 질문은 (a) 길이 3+ 특정 토큰이 걸리거나 (b) 서로 다른 토큰 3개+가
#     걸리거나 (c) 제목급 매칭(score>=title 가중치)이 난다.
# ponytail: 완벽한 의미 분류가 아니라 결정론 방어층이다 — 임계는 팩 교체 시
# 재튜닝 대상.
_TITLE_WEIGHT = 100
# 구조 팩의 필드 밴드(keywords·tags·topic) 중간 가중. 제목(100x)과 본문(1x) 사이.
# flat 팩은 이 필드가 비어 밴드 기여 0 -> 기존 검색 동작과 동일(하위호환).
_FIELD_WEIGHT = 10
_RELEVANCE_MIN_DISTINCT = 3
_RELEVANCE_SPECIFIC_LEN = 3
_RELEVANCE_STOPWORDS = frozenset(
    {"어떻게", "무엇을", "무엇이", "그리고", "하지만", "그러나", "때문에", "위하여", "위해서"}
)


def _relevance_floor_met(matched: set, top_score: int) -> bool:
    if top_score >= _TITLE_WEIGHT:
        return True
    if any(
        len(token) >= _RELEVANCE_SPECIFIC_LEN and token not in _RELEVANCE_STOPWORDS
        for token in matched
    ):
        return True
    return sum(1 for token in matched if len(token) >= 2) >= _RELEVANCE_MIN_DISTINCT


def _field_text(doc: "Document") -> str:
    """검색 중간 밴드(10x) 대상: keywords + tags + topic 을 한 문자열로 잇는다.

    tags 는 Document.tags(프론트매터 tags), keywords·topic 은 meta 에서 온다
    (load_documents 가 스키마 밖 키를 meta 에 보존). 구조 팩에만 존재하며
    flat 팩에선 비어 밴드 기여가 0 이다.
    """
    parts: list = list(doc.tags)
    keywords = doc.meta.get("keywords")
    if isinstance(keywords, list):
        parts.extend(keywords)
    elif keywords:
        parts.append(keywords)
    topic = doc.meta.get("topic")
    if topic:
        parts.append(topic)
    return " ".join(str(p) for p in parts)


def score_document(query_words: list, doc: "Document") -> tuple:
    """(score, matched_tokens) — 필드 구조를 살린 결정론 키워드 스코어러.

    밴드: 제목 100x + (keywords·tags·topic) 10x + 본문 1x. 토큰 경계로 센다.

    임베딩 seam: 이 함수 하나가 검색 점수의 교체 지점이다. 임베딩 백엔드로 바꾸려면
    query_words(원 질의)를 임베딩해 doc 와의 유사도를 score 로 돌려주도록 이 함수만
    교체하면 된다(search 는 _SCORER 상수를 통해서만 점수를 얻는다). matched_tokens 는
    relevance floor(F1) 판정에 쓰이므로 임베딩 교체 시에도 '겹친 질의 토큰 집합' 의미를
    유지해야 게이트 우회 방어가 산다. 현재 구현은 키워드 밴드다(RAG/벡터DB 미사용).
    """
    title_counts = Counter(_tokenize(doc.title))
    field_counts = Counter(_tokenize(_field_text(doc)))
    body_counts = Counter(_tokenize(doc.body))
    score = 0
    matched: set = set()
    for word in query_words:
        t = title_counts[word]
        f = field_counts[word]
        b = body_counts[word]
        score += _TITLE_WEIGHT * t + _FIELD_WEIGHT * f + b
        if t or f or b:
            matched.add(word)
    return score, matched


# 검색 점수 계산의 교체 지점(임베딩 seam). search 는 이 상수만 호출한다.
_SCORER = score_document


def search(query: str, documents: list, top_n: int = 3, *, topic: str | None = None) -> list:
    """질문어가 제목/필드/본문에 '토큰 단위로' 등장하는 빈도로 상위 top_n 문서를 반환한다.

    부분문자열이 아니라 토큰 경계로 센다. 1글자 토큰('법')이 무관한 문서의 긴
    단어('방법') 안에 박혀 무관한 질문이 근거를 얻는 오매칭을 막기 위함이다. 이
    오매칭이 막혀야 검색 0건이 유지되고, 코칭 팩의 고정 거부 게이트(phase-06)가
    우회되지 않는다. 조사 정규화는 질의·문서 양쪽에 같은 방식으로 적용한다.

    점수는 _SCORER(score_document)가 낸다 — 제목 100x + keywords·tags·topic 10x +
    본문 1x 의 필드 가중 밴드다. 임베딩으로 교체하려면 _SCORER 만 바꾸면 된다.

    topic(keyword-only, 기본 None): 지정 시 그 primary topic 문서로 스코프를 축소한다
    (탑다운 브라우징 seam). 채팅 흐름은 topic=None 기본이라 무해하다.

    관련성 바닥(_relevance_floor_met, F1): 토큰이 겹치더라도 관련성이 바닥
    미만이면 0건으로 되돌린다 — 흔한 토큰 하나로 게이트를 우회하는 것을 막는다.

    범위: 1건 이상일 때의 랭킹 품질(무관 문서가 상위에 잔존하는 문제)은 닫지
    않았다 — 사용자 결정으로 범위 밖이다.
    """
    words = _tokenize(query)
    if not words:
        return []

    if topic is not None:
        documents = [doc for doc in documents if doc.meta.get("topic") == topic]

    scored = []
    matched_tokens: set = set()
    for doc in documents:
        score, matched = _SCORER(words, doc)
        if score > 0:
            scored.append((score, doc))
            matched_tokens.update(matched)

    if not scored:
        return []
    if not _relevance_floor_met(matched_tokens, max(score for score, _ in scored)):
        return []

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [doc for _, doc in scored[:top_n]]
