"""세션별 대화 상태 + 10턴 캡 + 지식 검색 + LLM 호출을 잇는 대화 루프.

세션 상태는 메모리 dict에 둔다. 재시작 시 소실은 프로토타입 수용 범위 —
대화 내역 영속은 Phase 3의 JSON 저장이 맡는다.
"""

import json
import re
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from app import addiction, intake, knowledge, llm, prompting, safety, storage
from app.config import Settings

MAX_TURNS = 10
LIMIT_MESSAGE = f"이 세션은 대화 {MAX_TURNS}턴 한도에 도달했습니다. 새 세션으로 다시 시작해 주세요."

# 면담 종료(MAX_TURNS 도달) 시 히스토리를 넘겨 접수 요약을 생성하는 지시.
_SUMMARY_INSTRUCTION = (
    "지금까지의 면담 대화를 접수 요약으로 정리하라. 방문 이유, 주 호소, 위기 신호, "
    "다음 단계 네 항목을 포함하라."
)

# 실모드 단일 호출 통합(D02) — 응답 생성 호출 안에 슬롯 추출 지시를 함께
# 넣는다. 모델 출력은 intake.extract_real이 신뢰 경계 검증 후 분리한다.
_CRISIS_ESCALATION_INSTRUCTION = (
    "안전 규칙(다른 모든 규칙에 우선): 내담자 발화에서 자살·자해 위험이 읽히면 "
    "신호어 사전에 없는 완곡한 표현이라도 slots에 {\"track\": \"위기\"}를 반드시 포함하라. "
    "이미 다른 트랙이 채워져 있어도 위기로 올려라. 위험이 읽히지 않으면 track을 건드리지 마라. "
    "신호어 사전은 놓치는 표현이 있다 — 판단은 사전이 아니라 발화의 의미로 하라."
)
_CRISIS_HOTLINE_NOTICE = (
    " 지금 혼자 버티기 어렵거나 스스로를 해칠까 걱정된다면 "
    "자살예방상담전화 109 또는 생명의전화 1588-9191에 바로 연락해 주세요."
)

_EXTRACTION_INSTRUCTION = (
    "응답을 마친 뒤 마지막 줄에 ```slots 로 시작하는 fenced 코드블록을 추가하고, "
    "그 안에 이번 발화에서 새로 확인된 슬롯만 담은 JSON 객체를 출력하라. "
    '형식: ```slots\n{"슬롯id": "값"}\n```. 새로 확인된 슬롯이 없으면 빈 객체 '
    "{}를 출력하라."
)


@dataclass
class ChatSession:
    session_id: str
    turns: int = 0
    history: list[dict[str, str]] = field(default_factory=list)
    slots: dict[str, str] = field(default_factory=dict)
    last_question_slot_id: str | None = None
    participant_id: str | None = None


# F6 -- 세션 상태는 메모리에 쌓이므로 상한을 두고 LRU로 evict한다. 각 세션은
# 10턴 캡이라 오래된 세션은 이미 종료됐을 가능성이 높다. 상한을 넘으면 가장
# 오래 접근되지 않은 세션을 버린다(프로토타입 수용 범위 -- 모듈 docstring 참조).
# ponytail: 단일 프로세스 in-memory LRU. 멀티 워커/영속이 필요하면 외부 스토어로.
MAX_SESSIONS = 10_000
_sessions: "OrderedDict[str, ChatSession]" = OrderedDict()


def _get_session(session_id: str) -> ChatSession:
    session = _sessions.get(session_id)
    if session is None:
        session = ChatSession(session_id=session_id)
        _sessions[session_id] = session
        if len(_sessions) > MAX_SESSIONS:
            _sessions.popitem(last=False)  # 가장 오래된 세션 evict
    else:
        _sessions.move_to_end(session_id)  # 최근 접근으로 갱신
    return session


