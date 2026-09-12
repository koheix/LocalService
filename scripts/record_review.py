#!/usr/bin/env python3
"""レビュー結果を reports/review-record.json に記録する。

.claude/hooks/guard-gh.ps1 は、いまの HEAD に対するこの記録が無い、または
verdict が pass でないと `gh pr create` をブロックする。

使い方:
    python scripts/record_review.py --reviewers test-auditor,pr-reviewer --verdict pass
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = REPO_ROOT / "reports"


def git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reviewers", required=True, help="カンマ区切り。例: test-auditor,pr-reviewer"
    )
    parser.add_argument("--verdict", required=True, choices=["pass", "changes-requested"])
    args = parser.parse_args()

    commit = git_head()
    if not commit:
        print("git HEAD を特定できません。", file=sys.stderr)
        return 1

    REPORTS_DIR.mkdir(exist_ok=True)
    record = {
        "commit": commit,
        "reviewers": [r.strip() for r in args.reviewers.split(",") if r.strip()],
        "verdict": args.verdict,
        "recordedAtUtc": datetime.now(timezone.utc).isoformat(),
    }
    (REPORTS_DIR / "review-record.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"レビュー記録を書き込みました: {args.verdict} "
        f"({', '.join(record['reviewers'])}) @ {commit}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
