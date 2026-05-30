#!/usr/bin/env bash
set -euo pipefail

AGENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$AGENT_DIR/.." && pwd)"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
SCRIPT_PATH="$AGENT_DIR/course_notes_agent.py"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "未找到虚拟环境 Python: $PYTHON_BIN" >&2
  echo "请先创建 .venv 并安装依赖。" >&2
  exit 1
fi

exec "$PYTHON_BIN" "$SCRIPT_PATH" "$@"