_load_persona = prompting.load_persona
_build_doc_section = prompting.build_doc_section
_SYSTEM_PREAMBLE = prompting.SYSTEM_PREAMBLE

_NO_GROUNDING_REPLY = (
    "죄송하지만 연결된 지식 문서에서 관련 내용을 찾지 못했습니다. "
    "문서에 있는 주제로 다시 질문해 주시겠어요?"
)

# CAP06 인용 대조용 -- "근거: <path>" 줄에서 문서 식별자(팩 루트 상대경로,
# phase-05 통일)만 뽑는다. 확장자(.md)로 좁혀서 fake 모드의 title 기반 표기
# ("[fake] 위키 근거: <title>")는 대상에서 자연히 빠진다(title엔 .md가 없다) --
# 그래서 fake 모드를 따로 분기할 필요가 없다.
_CITATION_LINE_RE = re.compile(r"근거\s*[:：]\s*(.+)")
_CITED_PATH_RE = re.compile(r"\S*\.md\b")


def _cited_doc_paths(reply: str) -> set[str]:
    """응답의 "근거: <path>" 줄에서 인용 식별자를 파싱한다.

    일부러 관대하게 판다 -- 문장부호·공백 차이로 정상 인용까지 걸러내지
    않기 위해서다(집합 밖 식별자만 잡으면 충분하다).
    """
    cited: set[str] = set()
    for line_match in _CITATION_LINE_RE.finditer(reply):
        for segment in line_match.group(1).split(","):
            path_match = _CITED_PATH_RE.search(segment)
            if path_match:
                cited.add(path_match.group(0))
    return cited


def is_grounded_mode(knowledge_dir) -> bool:
    """코칭(순수 근거) 모드인지 -- `_intake_schema.md` **파일 존재**로만 판정한다.

    `intake.load_schema(...)`의 파싱 결과(None)를 모드 판정에 쓰지 않는다:
    파일이 있는데 YAML이 깨진 경우도 접수 팩으로 취급해야 한다 -- 형식
    오류가 그 팩을 조용히 코칭 모드로 넘겨 거부 게이트 대상으로 만들면
    안 된다(GM3, E28 증폭 차단). 이 함수가 유일한 판정처이며 여기(거부
    게이트)·`app/main.py`(`/api/config`)·`app/knowledge_pack.py`(pack
    exercise)가 모두 이 함수를 쓴다.
    """
    return not (Path(knowledge_dir) / "_intake_schema.md").is_file()


def _slot_desc(slot: intake.Slot) -> str:
    """`id(label)` + 닫힌 값 집합. 허용값을 안 알려주면 모델이 자유 문자열을 지어내고,
    extract_real이 그걸 폐기해 해당 슬롯이 영영 안 채워진다."""
    desc = f"{slot.id}({slot.label})"
    if slot.values:
        desc += f"[허용값: {'|'.join(slot.values)}]"
    return desc


