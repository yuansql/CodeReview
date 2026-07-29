"""报告摘要分类计数。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from code_review_agent.models import Finding, Severity
from code_review_agent.report import render_report


def test_summary_breaks_down_warning_categories() -> None:
    findings = [
        Finding(
            severity=Severity.WARNING,
            category="NAMING",
            file="a.php",
            line=1,
            message="x",
        ),
        Finding(
            severity=Severity.WARNING,
            category="NAMING",
            file="a.php",
            line=2,
            message="y",
        ),
        Finding(
            severity=Severity.WARNING,
            category="SLOW_CODE",
            file="a.php",
            line=3,
            message="z",
        ),
        Finding(
            severity=Severity.WARNING,
            category="SLOW_SQL",
            file="a.php",
            line=4,
            message="s",
        ),
    ]
    md = render_report(
        project_id="demo",
        repo_path="/tmp",
        base_ref="main",
        head_ref="feat",
        findings=findings,
        skipped_files=[],
        standards_python=[],
        standards_php=[],
        commits=[],
    )
    assert "SLOW_SQL（淡红）：1" in md
    assert "SLOW_CODE：1" in md
    assert "NAMING：2" in md
    assert "警告合计：4" in md


def _run_all() -> int:
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {name}: {exc}")
        else:
            print(f"ok   {name}")
    print("FAILED" if failures else "ALL PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
