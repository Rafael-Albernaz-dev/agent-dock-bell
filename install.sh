#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR="${HOME}/.local/bin"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_SOURCE="${SCRIPT_DIR}/bin/agent-dock-bell"
BIN_TARGET="${TARGET_DIR}/agent-dock-bell"

echo "=== Installing agent-dock-bell ==="
mkdir -p "${TARGET_DIR}"

# 1. Install executable
cp -f "${BIN_SOURCE}" "${BIN_TARGET}"
chmod +x "${BIN_TARGET}"
echo "✓ Installed executable to ${BIN_TARGET}"

# 2. Maintain legacy alias symlink (for existing configs using agent-alarm.sh)
ln -sf "${BIN_TARGET}" "${TARGET_DIR}/agent-alarm.sh"
echo "✓ Created compatibility symlink: ${TARGET_DIR}/agent-alarm.sh -> agent-dock-bell"

# 3. Create codex helper script if needed
CODEX_WRAPPER="${TARGET_DIR}/codex-cli-alarm.sh"
cat << 'EOF' > "${CODEX_WRAPPER}"
#!/usr/bin/env bash
set -u
exec "${HOME}/.local/bin/agent-dock-bell" "${1:-Codex CLI}"
EOF
chmod +x "${CODEX_WRAPPER}"
echo "✓ Configured Codex wrapper: ${CODEX_WRAPPER}"

# 4. Verify installation
if "${BIN_TARGET}" --test >/dev/null 2>&1; then
  echo "✓ Test run passed cleanly."
else
  echo "⚠ Warning: Test run returned non-zero exit code."
fi

echo ""
echo "=== Integration Guide ==="
echo "1. Google Antigravity CLI (~/.gemini/config/hooks.json):"
echo '   { "prompt-alarm": { "Stop": [{ "type": "command", "command": "'"${BIN_TARGET}"'" }] } }'
echo ""
echo "2. Codex CLI (~/.codex/hooks.json):"
echo '   { "hooks": { "Stop": [{ "hooks": [{ "type": "command", "command": "'"${CODEX_WRAPPER}"' \"Codex CLI\"", "timeout": 600 }] }] } }'
echo ""
echo "3. Hermes Agent (~/.hermes/config.yaml):"
echo '   hooks:'
echo '     on_session_end:'
echo '       - command: "'"${BIN_TARGET}"' Hermes"'
echo '         timeout: 300'
echo ""
echo "Installation complete!"