def _build_slot_section(
    schema: intake.Schema,
    filled: dict[str, str],
    unfilled: list[intake.Slot],
    turns_before: int,
) -> str:
    """모드 공통 슬롯 섹션 — 채워진/미충족(우선순위순)/레드플래그 규칙/턴 예산.

    슬롯을 `id(label)` 형태로 노출한다. 실모드 추출 지시(_EXTRACTION_INSTRUCTION)가
    `{"슬롯id": "값"}`을 요구하는데 라벨만 주면 모델이 id를 몰라 빈 객체를 뱉는다.
    """
    filled_text = ", ".join(
        f"{slot.id}({slot.label})={filled[slot.id]}" for slot in schema.slots if slot.id in filled
    )
    unfilled_text = ", ".join(_slot_desc(slot) for slot in unfilled)
    lines = [
        "[접수 슬롯 상태 — 내부 운영용, 사용자에게 노출 금지]",
        f"채워진 슬롯: {filled_text or '없음'}",
        f"미충족 슬롯(우선순위순): {unfilled_text or '없음'}",
        "레드플래그 규칙: 미충족 목록 최상단 슬롯이 레드플래그면 그 슬롯을 최우선으로 질문하라.",
        f"턴 예산: 잔여 {MAX_TURNS - turns_before}턴 — 우선순위 높은 슬롯부터 소비하라.",
        "응답 규칙: 사용자의 방금 표현에서 감정·상황·강도를 읽어 구체적으로 1문장 반영하고, 한 번에 질문 하나만 한다.",
        "다음 질문은 슬롯 의도를 지키되 문장을 그대로 복붙하지 말고 사용자 표현에 맞춰 자연스럽게 바꾼다.",
        "금지: 내부 슬롯명·JSON·코드·로직 설명·문서 제목을 사용자에게 보이지 마라. 진단·치료 조언도 하지 마라.",
    ]
    next_slot = unfilled[0] if unfilled else None
    if next_slot is not None:
        lines.append(f"다음 질문 슬롯: {_slot_desc(next_slot)}")
        if next_slot.ask:
            lines.append(f"다음 질문 의도: {next_slot.ask}")
    if turns_before == 0:
        lines.append(
            f"첫 안내문(인사·비밀보장·첫 개방형 질문: {schema.opening_question})은 이미 화면에 표시됐다. "
            "반복하지 말고 현재 사용자 발화를 짧게 반영한 뒤 다음 질문만 한다."
        )
    return "\n".join(lines)


def _fake_progress_suffix(
    schema: intake.Schema,
    new_fills: dict[str, str],
    unfilled: list[intake.Slot],
) -> str:
    """fake 모드 reply에 붙일 진행 접미사. 새 채움/다음 질문이 없으면 빈 문자열."""
    label_by_id = {slot.id: slot.label for slot in schema.slots}
    parts = []
    if new_fills:
        fills_text = ", ".join(f"{label_by_id[sid]}={value}" for sid, value in new_fills.items())
        parts.append(f"채움: {fills_text}")
    if unfilled:
        parts.append(f"다음 질문: {unfilled[0].label}")
    return f" | {' | '.join(parts)}" if parts else ""


def _question_for_slot(slot: intake.Slot) -> str:
    """스키마의 ask 문장을 우선 사용하고 없으면 라벨 기반 질문으로 폴백한다."""
    if slot.ask:
        return slot.ask
    return f"{slot.label}에 대해 조금 더 말씀해 주세요."


def _build_fake_intake_reply(
    schema: intake.Schema,
    new_fills: dict[str, str],
    unfilled: list[intake.Slot],
    turns_before: int,
) -> str:
    """API 키 없는 데모에서도 접수면담처럼 보이는 결정론 응답을 만든다."""
    next_slot = unfilled[0] if unfilled else None
    # 첫 안내문도 스키마 데이터(ui.intro)가 소유한다. 없으면 기존 상담 문구 유지.
    intro = schema.ui.get("intro") or (
        "안녕하세요. 이 대화는 첫 상담 전 접수면담입니다. 내용은 기본적으로 비밀로 "
        "다루지만, 자신이나 타인에게 즉각적인 위험이 있거나 학대·법적 요청이 있는 "
        "경우에는 안전을 위해 공유될 수 있습니다."
    )

    if next_slot is None:
        return (
            "필요한 접수 항목은 대부분 확인했습니다. 남은 시간에는 상담에서 꼭 "
            "다루고 싶은 점이나 빠뜨린 내용을 말씀해 주세요."
        )

    question = _question_for_slot(next_slot)
    if turns_before == 0 and not new_fills:
        return f"{intro} {schema.opening_question}"

    if next_slot.red_flag:
        return (
            "말씀해 주셔서 감사합니다. 안전 확인을 먼저 하겠습니다. "
            f"{question} 지금 혼자 버티기 어렵거나 스스로를 해칠까 걱정된다면 "
            "자살예방상담전화 109 또는 생명의전화 1588-9191에 바로 연락해 주세요."
        )

    if new_fills:
        return f"말씀해 주셔서 감사합니다. {question}"

    return f"조금 더 정확히 이해하기 위해 한 가지만 여쭤볼게요. {question}"

