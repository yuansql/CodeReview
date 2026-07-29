#!/usr/bin/env bash
# 企业 CI 门禁：审业务仓 diff。
# 用法：
#   scripts/ci_gate.sh --project fm-app --base origin/pre --head feature-x [--fail-on fatal|slow|any]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="${PYTHON:-python3}"
fi
# 确保可 import 已安装包；开发态用 src
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$PY" -m code_review_agent review "$@"
