"""100명 합성 내담자 프로파일을 만들고 fake 챗봇 응답을 DB까지 적재한다.

실제 개인정보가 아닌 시연용 synthetic corpus다. 같은 session_id/participant_id를
재사용하므로 여러 번 실행해도 SQLite는 UPSERT로 같은 100명 데모를 갱신한다.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app import chat
from app.config import Settings
from scripts.load_to_sqlite import load_day

PROFILE_MD = REPO_ROOT / "docs" / "demo-100-profiles.md"
CONVERSATIONS_DIR = REPO_ROOT / "data" / "conversations"
DB_PATH = REPO_ROOT / "data" / "chatlog.db"


@dataclass(frozen=True)
class DemoProfile:
    idx: int
    participant_id: str
    session_id: str
    track: str
    age_band: str
    situation: str
    chief_complaint: str
    context: str
    coping: str
    support: str
    expectation: str
    plan: str | None = None
    history: str | None = None

    def messages(self) -> list[str]:
        if self.track == "관계":
            core = [
                self.chief_complaint,
                self.context,
                self.coping,
                self.support,
                self.expectation,
            ]
        elif self.track == "위기":
            core = [
                self.chief_complaint,
                self.plan or "구체적인 계획은 없지만 오늘 혼자 있으면 위험할까 걱정돼요.",
                self.history or "예전에 비슷한 생각한 적은 있었지만 실제로 시도한 적은 없어요.",
                self.coping,
                self.support,
                self.expectation,
            ]
        else:
            core = [
                self.chief_complaint,
                self.context,
                self.coping,
                self.support,
                self.expectation,
            ]
        fillers = [
            "지금 말한 내용이 제일 큰 것 같아요.",
            "빠뜨린 건 많지 않은데 막상 말하려니 정리가 어렵네요.",
            "상담에서 차분히 이어서 말해보고 싶어요.",
            "오늘은 여기까지 정리되면 좋겠습니다.",
            "네, 그렇게 진행해도 괜찮습니다.",
        ]
        return (core + fillers)[: chat.MAX_TURNS]


EMOTIONAL_COMPLAINTS = [
    "우울한 기분과 무기력함이 계속돼요.",
    "요즘 마음이 자주 불안하고 걱정이 많아요.",
    "밤에 잠을 잘 못 자고 피곤해요.",
    "시험과 진로 압박 때문에 숨이 막히는 느낌이에요.",
    "회사 일 생각만 하면 긴장되고 집중이 안 돼요.",
    "이유 없이 슬프고 눈물이 나고 아무것도 하기 싫어요.",
    "작은 일에도 걱정이 커져서 일상이 흔들려요.",
    "번아웃처럼 무기력하고 기운이 빠져 계속 쉬고 싶어요.",
]
EMOTIONAL_CONTEXTS = [
    "두 달 정도 됐고 회사에 가기 전부터 몸이 무거워져요.",
    "최근 한 달 사이 심해졌고 잠과 식욕에 영향이 있어요.",
    "학기 시작 후 계속됐고 공부 집중이 거의 안 돼요.",
    "갑자기 시작됐다기보다 조금씩 쌓이다가 최근에 무너졌어요.",
    "퇴근 뒤에도 머리가 쉬지 않아서 일상 회복이 어렵습니다.",
]
RELATION_COMPLAINTS = [
    "남편과 갈등 때문에 너무 힘들어요.",
    "아내와 대화가 자꾸 싸움으로 번져요.",
    "가족이나 대인관계 때문에 마음이 지쳐요.",
    "아이와 부딪히는 일이 많아서 어떻게 해야 할지 모르겠어요.",
    "친구 관계에서 계속 소외되는 느낌이 들어요.",
    "직장 동료와 갈등이 커져서 출근이 부담돼요.",
]
RELATION_CONTEXTS = [
    "배우자와는 몇 년째 비슷한 패턴이 반복되고 최근 한 달 더 심해졌어요.",
    "가족 문제는 오래됐지만 최근 대화가 거의 끊긴 상태예요.",
    "친구와는 최근 몇 주 사이 멀어졌고 제가 계속 눈치를 봐요.",
    "아이와의 갈등은 올해 들어 잦아졌고 집 분위기가 계속 긴장돼요.",
    "동료와의 갈등은 프로젝트 이후 시작됐고 매일 마주쳐야 해서 부담입니다.",
]
CRISIS_COMPLAINTS = [
    "요즘 죽고 싶다는 생각이 자주 들어요.",
    "살고 싶지 않다는 생각이 반복돼서 무섭습니다.",
    "사라지고 싶다는 생각이 들고 혼자 있으면 위험할까 걱정돼요.",
    "자해 충동이 올라와서 상담 전에 먼저 말해야 할 것 같아요.",
]
CRISIS_PLANS = [
    "구체적인 계획은 없지만 오늘 혼자 있으면 위험할까 걱정돼요.",
    "지금 당장 실행할 계획은 없지만 약을 보면 충동이 생길까 봐 치워뒀어요.",
    "구체적인 수단은 정하지 않았지만 밤에 혼자 있으면 생각이 세져요.",
    "오늘은 계획이 없고 친구에게 연락해둔 상태예요.",
]
CRISIS_HISTORY = [
    "예전에 비슷한 생각한 적은 있었지만 실제로 시도한 적은 없어요.",
    "과거에 자해한 적이 한 번 있어서 이번에는 빨리 도움을 받고 싶어요.",
    "예전에 약을 먹으려고 한 적이 있는데 멈췄고 지금은 혼자 두기 싫어요.",
    "자살 시도한 적은 없지만 생각이 반복된 적은 있습니다.",
]
COPING = [
    "그냥 집에서 쉬었어요.",
    "산책을 해봤지만 오래가지는 않았어요.",
    "친구에게 얘기해봤는데 아직 정리는 안 됐어요.",
    "병원에 가보려 했지만 예약을 미뤘어요.",
    "참고 버티는 식으로 지냈는데 한계가 온 것 같아요.",
]
SAFE_COPING = [item for item in COPING if "병원" not in item and "예약" not in item]
SUPPORT = [
    "친구 한 명이 알고 있고 가끔 연락해줘요.",
    "가족은 자세히 모르고 혼자 감당하는 편이에요.",
    "엄마에게 조금 말했지만 걱정할까 봐 다 말하지 못했어요.",
    "동료 한 명에게만 이야기해봤어요.",
    "도와주는 사람은 거의 없고 상담에서 처음 정리해보려 합니다.",
]
EXPECTATIONS = [
    "상담에서 마음이 조금 편해지고 싶어요.",
    "상황을 정리하고 다음에 뭘 해야 할지 도움을 받고 싶어요.",
    "제가 왜 이렇게 반응하는지 이해하고 싶어요.",
    "관계를 덜 망치면서 말하는 방법을 배우고 싶어요.",
    "지금 상태를 안전하게 넘기는 방법을 같이 세우고 싶어요.",
]
AGE_BANDS = ["10대 후반", "20대 초반", "20대 후반", "30대", "40대", "50대"]
SITUATIONS = ["학생", "취업준비", "직장인", "자영업", "육아 중", "이직 준비", "휴직 중"]


def build_profiles(count: int) -> list[DemoProfile]:
    profiles: list[DemoProfile] = []
    for i in range(1, count + 1):
        if i <= round(count * 0.52):
            track = "정서"
            chief = EMOTIONAL_COMPLAINTS[(i - 1) % len(EMOTIONAL_COMPLAINTS)]
            context = EMOTIONAL_CONTEXTS[(i - 1) % len(EMOTIONAL_CONTEXTS)]
            plan = history = None
        elif i <= round(count * 0.85):
            track = "관계"
            chief = RELATION_COMPLAINTS[(i - 1) % len(RELATION_COMPLAINTS)]
            context = RELATION_CONTEXTS[(i - 1) % len(RELATION_CONTEXTS)]
            plan = history = None
        else:
            track = "위기"
            chief = CRISIS_COMPLAINTS[(i - 1) % len(CRISIS_COMPLAINTS)]
            context = "안전 확인 우선"
            plan = CRISIS_PLANS[(i - 1) % len(CRISIS_PLANS)]
            history = CRISIS_HISTORY[(i - 1) % len(CRISIS_HISTORY)]

        profiles.append(
            DemoProfile(
                idx=i,
                participant_id=f"demo-person-{i:03d}",
                session_id=f"demo-session-{i:03d}",
                track=track,
                age_band=AGE_BANDS[(i - 1) % len(AGE_BANDS)],
                situation=SITUATIONS[(i - 1) % len(SITUATIONS)],
                chief_complaint=chief,
                context=context,
                coping=(COPING if track == "위기" else SAFE_COPING)[
                    (i - 1) % len(COPING if track == "위기" else SAFE_COPING)
                ],
                support=SUPPORT[(i - 1) % len(SUPPORT)],
                expectation=EXPECTATIONS[(i - 1) % len(EXPECTATIONS)],
                plan=plan,
                history=history,
            )
        )
    return profiles


def write_profiles_markdown(profiles: list[DemoProfile]) -> None:
    lines = [
        "# 데모 100명 합성 내담자 프로파일",
        "",
        "- 생성 목적: 챗봇 응답, JSON 로그, SQLite 적재, 통계 대시보드 시연",
        "- 개인정보: 실제 인물·실제 상담 기록 없음. 모두 합성 데이터",
        "- 개인번호: `demo-person-###`; 세션: `demo-session-###`",
        "",
        "| 번호 | 개인번호 | 세션 | 트랙 | 연령대 | 상황 | 첫 호소 |",
        "|---:|---|---|---|---|---|---|",
    ]
    for profile in profiles:
        lines.append(
            f"| {profile.idx} | {profile.participant_id} | {profile.session_id} | "
            f"{profile.track} | {profile.age_band} | {profile.situation} | {profile.chief_complaint} |"
        )

    lines.extend(["", "## 상세 프로파일", ""])
    for profile in profiles:
        lines.extend(
            [
                f"### {profile.idx:03d}. {profile.participant_id}",
                "",
                f"- session_id: `{profile.session_id}`",
                f"- track: {profile.track}",
                f"- age_band: {profile.age_band}",
                f"- situation: {profile.situation}",
                f"- chief_complaint: {profile.chief_complaint}",
                f"- context: {profile.context}",
                f"- coping: {profile.coping}",
                f"- support: {profile.support}",
                f"- expectation: {profile.expectation}",
            ]
        )
        if profile.plan:
            lines.append(f"- crisis_plan_means: {profile.plan}")
        if profile.history:
            lines.append(f"- crisis_attempt_history: {profile.history}")
        lines.extend(["- scripted_turns:"])
        for turn, message in enumerate(profile.messages(), start=1):
            lines.append(f"  {turn}. {message}")
        lines.append("")

    PROFILE_MD.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_MD.write_text("\n".join(lines), encoding="utf-8")


def reset_demo_json() -> int:
    day_dir = CONVERSATIONS_DIR / date.today().isoformat()
    if not day_dir.exists():
        return 0
    removed = 0
    for path in day_dir.glob("demo-session-*.json"):
        path.unlink()
        removed += 1
    return removed


def simulate(profiles: list[DemoProfile]) -> int:
    os.chdir(REPO_ROOT)
    settings = Settings(
        anthropic_api_key="",
        knowledge_dir=str(REPO_ROOT / "knowledge"),
        model="fake",
        trust_proxy_hops=0,
        daily_request_cap=100000,
    )
    chat._sessions.clear()
    sent = 0
    for profile in profiles:
        for message in profile.messages():
            chat.handle_message(
                profile.session_id,
                message,
                settings,
                participant_id=profile.participant_id,
            )
            sent += 1
    return sent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--reset", action="store_true", help="오늘 demo-session-*.json만 재생성")
    parser.add_argument("--no-simulate", action="store_true", help="프로파일 마크다운만 생성")
    args = parser.parse_args(argv)

    profiles = build_profiles(args.count)
    write_profiles_markdown(profiles)
    removed = reset_demo_json() if args.reset else 0
    sent = 0 if args.no_simulate else simulate(profiles)
    loaded = 0 if args.no_simulate else load_day(date.today(), CONVERSATIONS_DIR, DB_PATH)
    print(
        f"profiles={len(profiles)} markdown={PROFILE_MD} removed={removed} "
        f"user_messages={sent} sqlite_turns_loaded={loaded} db={DB_PATH}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
