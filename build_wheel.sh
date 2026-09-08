#!/usr/bin/env bash
set -euo pipefail

# Build an openhop-alert-plugin wheel from the repository root.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$SCRIPT_DIR"

# Prefer local project venvs. If none exist, use python3/python and require
# the build module to already be available (avoids PEP 668 failures).
if [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"
elif [[ -x "../openhop_repeater/.venv/bin/python" ]]; then
  PYTHON="../openhop_repeater/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
else
  PYTHON="python"
fi

echo "Building wheel with: $PYTHON"
if ! "$PYTHON" -m build --version >/dev/null 2>&1; then
  cat >&2 <<'EOF'
The Python 'build' module is not installed for this interpreter.

Recommended:
1) python3 -m venv .venv
2) source .venv/bin/activate
3) pip install build

Then run: ./build_wheel.sh
EOF
  exit 1
fi

"$PYTHON" -m build --wheel

echo
echo "Wheel artifacts:"
ls -1 dist/*.whl
