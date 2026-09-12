#!/usr/bin/env python3
"""pytest を実行し、reports/test-report.md と reports/verification.json を生成する。

reports/verification.json は .claude/hooks/guard-gh.ps1 が読み、いまの HEAD で
失敗が記録されていなければ `gh pr create` をブロックする。テストを緩めて通すのではなく、
落ちたことを正しく記録すること。

使い方:
    python scripts/gen_report.py     # make test-report から呼ばれる
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = REPO_ROOT / "reports"

SUMMARY_RE = re.compile(r"(\d+) (passed|failed|skipped|error|errors|xfailed|xpassed)")


def run_pytest() -> tuple[int, str]:
    proc = subprocess.run(
        ["docker", "compose", "run", "--rm", "--no-deps", "gateway", "pytest", "-q"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def parse_summary(output: str) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "skipped": 0, "error": 0}
    for match in SUMMARY_RE.finditer(output):
        n = int(match.group(1))
        kind = match.group(2)
        if kind in ("error", "errors"):
            counts["error"] += n
        elif kind in ("passed", "failed", "skipped"):
            counts[kind] += n
        # xfailed / xpassed は意図的なマーカーのため、成功・失敗のどちらにも数えない
    return counts


def git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def main() -> int:
    REPORTS_DIR.mkdir(exist_ok=True)

    exit_code, output = run_pytest()
    counts = parse_summary(output)

    # pytest が summary 行すら出せずに落ちた場合（起動失敗など）も失敗として扱う。
    # ここを 0 のままにすると、壊れた実行が「全部成功」として記録されてしまう。
    if exit_code != 0 and counts["failed"] == 0 and counts["error"] == 0:
        counts["error"] = 1

    failed_total = counts["failed"] + counts["error"]
    commit = git_head()
    generated_at = datetime.now(timezone.utc).isoformat()

    verification = {
        "commit": commit,
        "passed": counts["passed"],
        "failed": failed_total,
        "skipped": counts["skipped"],
        "total": counts["passed"] + failed_total + counts["skipped"],
        "generatedAtUtc": generated_at,
    }
    (REPORTS_DIR / "verification.json").write_text(
        json.dumps(verification, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    report_md = f"""# テスト結果

| 項目 | 件数 |
|---|---|
| 成功 | {counts['passed']} |
| 失敗 | {failed_total} |
| スキップ | {counts['skipped']} |

コミット: `{commit}`
生成日時（UTC）: {generated_at}

<details>
<summary>pytest 出力</summary>

```
{output.strip()}
```

</details>
"""
    (REPORTS_DIR / "test-report.md").write_text(report_md, encoding="utf-8")

    print(f"成功 {counts['passed']} / 失敗 {failed_total} / スキップ {counts['skipped']}")
    return 1 if failed_total > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
