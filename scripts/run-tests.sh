#!/usr/bin/env bash
# Universal test runner for agent-dock-bell
# Detects available test runner (.venv/pytest, uv, or python3 -m unittest)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

export PYTHONPATH="${REPO_ROOT}/src:${PYTHONPATH:-}"

echo "=== Running agent-dock-bell Test Suite ==="

if [ -f "${REPO_ROOT}/.venv/bin/pytest" ]; then
    echo "→ Using virtualenv pytest (.venv/bin/pytest)"
    "${REPO_ROOT}/.venv/bin/pytest" -v "$@"
elif command -v uv >/dev/null 2>&1; then
    echo "→ Using uv (uv run --extra dev pytest)"
    uv run --extra dev pytest -v "$@"
elif command -v pytest >/dev/null 2>&1; then
    echo "→ Using system pytest"
    pytest -v "$@"
else
    echo "→ Fallback: Running python3 unittest discovery"
    python3 -m unittest discover -s tests -p "test_*.py" "$@"
fi

echo "✓ All tests executed successfully."
