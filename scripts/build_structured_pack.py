#!/usr/bin/env python3
"""구조화 지식 팩 빌더 — flat 팩 입력 -> 주제 계층 + 논문별 인덱스 카드 (결정론 v1).

flat 팩(`data/knowledge-srl-pack`)과 메타 CSV 원본(`../knowledge-source`)은 오직 읽기만
한다. 빌드 전후 해시 매니페스트가 같은지 단언한다(불가역 리스크, load-bearing).

결정론 v1: 주제 분류=키워드 규칙(상단 TOPIC_KEYWORDS dict), keywords=본문 빈도 추출
(형태소기 금지 — 기존 정책, 정규식+불용어). 실제 임베딩·LLM 분류는 하지 않는다 —
검색 스코어러 교체 지점(seam)은 app/knowledge.py 의 _SCORER 다.

`_intake_schema.md` 는 만들지 않는다 -- 만들면 팩이 접수 모드가 되어 phase-06 의 0건
거부 게이트가 미적용된다(코칭 모드 불변식).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import shutil
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml

# --- 주제 분류 키워드 (튜닝 가능한 상단 상수) -----------------------------------
# primary = 최고 히트 주제, tags = 임계 이상 걸린 주제 전부. 동점이면 이 dict 순서 우선.
# (주의: 07 에 신경/EEG/전두엽/mPFC 어휘를 넣어 뇌과학 질의가 이 주제로 라우팅되게 함.)
TOPIC_KEYWORDS: dict[str, list[str]] = {
    "01_자기조절-자기주도-기초": [
        "self-regulat", "self-direct", "srl", "sdl", "자기조절", "자기주도",
        "framework", "measurement", "scale", "개념", "competenc",
    ],
    "02_메타인지": [
        "metacognit", "메타인지", "monitoring", "reflection", "성찰", "self-monitor",
    ],
    "03_동기-정서-마인드셋": [
        "motivation", "동기", "emotion", "정서", "mindset", "마인드셋", "resilience",
        "회복탄력", "self-efficacy", "자기효능", "well-being", "engagement", "몰입",
        "anxiety", "불안", "curiosity", "호기심",
    ],
    "04_피드백-교사-개입": [
        "feedback", "피드백", "teacher", "교사", "intervention", "개입", "training",
        "훈련", "scaffold", "스캐폴딩", "instruction", "tutor",
    ],
    "05_온라인-블렌디드-원격": [
        "online", "온라인", "blended", "블렌디드", "distance", "원격", "mooc",
        "flipped", "플립", "remote", "lms", "e-learning", "이러닝", "hybrid",
    ],
    "06_AI-에듀테크-학습분석": [
        "artificial intelligence", "ai", "chatbot", "챗봇", "gpt", "learning analytic",
        "학습분석", "trace data", "트레이스", "technology-enhanced", "에듀테크",
        "digital", "디지털", "machine learning", "multimodal data",
    ],
    "07_체화-감각-멀티모달": [
        "embodied", "체화", "sensory", "감각", "multisensory", "멀티모달",
        "virtual reality", "vr", "가상현실", "eye-track", "시선추적", "neuroplastic",
        "eeg", "뇌", "prefrontal", "전두엽",
    ],
    "08_학업성취-성과": [
        "academic achievement", "학업성취", "academic performance", "성과", "outcome",
        "gpa", "grade", "성적",
    ],
}
OTHER_TOPIC = "08_기타"
TAG_MIN_HITS = 2  # 이 히트 수 이상인 주제만 tags 에 (primary 는 히트와 무관하게 항상 포함)
TERM_SCAN_CHARS = 4000  # 분류 시 훑는 (제목+본문) 앞부분 길이
DOI_SCAN_CHARS = 3000

_DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
_HANGUL_RE = re.compile(r"[가-힣]")
_WORD_RE = re.compile(r"[0-9A-Za-z가-힣]+", re.UNICODE)
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")

# keywords 빈도 추출 불용어(정규식+불용어; 형태소기 금지). 학술 잡음어 포함.
_STOPWORDS = frozenset(
    {
        "the", "and", "of", "to", "in", "for", "on", "with", "as", "that", "this",
        "are", "was", "were", "which", "from", "by", "an", "at", "or", "be", "is",
        "it", "its", "their", "these", "those", "study", "studies", "research",
        "results", "result", "using", "used", "use", "based", "http", "https",
        "www", "com", "org", "doi", "pdf", "et", "al", "vol", "no", "pp", "isbn",
        "issn", "abstract", "keywords", "article", "journal", "however", "also",
        "such", "can", "may", "not", "has", "have", "had", "between", "into",
        "학습", "연구", "논문", "그리고", "하지만", "그러나", "때문에", "위하여",
        "위해서", "대한", "대해", "있는", "없는", "통해", "위한", "따라", "등의",
        "것으로", "것이", "있다", "한다", "된다", "이다",
    }
)


def _hash_manifest(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def norm_doi(doi: str) -> str:
    """소문자 + 후행 구두점/URL 아티팩트(/full,/pdf 등) 제거 — MASTER_INDEX 와 조인되게."""
    d = doi.lower().rstrip(").,;")
    for suffix in ("/full", "/pdf", "/abstract", "/html", "/meta", "/epdf"):
        if d.endswith(suffix):
            d = d[: -len(suffix)]
    return d


def extract_doi(head: str) -> str | None:
    """본문 앞부분에서 '이 논문의 DOI' 를 뽑는다.

    정확히 하나의 서로 다른 DOI 만 있을 때 그 DOI 를 반환한다. 0개면 None.
    여러 개면(리스트/집계 문서 — 첫머리에 다른 논문 DOI 나열) None 을 돌려 title 키로
    폴백시킨다. ponytail: 이 다중-DOI 가드가 없으면 50편 리스트 파일이 단일 논문과
    조용히 병합돼 정본이 뒤바뀐다(실측 96 파일). 상한: 첫 DOI 만 보는 순진한 방식
    대신 '한 논문=한 DOI' 신호를 쓴다 — 더 정교해지려면 섹션 파싱 필요(현재 불필요).
    """
    dois = {norm_doi(d) for d in _DOI_RE.findall(head[:DOI_SCAN_CHARS])}
    if len(dois) == 1:
        return next(iter(dois))
    return None


def norm_title(title: str) -> str:
    """정규화 제목 키: NFC 소문자 + 영숫자/한글만(공백·구두점 제거)."""
    t = unicodedata.normalize("NFC", title).lower()
    return re.sub(r"[^0-9a-z가-힣]", "", t)


def _count_hits(text: str, keyword: str) -> int:
    """text(소문자) 안 keyword 히트 수. 한글=부분문자열, 짧은 ASCII(<=3)=완전단어,
    긴 ASCII/구=단어경계 접두(굴절 흡수). 'ai' 가 training/domain 안에 박혀 오분류되는
    부분문자열 함정을 막는다."""
    if _HANGUL_RE.search(keyword):
        return text.count(keyword)
    esc = re.escape(keyword)
    pattern = r"\b" + esc + (r"\b" if len(keyword) <= 3 else "")
    return len(re.findall(pattern, text))


def classify(title: str, body: str) -> tuple[str, list[str]]:
    """(primary, tags). 제목+본문 앞부분 소문자에서 주제별 키워드 히트 수 합산."""
    text = unicodedata.normalize("NFC", f"{title}\n{body}")[: TERM_SCAN_CHARS + len(title) + 1].lower()
    hits = {
        topic: sum(_count_hits(text, kw) for kw in kws)
        for topic, kws in TOPIC_KEYWORDS.items()
    }
    best = max(hits, key=lambda t: (hits[t], -list(TOPIC_KEYWORDS).index(t)))
    if hits[best] == 0:
        return OTHER_TOPIC, [OTHER_TOPIC]
    tags = [t for t in TOPIC_KEYWORDS if hits[t] >= TAG_MIN_HITS]
    if best not in tags:
        tags.insert(0, best)
    return best, tags


def extract_keywords(text: str, top: int = 8) -> list[str]:
    """본문 빈도 상위 명사류 top 개(간단 추출; 형태소기 금지). 3자+·불용어·순수숫자 제외."""
    counts: Counter[str] = Counter()
    for tok in _WORD_RE.findall(unicodedata.normalize("NFC", text).lower()):
        if len(tok) < 3 or tok in _STOPWORDS or tok.isdigit():
            continue
        counts[tok] += 1
    return [w for w, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top]]


def slugify(text: str) -> str:
    """파일시스템 안전 slug: NFC + 영숫자/한글만 남기고 나머지는 '_' 로, 길이 80 캡."""
    t = unicodedata.normalize("NFC", text).strip().lower()
    t = re.sub(r"[^0-9a-z가-힣]+", "_", t).strip("_")
    return (t or "doc")[:80]


def _split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    _, fm_raw, body = parts
    return (yaml.safe_load(fm_raw) or {}), body.lstrip("\n")


def load_master_index(csv_path: Path) -> dict[str, dict]:
    """MASTER_INDEX.csv -> {norm_doi: row}. utf-8-sig(BOM) 로 읽는다."""
    index: dict[str, dict] = {}
    with csv_path.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            doi = (row.get("doi") or "").strip()
            if doi:
                index[norm_doi(doi)] = row
    return index


def _year_from(stem: str) -> int | str:
    m = _YEAR_RE.search(stem)
    return int(m.group(0)) if m else ""


def _load_flat_docs(src: Path) -> list[dict]:
    """flat 팩의 각 지식 문서 -> {body, source, stem, h1, doi} 레코드."""
    records = []
    for md in sorted(src.rglob("*.md")):
        if md.name.startswith("_"):
            continue
        text = md.read_text(encoding="utf-8")
        meta, body = _split_frontmatter(text)
        source = meta.get("source") or md.relative_to(src).as_posix()
        stem = Path(str(source)).stem
        h1_match = re.search(r"^#\s+(.+?)\s*$", body, re.MULTILINE)
        records.append(
            {
                "body": body,
                "source": str(source),
                "stem": stem,
                "h1": h1_match.group(1).strip() if h1_match else stem,
                "doi": extract_doi(body),
                "len": len(body),
            }
        )
    return records


def _dedup(records: list[dict]) -> tuple[list[dict], list[str]]:
    """키(정규화 DOI 우선, 없으면 정규화 제목) 별로 본문 최장 1개를 정본, 나머지는 보고."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        key = rec["doi"] or ("title:" + norm_title(rec["h1"] or rec["stem"]))
        groups[key].append(rec)
    canonical: list[dict] = []
    dropped: list[str] = []
    for key, recs in groups.items():
        recs.sort(key=lambda r: (-r["len"], r["source"]))
        winner = recs[0]
        canonical.append(winner)
        for loser in recs[1:]:
            dropped.append(f"{winner['source']} <- {loser['source']} (key={key})")
    canonical.sort(key=lambda r: r["source"])
    return canonical, dropped