def _build_guardrail_reply(
    schema: intake.Schema | None,
    unfilled: list[intake.Slot],
) -> str:
    """프롬프트 인젝션·내부정보 요구를 접수면담으로 되돌리는 고정 안전 응답."""
    if schema is None:
        return (
            "그 요청은 여기서 다루지 않고, 현재 지식 문서에 관한 질문으로만 답하겠습니다. "
            "궁금한 내용을 한 가지로 다시 말씀해 주세요."
        )
    next_question = (
        _question_for_slot(unfilled[0])
        if unfilled
        else "상담에서 빠뜨리지 말아야 할 내용이 있다면 말씀해 주세요."
    )
    return (
        "그 요청은 여기서 다루지 않고, 지금은 첫 상담 전 접수에 필요한 내용으로 좁혀볼게요. "
        f"{next_question}"
    )


def _ensure_crisis_hotline(reply: str) -> str:
    if "109" in reply and "1588-9191" in reply:
        return reply
    return reply.rstrip() + _CRISIS_HOTLINE_NOTICE


def _slot_by_id(schema: intake.Schema, slot_id: str | None) -> intake.Slot | None:
    if slot_id is None:
        return None
    return next((slot for slot in schema.slots if slot.id == slot_id), None)


def _answer_to_previous_question(
    message: str,
    schema: intake.Schema,
    filled: dict[str, str],
    slot_id: str | None,
) -> dict[str, str]:
    """If keyword matching missed a direct answer, accept it for the slot just asked.

    Fake mode is deterministic and keyword-based. Real users answer questions with
    short phrases like "쉬었어요" or "두세 달 된 것 같아요" that may not contain
    the schema's signal words. When the previous assistant turn asked a concrete
    free-text slot, this fallback records the user's reply instead of looping the
    same question forever.
    """
    slot = _slot_by_id(schema, slot_id)
    if slot is None or slot.id in filled or not slot.is_active(filled):
        return {}
    if slot.capture != "full_message":
        return {}
    value = " ".join(message.split())[: intake._MAX_SLOT_VALUE_LEN]
    if not value:
        return {}
    return {slot.id: value}



def _intake_state(
    schema: intake.Schema,
    filled: dict[str, str],
    unfilled: list[intake.Slot],
) -> dict:
    """GUI 슬롯 패널용 상태. 스키마 활성 응답에만 additive로 실린다 —
    기존 {reply, turn, limit_reached} 계약은 그대로, 스키마 없는 지식셋
    (knowledge-alt 스왑)은 이 키 자체가 없다."""
    return {
        "filled": [
            {"id": slot.id, "label": slot.label, "value": filled[slot.id]}
            for slot in schema.slots
            if slot.id in filled
        ],
        "unfilled": [
            {"id": slot.id, "label": slot.label, "red_flag": slot.red_flag}
            for slot in unfilled
        ],
    }


_ADDICTION_SEVERITY_RANK = {"평가 필요": 0, "고위험": 1, "응급": 2}


