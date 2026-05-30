#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $# -eq 0 ]]; then
  exec "$ROOT_DIR/course.sh"
fi

case "$1" in
  course)
    shift
    exec "$ROOT_DIR/course.sh" "$@"
    ;;
  *)
    echo "用法: ./run.sh [course]" >&2
    exit 1
    ;;
esac
