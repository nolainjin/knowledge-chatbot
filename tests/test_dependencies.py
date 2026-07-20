import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_direct_dependencies_are_pinned_and_locked():
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_dependencies.py")],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
