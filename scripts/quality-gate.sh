#!/usr/bin/env bash
# Mandatory 6-Stage Quality Gate Suite for agent-dock-bell v2
# Zero host side-effects: runs integration tests inside an isolated dbus-run-session.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

echo "=========================================================="
echo "🛡️  RUNNING AGENT-DOCK-BELL v2 QUALITY GATES (6 GATES)"
echo "=========================================================="

# -----------------------------------------------------------------------------
# GATE 1: Static Analysis, Formatting & Compilation
# -----------------------------------------------------------------------------
echo ""
echo "[Gate 1/6] Checking Go formatting, vet, and binary compilation..."
UNFORMATTED="$(gofmt -s -l cmd internal)"
if [ -n "${UNFORMATTED}" ]; then
    echo "❌ Error: Unformatted Go files detected:"
    echo "${UNFORMATTED}"
    exit 1
fi

go vet ./...
mkdir -p "${REPO_ROOT}/build"
go build -o "${REPO_ROOT}/build/agent-dock-bell" ./cmd/agent-dock-bell
echo "✓ Go formatting, go vet, and binary compilation passed."

# -----------------------------------------------------------------------------
# GATE 2: Forbidden Pattern Guardrail (Zero AT-SPI / X11 / PTY Write / pkill)
# -----------------------------------------------------------------------------
echo ""
echo "[Gate 2/6] Auditing source code for forbidden performance-degrading patterns..."
FORBIDDEN_REGEX='(gi\.repository|Atspi|libatspi|org\.a11y\.atspi|xprop|libX11|XOpenDisplay|pkill)'
if grep -RInE "${FORBIDDEN_REGEX}" "${REPO_ROOT}/cmd" "${REPO_ROOT}/internal" "${REPO_ROOT}/gnome-extension"; then
    echo "❌ Error: Forbidden pattern detected in source code (violates RULES.md §2)."
    exit 1
fi

# Verify /dev/pts is never opened for writing or held as an open file descriptor
if grep -RInE 'O_WRONLY|O_RDWR|os\.Open\([^)]*tty' "${REPO_ROOT}/cmd" "${REPO_ROOT}/internal"; then
    echo "❌ Error: Direct file open or writable flag on TTY detected in source code."
    exit 1
fi

# Verify legacy Python files are completely purged
if find "${REPO_ROOT}" -maxdepth 3 \( -name "*.py" -o -name "pyproject.toml" -o -name "uv.lock" -o -name ".venv" \) | grep -q .; then
    echo "❌ Error: Legacy Python files detected in repository."
    exit 1
fi
echo "✓ Zero forbidden patterns (AT-SPI, xprop, libX11, pkill, PTY open/write, legacy Python) in source tree."

# -----------------------------------------------------------------------------
# GATE 3: Bundled Audio Asset & GNOME Shell Extension Integrity
# -----------------------------------------------------------------------------
echo ""
echo "[Gate 3/6] Verifying bundled audio asset and GNOME Shell 46 extension..."
ASSET_FILE="${REPO_ROOT}/assets/universfield-new-notification-02-323592.mp3"
if [ ! -f "${ASSET_FILE}" ]; then
    echo "❌ Error: Required bundled audio asset missing: ${ASSET_FILE}"
    exit 1
fi

FILE_SIZE=$(wc -c < "${ASSET_FILE}")
if [ "${FILE_SIZE}" -lt 10000 ]; then
    echo "❌ Error: Audio asset file size is suspiciously small (${FILE_SIZE} bytes): ${ASSET_FILE}"
    exit 1
fi

EXT_DIR="${REPO_ROOT}/gnome-extension/focus-bridge@agent-dock-bell"
if [ ! -f "${EXT_DIR}/metadata.json" ] || [ ! -f "${EXT_DIR}/extension.js" ]; then
    echo "❌ Error: GNOME Shell extension files missing in ${EXT_DIR}"
    exit 1
fi

if ! grep -q '"46"' "${EXT_DIR}/metadata.json"; then
    echo "❌ Error: GNOME Shell extension metadata.json does not declare support for GNOME 46."
    exit 1
fi
echo "✓ Verified audio asset (${FILE_SIZE} bytes) and GNOME Shell 46 extension."

# -----------------------------------------------------------------------------
# GATE 4: Unit & Anti-Regression Test Suite (with -race detector)
# -----------------------------------------------------------------------------
echo ""
echo "[Gate 4/6] Running unit and regression tests with Go race detector..."
go test -race -v ./internal/hook ./internal/audio ./internal/procinfo
echo "✓ Unit and regression tests passed with zero data races."

# -----------------------------------------------------------------------------
# GATE 5: Isolated End-to-End Integration Suite (dbus-run-session)
# -----------------------------------------------------------------------------
echo ""
echo "[Gate 5/6] Running isolated D-Bus & Unix socket end-to-end integration tests..."
if command -v dbus-run-session >/dev/null 2>&1; then
    dbus-run-session -- go test -race -v ./internal/daemon ./cmd/agent-dock-bell
else
    go test -race -v ./internal/daemon ./cmd/agent-dock-bell
fi
echo "✓ Isolated end-to-end integration tests passed."

# -----------------------------------------------------------------------------
# GATE 6: Hook Latency & Resource Budget (< 25 ms round-trip)
# -----------------------------------------------------------------------------
echo ""
echo "[Gate 6/6] Verifying Hook CLI latency budget (< 25 ms)..."
START_NS=$(date +%s%N)
OUT=$("${REPO_ROOT}/build/agent-dock-bell" --dry-run Antigravity <<< '{"hook_event_name":"Stop","fullyIdle":true}')
END_NS=$(date +%s%N)
ELAPSED_MS=$(( (END_NS - START_NS) / 1000000 ))

if [ "${OUT}" != "{}" ]; then
    echo "❌ Error: Hook CLI output was '${OUT}', expected '{}'"
    exit 1
fi

if [ "${ELAPSED_MS}" -gt 25 ]; then
    echo "❌ Error: Hook CLI took ${ELAPSED_MS} ms (budget is <= 25 ms)"
    exit 1
fi
echo "✓ Hook CLI executed in ${ELAPSED_MS} ms (budget <= 25 ms) and returned '{}'."

echo ""
echo "=========================================================="
echo "🎉 ALL 6 QUALITY GATES PASSED CLEANLY!"
echo "=========================================================="
