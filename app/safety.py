"""Prompt-injection guardrails for the demo chatbot.

This module is deliberately deterministic. The LLM can make the wording warmer,
but trust-boundary decisions must not depend on the same model being attacked.
"""

from __future__ import annotations

import base64
import binascii
import codecs
import re
import unicodedata
import urllib.parse
from dataclasses import dataclass

_ZERO_WIDTH_AND_BIDI_RE = re.compile(
    "[\u200b\u200c\u200d\u2060\ufeff\u202a-\u202e\u2066-\u2069]"
)
_WHITESPACE_RE = re.compile(r"\s+")
_BASE64_RE = re.compile(r"\b[A-Za-z0-9+/]{16,}={0,2}\b")
_HEX_RE = re.compile(r"\b(?:0x)?[0-9a-fA-F]{24,}\b")
_BASE32_RE = re.compile(r"[A-Z2-7]{16,}={0,6}")
_URL_ENCODED_RE = re.compile(r"%[0-9A-Fa-f]{2}")
_WORD_RE = re.compile(r"[a-zA-Z]{4,}")

_ATTACK_PATTERNS: dict[str, tuple[str, ...]] = {
    "instruction_override": (
        r"\b(ignore|disregard|forget|bypass|override)\b.{0,80}\b(previous|prior|above|system|developer|instruction|rules?)\b",
        r"\b(system|developer)\s+override\b",
        r"\bdo\s+not\s+follow\b.{0,40}\b(system|developer|previous|above)\b",
        r"이전\s*(지시|규칙|명령).{0,30}(무시|잊어|삭제|따르지|버려)",
        r"앞선\s*(지시|규칙|명령).{0,30}(무시|잊어|삭제|따르지|버려)",
        r"위\s*(지시|규칙|명령).{0,30}(무시|잊어|삭제|따르지|버려)",
        r"이전\s*(내용|대화|문맥).{0,30}(무시|잊어|잊고|삭제|따르지|버려)",
    ),
    "role_hijack": (
        r"\byou\s+are\s+now\b",
        r"\bdeveloper\s+mode\b",
        r"\bDAN\b",
        r"\bjailbreak\b",
        r"너는\s*이제",
        r"역할을\s*(바꿔|변경)",
        r"상담사(가|로)?\s*아니",
        # F3 -- 어순 뒤바뀐 한/영 탈취.
        r"이제부터\s*너는",
        r"제약\s*없(는|이)\s*(도우미|어시스턴트|assistant|ai|봇|모델)",
        r"\bfrom\s+now\s+on\b.{0,40}\byou\s+are\b",
        r"\bunrestricted\b.{0,20}\b(assistant|ai|model|bot|chatbot)\b",
        r"역할.{0,10}하도록\s*설정",
    ),
    "prompt_leak": (
        r"\bsystem\s*prompt\b",
        r"\bdeveloper\s*(message|instruction|prompt)\b",
        r"\bhidden\s*prompt\b",
        r"\breveal\b.{0,40}\b(prompt|instruction|system)\b",
        r"\bprint\b.{0,40}\b(prompt|instruction|system)\b",
        r"\brepeat\b.{0,50}\b(text|instructions?)\b.{0,30}\babove\b",
        r"시스템\s*프롬프트",
        r"개발자\s*(메시지|지시|프롬프트)",
        r"숨겨진\s*(프롬프트|지시)",
        r"내부\s*(프롬프트|지시|규칙)",
        r"프롬프트.{0,20}(보여|출력|공개|말해|읽어)",
        r"(운영|내부|숨겨진)\s*(규칙|지침|정책).{0,40}(전문|원문|그대로|출력|말해|보여|반복)",
        r"개발자\s*(메시지|지시|프롬프트).{0,40}(원문|그대로|반복|출력|공개)",
        # F3 -- 키워드 없는 페르소나/지침 추출.
        r"(처음|최초|첫|맨\s*처음).{0,15}(받은|주어진|들은).{0,15}(안내문|안내|지시|지침|프롬프트|규칙|설정).{0,20}(그대로|원문|전문|알려|보여|출력|공개|말해|설명)",
        r"(받은|주어진|들은).{0,10}(첫|처음|최초).{0,10}(지시|지침|안내|프롬프트|규칙|설정).{0,20}(원문|그대로|전문|보여|출력|알려|공개|설명)",
        r"설정\s*(지침|규칙|프롬프트|지시).{0,15}(설명|알려|보여|출력|공개|말해)",
    ),
    "data_exfiltration": (
        r"\b(api[_\s-]?key|secret|token|password)\b",
        r"\.env\b",
        r"\b(sqlite|database|db\s*schema|schema)\b.{0,40}\b(show|dump|print|reveal)\b",
        r"환경\s*변수",
        r"api\s*키",
        r"비밀\s*키",
        r"토큰",
        r"비밀번호",
        r"대화\s*기록.{0,20}(보여|출력|공개|덤프)",
        r"(db|데이터베이스|sqlite).{0,20}(구조|덤프|전체|보여|출력)",
    ),
    "markup_escape": (
        r"</\s*message\s*>",
        r"<\s*message\s+role\s*=\s*['\"]?\s*system",
        r"<\s*(script|iframe|img)\b",
        r"!\[[^\]]*\]\(\s*https?://",
        r"<!--.{0,120}\b(ignore|system|assistant|developer|instruction)\b",
        r"display\s*:\s*none",
        r"color\s*:\s*white",
    ),
}

