#!/usr/bin/env bash
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for python in python3 python; do
  if command -v "$python" >/dev/null 2>&1; then
    PYTHONPATH="$root" exec "$python" -m lens_claude_code "$@"
  fi
done
exit 0