def _handle_addiction_route(
    session: ChatSession,
    message: str,
    assessment: addiction.AddictionAssessment,
    schema: intake.Schema,
    participant_id: str,
) -> dict:
    """일반 상담 대신 결정론적 전문기관 안내를 저장하고 반환한다."""
    followup = session.slots.get("track") == "중독"
    previous_severity = session.slots.get("addiction_severity")
    severity = assessment.severity
    if previous_severity and _ADDICTION_SEVERITY_RANK.get(previous_severity, -1) > (
        _ADDICTION_SEVERITY_RANK.get(severity, -1)
    ):
        severity = previous_severity

    normalized_message = " ".join(message.split())[: intake._MAX_SLOT_VALUE_LEN]
    session.slots.update(
        {
            "track": "중독",
            "addiction_type": assessment.kind,
            "addiction_severity": severity,
            "addiction_referral": "전문기관 정보 제공",
        }
    )
    session.slots.setdefault("chief_complaint", normalized_message)

    routed_assessment = addiction.AddictionAssessment(
        kind=session.slots["addiction_type"],
        severity=session.slots["addiction_severity"],
    )
    reply = addiction.build_reply(routed_assessment, followup=followup)
    storage.append_turn(session.session_id, "user", message, participant_id=participant_id)
    storage.append_turn(session.session_id, "assistant", reply, participant_id=participant_id)
    session.history.append({"role": "user", "content": message})
    session.history.append({"role": "assistant", "content": reply})
    session.turns += 1
    session.last_question_slot_id = None

    if session.turns >= MAX_TURNS:
        summary_json = intake.build_summary_json(schema, session.slots)
        storage.append_turn(
            session.session_id,
            "intake_summary",
            json.dumps(summary_json, ensure_ascii=False),
            participant_id=participant_id,
        )

    unfilled = schema.unfilled_by_priority(session.slots, ())
    return {
        "reply": reply,
        "turn": session.turns,
        "limit_reached": False,
        "intake": _intake_state(schema, session.slots, unfilled),
    }