def _meta_summary(front: dict) -> str:
    bits = [str(front.get(k)) for k in ("year", "journal", "type") if front.get(k)]
    return " · ".join(bits)


def build_structured_pack(src: Path, dst: Path, master_csv: Path | None) -> dict:
    """flat 팩(src, 읽기전용) -> 구조 팩(dst): 메타 조인·dedup·8주제 분류·계층·백링크.

    master_csv 는 있으면 읽기만(DOI 조인). src·master_csv 는 절대 쓰지 않는다.
    """
    master = load_master_index(master_csv) if master_csv and master_csv.is_file() else {}

    records = _load_flat_docs(src)
    docs_before = len(records)
    canonical, dropped = _dedup(records)

    # 카드 메타 조립
    cards: list[dict] = []
    for rec in canonical:
        row = master.get(rec["doi"]) if rec["doi"] else None
        if row:
            title = (row.get("title") or "").strip() or rec["h1"]
            year: int | str = int(row["year"]) if (row.get("year") or "").strip().isdigit() else _year_from(rec["stem"])
            journal = (row.get("journal") or "").strip()
            paper_type = (row.get("type") or "").strip()
        else:
            title = rec["h1"] or rec["stem"]
            year = _year_from(rec["stem"])
            journal = ""
            paper_type = ""
        primary, tags = classify(title, rec["body"])
        cards.append(
            {
                "front": {
                    "title": title,
                    "year": year,
                    "journal": journal,
                    "doi": rec["doi"] or "",
                    "type": paper_type,
                    "topic": primary,
                    "tags": tags,
                    "keywords": extract_keywords(f"{title}\n{rec['body']}"),
                    "source": rec["source"],
                    "related": [],
                },
                "body": rec["body"],
            }
        )

    # slug 배정(주제 폴더 안에서 유일). related = 같은 주제 인접 2~3편.
    by_topic: dict[str, list[dict]] = defaultdict(list)
    for card in cards:
        by_topic[card["front"]["topic"]].append(card)

    for topic, group in by_topic.items():
        group.sort(key=lambda c: c["front"]["source"])
        used: set[str] = set()
        for card in group:
            base = slugify(card["front"]["title"])
            slug = base
            i = 2
            while slug in used:
                slug = f"{base}_{i}"
                i += 1
            used.add(slug)
            card["slug"] = slug
        n = len(group)
        for idx, card in enumerate(group):
            neighbors = [group[(idx + k) % n]["slug"] for k in range(1, min(4, n))]
            card["front"]["related"] = neighbors

    # 쓰기: 카드 + _topic.md + 00_INDEX.md
    dst.mkdir(parents=True, exist_ok=True)
    topic_dist: dict[str, int] = {}
    for topic in TOPIC_KEYWORDS.keys() | {OTHER_TOPIC}:
        group = by_topic.get(topic, [])
        topic_dist[topic] = len(group)
        if not group:
            continue
        topic_dir = dst / topic
        topic_dir.mkdir(parents=True, exist_ok=True)
        for card in group:
            front = card["front"]
            fm = yaml.safe_dump(front, allow_unicode=True, sort_keys=False).rstrip("\n")
            summary = _meta_summary(front)
            card_body = f"---\n{fm}\n---\n\n# {front['title']}\n\n"
            if summary:
                card_body += f"> {summary}\n\n"
            card_body += card["body"].rstrip("\n") + "\n"
            (topic_dir / f"{card['slug']}.md").write_text(card_body, encoding="utf-8")
        _write_topic_backlink(topic_dir, topic, group)

    _write_master_index(dst, by_topic)

    # persona 3종·_ui.json 복사(없을 때만; 코칭 모드 불변식으로 _intake_schema 는 안 만듦)
    for name in ("_persona.md", "_tone.md", "_safety_protocol.md", "_ui.json"):
        srcf = src / name
        if srcf.is_file() and not (dst / name).exists():
            (dst / name).write_text(srcf.read_text(encoding="utf-8"), encoding="utf-8")

    return {
        "docs_before": docs_before,
        "docs_after": len(canonical),
        "dropped_duplicates": dropped,
        "topic_distribution": topic_dist,
        "joined_master": sum(1 for c in cards if c["front"]["journal"] or c["front"]["type"]),
        "with_doi": sum(1 for c in cards if c["front"]["doi"]),
    }


