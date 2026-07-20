#!/usr/bin/env bash
# 리포 루트에 .venv 를 만들고 requirements.txt 를 설치한다. 이미 .venv 가 있으면 재생성하지 않는다(멱등).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi

.venv/bin/pip install -r requirements.txt
