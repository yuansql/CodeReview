"""飞书推送文案与签名（不打真实 webhook）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from code_review_agent.notify_feishu import build_review_text, gen_sign


def test_keyword_in_text() -> None:
    text = build_review_text(
        keyword="审核推送",
        project_id="fm-app",
        base_ref="origin/pre",
        head_ref="a6",
        summary={"fatal": 0, "slow_sql": 0, "slow_code": 1, "naming": 2, "warning": 3},
        report_path="/tmp/a.md",
    )
    assert text.startswith("审核推送")
    assert "fm-app" in text
    assert "SLOW_CODE 1" in text


def test_gen_sign_stable() -> None:
    ts, sign = gen_sign("secret", timestamp="1599360473")
    assert ts == "1599360473"
    assert isinstance(sign, str) and len(sign) > 10


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
