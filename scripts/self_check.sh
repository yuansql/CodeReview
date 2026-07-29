#!/usr/bin/env bash
# 本仓库自检：黄金用例。供 pre-commit / CI 调用。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="${PYTHON:-python3}"
fi
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"
status=0
for t in "$ROOT"/tests/test_*.py; do
  echo "==> $(basename "$t")"
  if ! "$PY" "$t"; then
    status=1
  fi
done
exit "$status"
