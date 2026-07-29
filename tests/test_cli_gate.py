"""门禁退出码行为。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import typer
from code_review_agent.cli import _exit_for_gate


def _expect_exit(summary: dict, fail_on: str, code: int | None) -> None:
    try:
        _exit_for_gate(summary, fail_on, has_error=False)
        assert code is None
    except typer.Exit as exc:
        assert exc.exit_code == code


def test_fail_on_fatal_only() -> None:
    _expect_exit({"fatal": 0, "warning": 9, "slow_sql": 2, "slow_code": 1}, "fatal", None)
    _expect_exit({"fatal": 1, "warning": 0, "slow_sql": 0, "slow_code": 0}, "fatal", 1)


def test_fail_on_slow() -> None:
    _expect_exit({"fatal": 0, "warning": 5, "slow_sql": 1, "slow_code": 0}, "slow", 1)
    _expect_exit({"fatal": 0, "warning": 5, "slow_sql": 0, "slow_code": 0}, "slow", None)


def test_fail_on_any() -> None:
    _expect_exit({"fatal": 0, "warning": 1, "slow_sql": 0, "slow_code": 0}, "any", 1)


def test_fail_on_off() -> None:
    _expect_exit({"fatal": 3, "warning": 9, "slow_sql": 2, "slow_code": 1}, "off", None)


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
