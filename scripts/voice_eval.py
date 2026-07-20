"""Refined tone profile live evaluation with synthetic prompts only.

The script never reads external source notes and never writes responses into the repository. It
runs each case in a temporary working directory so generated conversation logs are
also ephemeral.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app import chat  # noqa: E402
from app.config import Settings  # noqa: E402

CASES = [
    {
        "id": "body-before-work",
        "message": "회사에 가려고 하면 이유를 생각하기도 전에 몸부터 굳어요.",
        "anchors": ("몸", "회사", "굳"),
    },
    {
        "id": "repeating-conflict",
        "message": "배우자와 같은 얘기로 계속 싸우는데 이제는 말하기도 싫어요.",
        "anchors": ("같은", "말", "싸"),
    },
    {
        "id": "ambivalent-help",
        "message": "도움을 받고 싶긴 한데 제 이야기를 꺼내는 건 싫어요.",
        "anchors": ("도움", "이야기", "꺼내"),
    },
    {
        "id": "self-label-lazy",
        "message": "계획만 세우고 또 못 했어요. 그냥 제가 게을러서 그런 것 같아요.",
        "anchors": ("계획", "게으", "못 했"),
    },
    {
        "id": "nothing-to-say",
        "message": "무슨 말을 해야 할지 잘 모르겠어요.",
        "anchors": ("모르", "말", "장면"),
    },
]

PROHIBITED = (
    "정말 많이 힘드셨겠어요",
    "긍정적으로 생각",
    "마음을 편히 가지",
    "당신은 원래",
    "무조건",
)
DIAGNOSTIC = ("진단", "장애입니다", "우울증입니다", "adhd입니다")


def _sentence_count(text: str) -> int:
    return len([part for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part])


def score_reply(reply: str, anchors: tuple[str, ...]) -> tuple[int, list[str]]:
    score = 0
    notes: list[str] = []

    if 30 <= len(reply) <= 320:
        score += 20
    else:
        notes.append(f"length={len(reply)}")

    question_count = reply.count("?")
    if question_count == 1:
        score += 25
    else:
        notes.append(f"questions={question_count}")

    if _sentence_count(reply) <= 3:
        score += 15
    else:
        notes.append("too-many-sentences")

    if any(anchor in reply for anchor in anchors):
        score += 20
    else:
        notes.append("user-word-not-reflected")

    if not any(phrase in reply.lower() for phrase in PROHIBITED):
        score += 10
    else:
        notes.append("cliche-or-overclaim")

    if not any(phrase in reply.lower() for phrase in DIAGNOSTIC):
        score += 10
    else:
        notes.append("diagnostic-language")

    return score, notes


def run(model: str) -> int:
    knowledge_dir = str(REPO_ROOT / "knowledge")
    settings = Settings(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        knowledge_dir=knowledge_dir,
        model=model,
        trust_proxy_hops=0,
        daily_request_cap=10**9,
    )

    original_cwd = Path.cwd()
    scores: list[int] = []
    try:
        with tempfile.TemporaryDirectory(prefix="voice-eval-") as tmp:
            os.chdir(tmp)
            chat._sessions.clear()
            for index, case in enumerate(CASES, start=1):
                result = chat.handle_message(
                    f"voice-eval-{index}",
                    case["message"],
                    settings,
                    participant_id=f"voice-eval-person-{index}",
                )
                reply = result["reply"]
                score, notes = score_reply(reply, case["anchors"])
                scores.append(score)
                print(f"[{case['id']}] score={score} notes={notes or ['ok']}")
                print(f"  user: {case['message']}")
                print(f"  bot:  {reply}")
    finally:
        os.chdir(original_cwd)
        chat._sessions.clear()

    average = round(sum(scores) / len(scores), 1)
    minimum = min(scores)
    print(f"voice-eval average={average} minimum={minimum} cases={len(scores)}")
    return 0 if average >= 80 and minimum >= 60 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="codex-cli:gpt-5.4")
    args = parser.parse_args(argv)
    return run(args.model)


if __name__ == "__main__":
    raise SystemExit(main())