_FUZZY_TARGETS = {
    "ignore",
    "bypass",
    "override",
    "reveal",
    "delete",
    "system",
    "developer",
    "prompt",
    "instruction",
    "instructions",
    "previous",
}

_OUTPUT_LEAK_PATTERNS = (
    r"\[시스템 지시\]",
    r"SYSTEM_INSTRUCTIONS",
    r"USER_DATA_TO_PROCESS",
    r"\bsystem\s*prompt\b",
    r"\bdeveloper\s*(message|instruction|prompt)\b",
    r"ANTHROPIC_API_KEY",
    r"OPENAI_API_KEY",
    r"CODEX_MODEL",
    r"_safety_protocol\.md",
    r"_persona\.md",
    r"_tone\.md",
    r"<\s*(script|iframe)\b",
)
_REMOTE_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*https?://[^)]+\)", re.IGNORECASE)
_RAW_ACTIVE_HTML_RE = re.compile(r"<\s*(script|iframe|img)\b[^>]*>", re.IGNORECASE)


@dataclass(frozen=True)
class SafetyAssessment:
    blocked: bool
    categories: tuple[str, ...]
    decoded_fragments: tuple[str, ...] = ()


def normalize_text(text: str) -> str:
    """Normalize obvious obfuscation before detection."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = _ZERO_WIDTH_AND_BIDI_RE.sub("", normalized)
    normalized = _WHITESPACE_RE.sub(" ", normalized)
    # Long repeated characters are a cheap bypass for exact filters: byyyyypass.
    normalized = re.sub(r"(.)\1{3,}", r"\1\1", normalized)
    return normalized.strip()


def _looks_printable(decoded: bytes) -> bool:
    if not decoded:
        return False
    printable = sum(1 for b in decoded if b in (9, 10, 13) or 32 <= b <= 126 or b >= 128)
    return printable / len(decoded) >= 0.85


def _decode_fragments(text: str) -> tuple[str, ...]:
    """Decode short suspicious Base64/hex payloads for detection only."""
    fragments: list[str] = []
    for match in _BASE64_RE.findall(text):
        padded = match + ("=" * ((4 - len(match) % 4) % 4))
        try:
            decoded = base64.b64decode(padded, validate=True)
        except (binascii.Error, ValueError):
            continue
        if _looks_printable(decoded):
            try:
                fragments.append(decoded.decode("utf-8", errors="ignore"))
            except UnicodeDecodeError:
                continue

    for match in _HEX_RE.findall(text):
        raw = match[2:] if match.startswith("0x") else match
        if len(raw) % 2:
            continue
        try:
            decoded = bytes.fromhex(raw)
        except ValueError:
            continue
        if _looks_printable(decoded):
            fragments.append(decoded.decode("utf-8", errors="ignore"))

    for match in _BASE32_RE.findall(text):
        raw = match.rstrip("=")
        padded = raw + "=" * ((8 - len(raw) % 8) % 8)
        try:
            decoded = base64.b32decode(padded)
        except (binascii.Error, ValueError):
            continue
        if _looks_printable(decoded):
            fragments.append(decoded.decode("utf-8", errors="ignore"))
    return tuple(dict.fromkeys(fragments))


def _alternate_encodings(text: str) -> tuple[str, ...]:
    """전체 문자열 단위 인코딩을 되돌린 후보(탐지 전용, F3).

    rot13은 자기역함수라 평문에 적용하면 무해한 gibberish가 되고 암호문에
    적용하면 평문이 된다. 역순·URL-인코딩도 마찬가지로 정상 발화에 적용하면
    공격 패턴과 겹치지 않는다(오차단 위험 낮음).
    """
    alternates = [codecs.encode(text, "rot_13"), text[::-1]]
    if _URL_ENCODED_RE.search(text):
        try:
            alternates.append(urllib.parse.unquote(text))
        except (ValueError, UnicodeDecodeError):
            pass
    return tuple(alternates)


def _is_typoglycemia_variant(word: str, target: str) -> bool:
    if word == target:
        return True
    if len(word) != len(target) or len(word) < 5:
        return False
    return word[0] == target[0] and word[-1] == target[-1] and sorted(word[1:-1]) == sorted(
        target[1:-1]
    )


def _typoglycemia_categories(text: str) -> set[str]:
    matched: set[str] = set()
    for word in _WORD_RE.findall(text):
        token = word.casefold().rstrip("s")
        for target in _FUZZY_TARGETS:
            if _is_typoglycemia_variant(token, target.rstrip("s")):
                matched.add(target.rstrip("s"))

    categories: set[str] = set()
    if "ignore" in matched and ({"previous", "system", "instruction", "developer"} & matched):
        categories.add("typoglycemia_instruction_override")
    if "reveal" in matched and ({"prompt", "system", "instruction"} & matched):
        categories.add("typoglycemia_prompt_leak")
    if "bypass" in matched and ({"system", "instruction", "developer"} & matched):
        categories.add("typoglycemia_instruction_override")
    return categories


def assess_prompt_injection(text: str) -> SafetyAssessment:
    """Return a deterministic assessment of likely prompt-injection intent."""
    normalized = normalize_text(text)
    decoded = _decode_fragments(text)
    alternates = _alternate_encodings(text)
    candidates = (
        normalized,
        *(normalize_text(fragment) for fragment in decoded),
        *(normalize_text(alternate) for alternate in alternates),
    )

    categories: set[str] = set()
    for candidate in candidates:
        for category, patterns in _ATTACK_PATTERNS.items():
            if any(re.search(pattern, candidate, re.IGNORECASE | re.DOTALL) for pattern in patterns):
                categories.add(category)
        categories.update(_typoglycemia_categories(candidate))

    return SafetyAssessment(
        blocked=bool(categories),
        categories=tuple(sorted(categories)),
        decoded_fragments=decoded,
    )


def sanitize_model_reply(reply: str, fallback: str) -> str:
    """Block leaked privileged text and remove active remote-rendering payloads."""
    if any(re.search(pattern, reply, re.IGNORECASE | re.DOTALL) for pattern in _OUTPUT_LEAK_PATTERNS):
        return fallback
    sanitized = _REMOTE_MARKDOWN_IMAGE_RE.sub("[원격 이미지 링크 생략]", reply)
    sanitized = _RAW_ACTIVE_HTML_RE.sub("[HTML 링크 생략]", sanitized)
    return sanitized