def handle_message(
    session_id: str,
    message: str,
    settings: Settings | None = None,
    participant_id: str | None = None,
) -> dict:
    settings = settings or Settings.from_env()
    session = _get_session(session_id)
    if participant_id and session.participant_id is None:
        session.participant_id = participant_id
    effective_participant_id = session.participant_id or session_id

    if session.turns >= MAX_TURNS:
        return {"reply": LIMIT_MESSAGE, "turn": session.turns, "limit_reached": True}

    # ponytail: 매 요청마다 지식 디렉토리를 다시 읽는다. 소규모 위키 전제라
    # 캐시 없이도 충분하다 — 문서 수가 늘어 느려지면 그때 캐시를 붙인다.
    docs = knowledge.search(message, knowledge.load_documents(settings.knowledge_dir))
    persona = _load_persona(settings.knowledge_dir)
    progress = f"[진행: {session.turns + 1}/{MAX_TURNS}턴]"
    doc_section = _build_doc_section(docs)

    schema = intake.load_schema(settings.knowledge_dir)
    new_fills: dict[str, str] = {}
    unfilled: list[intake.Slot] = []
    red_flag_ids: set[str] = set()
    safety_assessment = safety.assess_prompt_injection(message)
    if safety_assessment.blocked:
        # 인젝션성 발화는 LLM에 넘기지 않는다. 단, 자해·자살 신호가 함께 있으면
        # 접수 흐름보다 안전 확인을 우선한다.
        if schema is None:
            reply = _build_guardrail_reply(None, [])
        else:
            crisis_fills = intake.extract_fake(message, schema, session.slots)
            if crisis_fills.get("track") == "위기" or session.slots.get("track") == "위기":
                new_fills = crisis_fills
                session.slots.update(new_fills)
                red_flag_ids = intake.detect_red_flags(message, schema, session.slots)
                unfilled = schema.unfilled_by_priority(session.slots, red_flag_ids)
                reply = _build_fake_intake_reply(schema, new_fills, unfilled, session.turns)
            elif assessment := addiction.assess(
                message,
                active=session.slots.get("track") == "중독",
                previous_kind=session.slots.get("addiction_type"),
            ):
                return _handle_addiction_route(
                    session,
                    message,
                    assessment,
                    schema,
                    effective_participant_id,
                )
            else:
                unfilled = schema.unfilled_by_priority(session.slots, red_flag_ids)
                reply = _build_guardrail_reply(schema, unfilled)
        if schema is not None and session.slots.get("track") == "위기":
            reply = _ensure_crisis_hotline(reply)

        storage.append_turn(session_id, "user", message, participant_id=effective_participant_id)
        storage.append_turn(session_id, "assistant", reply, participant_id=effective_participant_id)
        session.history.append({"role": "user", "content": message})
        session.history.append({"role": "assistant", "content": reply})
        session.turns += 1

        result = {"reply": reply, "turn": session.turns, "limit_reached": False}
        if schema is not None:
            final_unfilled = schema.unfilled_by_priority(session.slots, red_flag_ids)
            session.last_question_slot_id = final_unfilled[0].id if final_unfilled else None
            result["intake"] = _intake_state(schema, session.slots, final_unfilled)
        return result
    if schema is not None:
        classification = intake.extract_classification(message, schema, session.slots)
        crisis_detected = (
            classification.get("track") == "위기" or session.slots.get("track") == "위기"
        )
        assessment = addiction.assess(
            message,
            active=session.slots.get("track") == "중독",
            previous_kind=session.slots.get("addiction_type"),
        )
        if assessment is not None and not crisis_detected:
            return _handle_addiction_route(
                session,
                message,
                assessment,
                schema,
                effective_participant_id,
            )
    if schema is None:
        # 폴백 — 스키마 없는 지식셋(knowledge-alt)은 기존 경로 그대로.
        system = f"{persona}\n\n{progress}\n\n{doc_section}"
    else:
        # 질문 순서·반복 방지는 모델이 아니라 슬롯 엔진이 책임진다. 실모드도
        # 신호어/직전질문 fallback으로 먼저 상태를 갱신하고, 모델은 상담사 문장
        # 생성에 집중시킨다. 추가 의미 추출은 아래 extract_real에서 보강한다.
        new_fills = intake.extract_fake(message, schema, session.slots)

        filled_after_signals = {**session.slots, **new_fills}
        if not new_fills:
            new_fills.update(
                _answer_to_previous_question(
                    message, schema, filled_after_signals, session.last_question_slot_id
                )
            )
        session.slots.update(new_fills)
        red_flag_ids = intake.detect_red_flags(message, schema, session.slots)
        unfilled = schema.unfilled_by_priority(session.slots, red_flag_ids)
        slot_section = _build_slot_section(schema, session.slots, unfilled, session.turns)
        sections = [persona, progress, slot_section]
        if settings.model != "fake":
            sections.append(_CRISIS_ESCALATION_INSTRUCTION)
            sections.append(_EXTRACTION_INSTRUCTION)
        sections.append(doc_section)
        system = "\n\n".join(sections)

    if is_grounded_mode(settings.knowledge_dir) and not docs:
        # 코칭 팩(스키마 없음) + 검색 0건 — 근거 없이 모델이 자유 응답으로
        # 회귀하는 것을 llm.ask 호출 전에 차단한다(결정론 거부, GM1/GM3).
        reply = _NO_GROUNDING_REPLY
    else:
        reply = llm.ask(
            system=system,
            history=session.history,
            user=message,
            doc_titles=[doc.title for doc in docs],
            settings=settings,
        )

    if schema is not None and settings.model == "fake":
        reply = _build_fake_intake_reply(schema, new_fills, unfilled, session.turns)
        reply += _fake_progress_suffix(schema, new_fills, unfilled)
    elif schema is not None:
        # 실모드 단일 호출 통합(D02) — 응답 텍스트에 섞여온 슬롯 JSON을 신뢰
        # 경계 검증(intake.extract_real) 후 분리한다. reply는 이제 슬롯 JSON이
        # 제거된 clean 버전이라 history·storage에도 그대로 안전하게 쓴다.
        reply, real_fills = intake.extract_real(reply, schema, session.slots, message)
        session.slots.update(real_fills)
        new_fills.update(real_fills)
    if schema is not None:
        fallback_unfilled = schema.unfilled_by_priority(session.slots, red_flag_ids)
        reply = safety.sanitize_model_reply(
            reply,
            _build_guardrail_reply(schema, fallback_unfilled),
        )
        if session.slots.get("track") == "위기":
            reply = _ensure_crisis_hotline(reply)
    else:
        reply = safety.sanitize_model_reply(reply, _build_guardrail_reply(None, []))

    if is_grounded_mode(settings.knowledge_dir):
        # CAP06 -- 최종 reply 확정 이후 대조(위 덮어쓰기/sanitize 전부 반영된 뒤).
        # 집합 밖 식별자가 하나라도 있으면 날조로 보고 응답 전체를 고정 거부로
        # 대체한다(부분 인용만 제거하는 조용한 재작성은 하지 않는다, D02/D08).
        # 비교는 NFC 정규화로 한다 — macOS 파일시스템의 한글 rel_path는 NFD인데
        # 모델은 같은 경로를 NFC로 재출력해, 정상 인용이 날조로 오판돼 전량
        # 거부됐다(2026-07-18 실팩 실측). 정규화는 대조에만 쓰고 응답 원문은
        # 건드리지 않는다.
        injected_paths = {unicodedata.normalize("NFC", doc.rel_path) for doc in docs}
        cited_paths = {unicodedata.normalize("NFC", p) for p in _cited_doc_paths(reply)}
        if cited_paths - injected_paths:
            reply = _NO_GROUNDING_REPLY
        elif docs and settings.model != "fake" and not (cited_paths & injected_paths):
            # F2(MED-1) -- 문서가 주입됐는데 유효 인용이 0개면(근거 줄 없음/무관
            # 답) 무검증 통과를 막는다. fake 데모는 .md 인용 형식을 안 쓰므로
            # 제외한다. 내용 날조(경로는 실재하되 본문이 지어낸 경우)는 코드로
            # 완전차단 불가 -- 이 게이트는 '근거 없음'만 닫는다.
            reply = _NO_GROUNDING_REPLY

    storage.append_turn(session_id, "user", message, participant_id=effective_participant_id)
    storage.append_turn(session_id, "assistant", reply, participant_id=effective_participant_id)

    session.history.append({"role": "user", "content": message})
    session.history.append({"role": "assistant", "content": reply})
    session.turns += 1

    if session.turns >= MAX_TURNS:
        if schema is not None:
            # 채워진 슬롯이 이미 세션 상태에 있으므로 LLM을 부를 이유가 없다 —
            # 결정론 생성이라 fake 모드에서도 동일하게 돈다(CAP08).
            summary_json = intake.build_summary_json(schema, session.slots)
            storage.append_turn(
                session_id,
                "intake_summary",
                json.dumps(summary_json, ensure_ascii=False),
                participant_id=effective_participant_id,
            )
        else:
            try:
                summary = llm.ask(
                    system=_SUMMARY_INSTRUCTION,
                    history=session.history,
                    user=_SUMMARY_INSTRUCTION,
                    doc_titles=[],
                    settings=settings,
                )
                storage.append_turn(
                    session_id, "intake_summary", summary, participant_id=effective_participant_id
                )
            except Exception:
                pass  # 요약 실패가 본 대화 저장까지 유실시키지 않도록 격리

    result = {"reply": reply, "turn": session.turns, "limit_reached": False}
    if schema is not None:
        # 실모드는 extract_real이 unfilled 계산 이후에 슬롯을 채우므로,
        # 패널 상태와 다음 fallback 기준은 최종 session.slots 기준으로 다시 계산한다.
        final_unfilled = schema.unfilled_by_priority(session.slots, red_flag_ids)
        session.last_question_slot_id = final_unfilled[0].id if final_unfilled else None
        result["intake"] = _intake_state(schema, session.slots, final_unfilled)
    return result
