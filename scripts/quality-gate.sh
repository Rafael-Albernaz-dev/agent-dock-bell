#!/usr/bin/env bash
# Quality Gate Validator for agent-dock-bell
# Enforces zero-regression standards, real file integrity, and test suite execution.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

export PYTHONPATH="${REPO_ROOT}/src:${PYTHONPATH:-}"

echo "=========================================================="
echo "🛡️  RUNNING AGENT-DOCK-BELL QUALITY GATES"
echo "=========================================================="

# -----------------------------------------------------------------------------
# GATE 1: Syntax & Bytecode Compilation
# -----------------------------------------------------------------------------
echo ""
echo "[Gate 1/5] Checking Python syntax and compilation..."
python3 -m compileall -q src tests
echo "✓ All Python source and test files compiled cleanly."

# -----------------------------------------------------------------------------
# GATE 2: Asset Integrity & Audio File Presence
# -----------------------------------------------------------------------------
echo ""
echo "[Gate 2/5] Verifying bundled audio assets..."
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
echo "✓ Verified audio asset: $(basename "${ASSET_FILE}") (${FILE_SIZE} bytes)"

# -----------------------------------------------------------------------------
# GATE 3: Automated Test Suite (Unit + Regression + Integration)
# -----------------------------------------------------------------------------
echo ""
echo "[Gate 3/5] Executing automated test suite..."
"${REPO_ROOT}/scripts/run-tests.sh"

# -----------------------------------------------------------------------------
# GATE 4: System Diagnostics Verification
# -----------------------------------------------------------------------------
echo ""
echo "[Gate 4/5] Running system diagnostics check..."
DIAG_OUTPUT=$(python3 -m agent_dock_bell.cli --test)
echo "${DIAG_OUTPUT}"

if ! echo "${DIAG_OUTPUT}" | grep -q "universfield-new-notification-02-323592.mp3 (found)"; then
    echo "❌ Error: Diagnostics did not detect the configured new audio file as (found)."
    exit 1
fi

if ! echo "${DIAG_OUTPUT}" | grep -q "All systems operational"; then
    echo "❌ Error: Diagnostics reported non-operational subsystems."
    exit 1
fi
echo "✓ System diagnostics verified clean operation."

# -----------------------------------------------------------------------------
# GATE 5: CLI Lifecycle Verification (--stop / clean shutdown)
# -----------------------------------------------------------------------------
echo ""
echo "[Gate 5/5] Verifying alarm termination lifecycle (--stop)..."
STOP_OUTPUT=$(python3 -m agent_dock_bell.cli --stop)
echo "${STOP_OUTPUT}"
if ! echo "${STOP_OUTPUT}" | grep -q "Stopped active alarms"; then
    echo "❌ Error: --stop command did not output confirmation."
    exit 1
fi
echo "✓ Alarm stop lifecycle validated."

echo ""
echo "=========================================================="
echo "🎉 ALL 5 QUALITY GATES PASSED CLEANLY!"
echo "=========================================================="
