"""애플리케이션 설정 — 환경변수를 한곳에서 읽는다."""

import os
from dataclasses import dataclass


class ConfigError(Exception):
    """필수 환경변수 미설정 시 raise — main.py에서 500 + 사유로 변환한다."""


@dataclass
class Settings:
    anthropic_api_key: str
    knowledge_dir: str
    model: str
    trust_proxy_hops: int
    daily_request_cap: int

    @classmethod
    def from_env(cls) -> "Settings":
        knowledge_dir = os.getenv("KNOWLEDGE_DIR")
        if not knowledge_dir:
            raise ConfigError(
                "KNOWLEDGE_DIR 환경변수가 설정되지 않았습니다. 연결할 지식 폴더 경로를 지정하세요."
            )
        return cls(
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            knowledge_dir=knowledge_dir,
            model=os.getenv("MODEL", "auto"),
            trust_proxy_hops=int(os.getenv("TRUST_PROXY_HOPS", "0")),
            daily_request_cap=int(os.getenv("DAILY_REQUEST_CAP", "500")),
        )
