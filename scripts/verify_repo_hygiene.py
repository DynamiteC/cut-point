"""Phase 9 gate helper: asserts LICENSE exists, README contains required
sections, and .env is not tracked by git.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_README_SECTIONS = [
    "# CutPoint",
    "## Quickstart",
    "## Architecture",
    "## Environment variables",
]


def check_license() -> list[str]:
    license_path = REPO_ROOT / "LICENSE"
    if not license_path.exists():
        return ["LICENSE file is missing"]
    if "MIT License" not in license_path.read_text():
        return ["LICENSE does not appear to be MIT"]
    return []


def check_readme_sections() -> list[str]:
    readme_path = REPO_ROOT / "README.md"
    if not readme_path.exists():
        return ["README.md is missing"]
    text_lower = readme_path.read_text().lower()
    return [f"README.md missing section: {s}" for s in REQUIRED_README_SECTIONS if s.lower() not in text_lower]


def check_env_not_tracked() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", ".env"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    if result.stdout.strip():
        return [".env is tracked by git -- remove it and add to .gitignore"]
    return []


def main() -> int:
    failures: list[str] = []
    failures.extend(check_license())
    failures.extend(check_readme_sections())
    failures.extend(check_env_not_tracked())

    if failures:
        print("REPO HYGIENE FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("REPO HYGIENE PASSED: LICENSE present, README sections present, .env not tracked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