def _write_topic_backlink(topic_dir: Path, topic: str, group: list[dict]) -> None:
    lines = [f"# {topic}", "", f"이 주제에 속한 논문 {len(group)}편(바텀업 백링크).", ""]
    for card in group:
        front = card["front"]
        summary = _meta_summary(front)
        tail = f" — {summary}" if summary else ""
        lines.append(f"- [{front['title']}]({card['slug']}.md){tail}")
    (topic_dir / "_topic.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_master_index(dst: Path, by_topic: dict[str, list[dict]]) -> None:
    # frontmatter+H1 을 둔다 -- validate_pack 이 비-예약 .md 전부에 이를 요구한다.
    lines = [
        "---",
        "title: 지식 마스터 인덱스",
        "topic: 00_index",
        "---",
        "",
        "# 지식 마스터 인덱스",
        "",
        "주제 -> 논문(탑다운 브라우징, 사람용 네비게이션 -- 검색 대상 아님). 바텀업은 각 주제 폴더의 `_topic.md`.",
        "",
    ]
    for topic in list(TOPIC_KEYWORDS.keys()) + [OTHER_TOPIC]:
        group = by_topic.get(topic, [])
        if not group:
            continue
        lines.append(f"## {topic} ({len(group)}편)")
        lines.append("")
        for card in group:
            front = card["front"]
            summary = _meta_summary(front)
            tail = f" — {summary}" if summary else ""
            lines.append(f"- [{front['title']}]({topic}/{card['slug']}.md){tail}")
        lines.append("")
    # 언더스코어 프리픽스 -- 마스터 인덱스는 사람이 탑다운 브라우징하는 네비게이션
    # 파일이지 검색 대상 지식이 아니다. load_documents 가 `_` 시작 파일을 검색에서
    # 제외하므로, 56KB 전체 제목 목록이 근거로 주입되는 노이즈/인젝션 표면을 막는다.
    (dst / "_00_INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _format_report(stats: dict) -> str:
    dist = "\n".join(f"    {t}: {n}" for t, n in sorted(stats["topic_distribution"].items()))
    return "\n".join(
        [
            f"dedup 전 문서 수: {stats['docs_before']}",
            f"dedup 후 문서 수: {stats['docs_after']}",
            f"중복 제거 수: {len(stats['dropped_duplicates'])}",
            f"DOI 보유(정본): {stats['with_doi']}",
            f"MASTER_INDEX 조인(정본): {stats['joined_master']}",
            "주제 분포:",
            dist,
        ]
    )


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="flat 지식 팩을 주제 계층 구조 팩으로 변환한다.")
    parser.add_argument("--src", default="data/knowledge-srl-pack", help="입력 flat 팩(읽기전용)")
    parser.add_argument("--dst", required=True, help="산출 구조 팩 폴더")
    parser.add_argument(
        "--master",
        default="../knowledge-source/srl-research/MASTER_INDEX.csv",
        help="메타 조인용 MASTER_INDEX.csv(읽기전용)",
    )
    parser.add_argument("--report", action="store_true", help="dedup 전후·주제 분포 출력")
    parser.add_argument("--force", action="store_true", help="기존 --dst 를 덮어쓴다")
    args = parser.parse_args(argv)

    src = Path(args.src).resolve()
    dst_arg = Path(args.dst)
    dst = dst_arg.resolve()
    master = Path(args.master).resolve() if args.master else None

    if src == dst:
        print(f"거부: --src 와 --dst 가 같습니다: {src}", file=sys.stderr)
        return 1
    if _is_inside(dst, src):
        print(f"거부: --dst 가 --src 안에 있습니다: {dst}", file=sys.stderr)
        return 1
    if not src.is_dir():
        print(f"거부: --src 가 없습니다: {src}", file=sys.stderr)
        return 1
    if dst_arg.exists() and not args.force:
        print(f"거부: 출력 팩이 이미 존재합니다(--force 없이 덮지 않음): {dst_arg}", file=sys.stderr)
        return 1
    if dst.exists() and args.force:
        # --force 는 깨끗한 재빌드다 -- 이전 산출물을 남기면 이름이 바뀐 파일
        # (예: 00_INDEX.md -> _00_INDEX.md)이 stale 하게 남아 검색·검증을 오염시킨다.
        # src/knowledge-source 밖(위 가드가 보장)이라 삭제 안전.
        shutil.rmtree(dst)

    # 원본 무변경 단언: flat 팩 + (있으면) knowledge-source 트리 해시.
    src_before = _hash_manifest(src)
    ks_root = master.parent.parent if master and master.is_file() else None
    ks_before = _hash_manifest(ks_root) if ks_root else {}

    stats = build_structured_pack(src, dst, master_csv=master)

    if _hash_manifest(src) != src_before:
        print("치명적: 빌드 중 flat 원본이 변경되었습니다", file=sys.stderr)
        return 1
    if ks_root and _hash_manifest(ks_root) != ks_before:
        print("치명적: 빌드 중 knowledge-source 원본이 변경되었습니다", file=sys.stderr)
        return 1

    if args.report:
        print(_format_report(stats))
        if stats["dropped_duplicates"]:
            print("중복 제거(정본 <- 버린 것):")
            for line in stats["dropped_duplicates"]:
                print(f"    {line}")
    print("원본 해시 불변: OK (flat + knowledge-source)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
