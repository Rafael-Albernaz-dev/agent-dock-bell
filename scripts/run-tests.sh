#!/usr/bin/env bash
# Test runner for agent-dock-bell v2 (Go)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

echo "=== Running agent-dock-bell v2 Test Suite (with -race) ==="

if command -v dbus-run-session >/dev/null 2>&1; then
    dbus-run-session -- go test -race -v ./... "$@"
else
    go test -race -v ./... "$@"
fi

echo "✓ All tests executed successfully."
