#!/usr/bin/env bash
# Installer for agent-dock-bell v2 (Go binary + GNOME Shell 46 FocusBridge + systemd --user)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_BIN_DIR="${HOME}/.local/bin"
TARGET_SHARE_DIR="${HOME}/.local/share/agent-dock-bell"
TARGET_SYSTEMD_DIR="${HOME}/.config/systemd/user"
TARGET_EXT_DIR="${HOME}/.local/share/gnome-shell/extensions/focus-bridge@agent-dock-bell"
BIN_TARGET="${TARGET_BIN_DIR}/agent-dock-bell"

echo "=== Running Quality Gates before installation ==="
"${SCRIPT_DIR}/scripts/quality-gate.sh"

echo ""
echo "=== Installing agent-dock-bell v2 ==="
mkdir -p "${TARGET_BIN_DIR}" "${TARGET_SHARE_DIR}" "${TARGET_SYSTEMD_DIR}" "${TARGET_EXT_DIR}"

# 1. Install bundled audio asset to ~/.local/share/agent-dock-bell/
cp -f "${SCRIPT_DIR}/assets/universfield-new-notification-02-323592.mp3" "${TARGET_SHARE_DIR}/"
echo "✓ Installed audio asset to ${TARGET_SHARE_DIR}"

# 2. Install compiled Go binary atomically
cp -f "${SCRIPT_DIR}/build/agent-dock-bell" "${BIN_TARGET}.new"
chmod +x "${BIN_TARGET}.new"
mv -f "${BIN_TARGET}.new" "${BIN_TARGET}"
echo "✓ Installed Go binary to ${BIN_TARGET}"

# 3. Create `dockbell` CLI symlink + compatibility wrappers (agent-alarm.sh, codex-cli-alarm.sh)
ln -sf "${BIN_TARGET}" "${TARGET_BIN_DIR}/dockbell"
ln -sf "${BIN_TARGET}" "${TARGET_BIN_DIR}/agent-alarm.sh"
cat << 'EOF' > "${TARGET_BIN_DIR}/codex-cli-alarm.sh"
#!/usr/bin/env bash
set -u
exec "${HOME}/.local/bin/agent-dock-bell" "${1:-Codex CLI}"
EOF
chmod +x "${TARGET_BIN_DIR}/codex-cli-alarm.sh"
echo "✓ Installed CLI command 'dockbell' and compatibility wrappers (agent-alarm.sh, codex-cli-alarm.sh)"

# 4. Install GNOME Shell 46 FocusBridge extension
cp -f "${SCRIPT_DIR}/gnome-extension/focus-bridge@agent-dock-bell/metadata.json" "${TARGET_EXT_DIR}/"
cp -f "${SCRIPT_DIR}/gnome-extension/focus-bridge@agent-dock-bell/extension.js" "${TARGET_EXT_DIR}/"
if command -v gnome-extensions >/dev/null 2>&1; then
    gnome-extensions enable focus-bridge@agent-dock-bell 2>/dev/null || true
fi
echo "✓ Installed GNOME Shell extension to ${TARGET_EXT_DIR}"

# 5. Install and start systemd --user service with hard resource guardrails
cp -f "${SCRIPT_DIR}/systemd/agent-dock-bell.service" "${TARGET_SYSTEMD_DIR}/agent-dock-bell.service"
systemctl --user daemon-reload
systemctl --user enable agent-dock-bell.service
systemctl --user restart agent-dock-bell.service
sleep 0.2
echo "✓ Enabled and started systemd user service: agent-dock-bell.service"

# 6. Verify runtime status via `dockbell status`
"${TARGET_BIN_DIR}/dockbell" status
echo ""
echo "Installation complete! Use 'dockbell status' or 'dockbell stop' anytime."
