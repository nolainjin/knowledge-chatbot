"""중독 관련 초기 발화를 전문기관 정보 제공으로 라우팅한다.

이 모듈은 진단이나 치료를 하지 않는다. 발화에서 중독 유형과 안내 긴급도를
보수적으로 분류하고, 유형별 공식 지원기관을 결정론적으로 안내한다. LLM이 문구나
기관 번호를 바꾸지 못하도록 채팅 모델 호출보다 먼저 실행한다.
"""

from dataclasses import dataclass

CENTER_DIRECTORY_URL = "https://www.mohw.go.kr/menu.es?hl=ko-KR&mid=a10706040400"

_TYPE_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "도박",
        (
            "도박",
            "베팅",
            "카지노",
            "스포츠토토",
            "사설토토",
            "슬롯머신",
        ),
    ),
    (
        "마약·약물",
        (
            "마약",
            "필로폰",
            "대마",
            "코카인",
            "펜타닐",
            "약물 중독",
            "처방약 중독",
            "약을 못 끊",
            "약에 의존",
        ),
    ),
    (
        "인터넷·스마트폰·게임",
        (
            "인터넷 중독",
            "인터넷 과의존",
            "스마트폰 중독",
            "스마트폰 과의존",
            "휴대폰 중독",
            "게임 중독",
            "게임 과몰입",
            "게임을 못 끊",
            "밤새 게임",
            "폰을 못 놓",
        ),
    ),
    (
        "알코올",
        (
            "알코올",
            "음주 문제",
            "과음",
            "소주",
            "맥주",
            "양주",
            "술 중독",
            "술을 못 끊",
            "술을 끊지 못",
            "술 때문에",
            "매일 술",
            "술 없이는",
        ),
    ),
)

_GENERIC_SIGNALS = (
    "중독인 것",
    "중독 같",
    "중독 문제",
    "중독 상담",
    "중독센터",
    "중독 센터",
)

_EMERGENCY_SIGNALS = (
    "과다복용",
    "과다 복용",
    "한꺼번에 먹",
    "너무 많이 먹었",
    "의식이 없",
    "의식을 잃",
    "깨워도 안",
    "호흡이 이상",
    "호흡 곤란",
    "숨을 못 쉬",
    "경련",
    "발작",
    "금단 섬망",
    "심한 금단",
    "환각이 보",
    "환청이 들",
)

_HIGH_RISK_SIGNALS = (
    "매일",
    "하루도",
    "끊지 못",
    "못 끊",
    "통제가 안",
    "조절이 안",
    "금단",
    "손이 떨",
    "블랙아웃",
    "기억이 안 나",
    "빚",
    "대출",
    "생활이 무너",
    "직장을 못",
    "학교를 못",
    "가족이 떠",
)


@dataclass(frozen=True)
class AddictionAssessment:
    kind: str
    severity: str


def _contains_any(message: str, signals: tuple[str, ...]) -> bool:
    normalized = message.casefold()
    return any(signal.casefold() in normalized for signal in signals)


def detect_kind(message: str) -> str | None:
    """명시적인 문제 표현이 있을 때만 중독 유형을 반환한다.

    평범한 "술 한잔"이나 "게임을 했다"를 중독으로 과탐지하지 않도록 단독 명사는
    피하고 문제·통제 상실이 드러나는 복합 표현을 사용한다.
    """
    for kind, signals in _TYPE_SIGNALS:
        if _contains_any(message, signals):
            return kind
    if _contains_any(message, _GENERIC_SIGNALS):
        return "기타·미확인"
    return None


def assess(
    message: str,
    *,
    active: bool = False,
    previous_kind: str | None = None,
) -> AddictionAssessment | None:
    """중독 맥락 여부와 안내 긴급도를 분류한다.

    ``active``는 앞선 턴에서 이미 중독 경로로 들어온 세션의 짧은 후속 답변
    (예: "아니요", "매일 해요")도 놓치지 않기 위한 상태다.
    """
    kind = detect_kind(message) or previous_kind
    emergency = _contains_any(message, _EMERGENCY_SIGNALS)
    if kind is None and emergency:
        kind = "마약·약물"
    if kind is None and not active:
        return None
    kind = kind or "기타·미확인"

    if emergency:
        severity = "응급"
    elif _contains_any(message, _HIGH_RISK_SIGNALS):
        severity = "고위험"
    else:
        severity = "평가 필요"
    return AddictionAssessment(kind=kind, severity=severity)


def _specialized_contact(kind: str) -> str | None:
    if kind == "도박":
        return "도박문제 헬프라인 1336(국번 없이, 무료)"
    if kind == "마약·약물":
        return "마약류 상담 1342(국번 없이, 익명 상담)"
    if kind == "인터넷·스마트폰·게임":
        return "스마트쉼센터 1599-0075"
    return None


def build_reply(assessment: AddictionAssessment, *, followup: bool = False) -> str:
    """심각도와 유형에 맞는 전문기관 안내 문구를 만든다.

    첫 안내 뒤의 후속 답변에서는 같은 응급 확인 질문을 반복하지 않는다.
    """
    contact = _specialized_contact(assessment.kind)
    contact_line = f" 유형별 전문창구는 {contact}입니다." if contact else ""
    center_line = (
        "가까운 중독관리통합지원센터는 본인과 가족에게 전화·방문 상담, 단기개입, "
        f"치료·재활 연계를 제공합니다. 센터 목록: {CENTER_DIRECTORY_URL}"
    )

    if assessment.severity == "응급":
        return (
            "지금 말씀은 의식 저하·호흡 이상·경련·과다복용·심한 금단 같은 응급 신호일 수 "
            "있습니다. 중독센터 상담을 기다리지 말고 119 또는 가까운 응급실로 바로 도움을 "
            "요청하고, 가능하면 혼자 있거나 직접 운전하지 마세요. 급한 위험이 가라앉은 뒤에는 "
            f"중독 전문기관으로 이어가는 것이 맞습니다.{contact_line} {center_line}"
        )

    if followup and assessment.severity == "고위험":
        return (
            "현재 발화에서는 즉시 119가 필요한 응급 신호가 확인되지 않았지만, 앞서 확인된 "
            "통제 상실이나 생활 손상 수준이라면 일반 상담보다 중독 전문기관 연결을 우선해야 "
            f"합니다.{contact_line} {center_line}"
        )

    if assessment.severity == "고위험":
        return (
            "반복 사용, 통제 상실, 금단이나 생활 손상이 보이면 이 챗봇에서 일반 상담을 이어가기보다 "
            "중독 전문기관의 평가와 연결을 가능한 한 빨리 받는 단계입니다."
            f"{contact_line} {center_line} 의식 저하·호흡 이상·경련·과다복용처럼 급한 신호가 "
            "생기면 센터 상담을 기다리지 말고 119 또는 응급실을 이용하세요."
        )

    if followup:
        if assessment.kind == "기타·미확인":
            return (
                "현재 발화에서는 급한 응급 신호가 확인되지 않았습니다. 이 챗봇에서 일반 상담을 "
                f"이어가기보다 전문기관 정보를 연결하겠습니다. {center_line} 알코올, 약물, 도박, "
                "인터넷·스마트폰·게임 중 어느 문제에 가장 가까운가요?"
            )
        return (
            "현재 발화에서는 급한 응급 신호가 확인되지 않았습니다. 이 챗봇에서 일반 상담을 "
            f"이어가기보다 해당 중독 전문기관 정보를 연결하는 것이 맞습니다.{contact_line} "
            f"{center_line}"
        )
    return (
        "중독 여부를 여기서 단정하지는 않겠습니다. 다만 반복되거나 일상에 영향을 주기 시작했다면 "
        "이 챗봇에서 일반 상담으로 진행하기보다 중독 전문기관에서 먼저 평가받도록 정보를 연결하는 "
        f"것이 적절합니다.{contact_line} {center_line} 지금 의식 저하·호흡 이상·경련·과다복용·심한 "
        "금단처럼 바로 의료 도움이 필요한 신호가 있나요?"
    )
