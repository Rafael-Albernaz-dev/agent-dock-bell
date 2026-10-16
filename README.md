# agent-dock-bell 🔔

[![Linux](https://img.shields.io/badge/OS-Linux-FCC624?logo=linux&logoColor=black)](#)
[![Python 3](https://img.shields.io/badge/Python-3.8%2B-blue?logo=python&logoColor=white)](#)
[![CI](https://github.com/Rafael-Albernaz-dev/agent-dock-bell/actions/workflows/ci.yml/badge.svg)](https://github.com/Rafael-Albernaz-dev/agent-dock-bell/actions/workflows/ci.yml)
[![Wayland / X11 / D-Bus](https://img.shields.io/badge/IPC-Wayland%20%7C%20X11%20%7C%20D--Bus-orange)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A focus-aware, zero-friction desktop alert daemon designed for **AI CLI Agents** (Google Antigravity CLI, OpenAI Codex CLI, ChatGPT Desktop App, Hermes Agent, Cursor, Windsurf, and custom developer workflows) on **Ubuntu / Linux (Wayland & X11)**.

When long-running agent reasoning or multi-step tool executions finish, `agent-dock-bell`:
1. 🎵 Plays a subtle, non-intrusive audio chime on a gentle loop.
2. 📌 Sets an unread badge (`"1"`) and urgency hint on your Ubuntu / GNOME Dock icon.
3. 💃 Triggers the native dock icon dance/wiggle animation.
4. 🔔 Displays a native desktop notification with agent name and status. On GNOME Terminal, clicking it focuses the terminal.
5. ⚡ Stops the alarm instantly when the target terminal/app regains focus or detectable TTY input occurs.

No modal confirmation dialogs. No dismiss buttons. Pure keyboard and focus flow.

---

## The Problem: The AI Agent Developer Experience (DX) Dilemma

Autonomous AI coding agents spend anywhere from 10 seconds to several minutes analyzing codebases, orchestrating subagents, running tests, and executing bash commands. 

Developers naturally switch workspaces—to read documentation, review PRs, or check Slack. Existing notification approaches suffer from three fatal flaws:
* **Single Beeps**: Easy to miss if you stepped away or have background music on.
* **Modal Dialogs (Zenity, alert boxes)**: Require grabbing the mouse to click "Dismiss" or "I'm back" before you can type in your terminal.
* **Agent Hook Timeouts**: Many CLI agent frameworks (such as Antigravity and Codex) enforce a strict 30-second timeout on synchronous lifecycle hooks. If an alert script blocks waiting for user input, the agent kills it with `SIGKILL`—bypassing cleanup handlers and leaving the dock permanently wiggling with zombie badges.

---

## The Architecture: Decoupled High-Speed Daemon

`agent-dock-bell` solves this with an asynchronous, decoupled architecture:

```mermaid
flowchart TD
    A[Agent CLI Run Ends\nAntigravity / Codex / Hermes / ChatGPT] -->|Hook Event via stdin JSON| B[agent-dock-bell Launcher]
    
    subgraph Launcher [Detached Hook Launcher]
        B --> C{Active Window\nis Terminal/App?}
        C -->|Yes: User already watching| D[Play Single Chime & Exit 0]
        C -->|No: User is in another app| E[Fork Detached Daemon\nstart_new_session=True]
        E --> F[Print '{}' JSON & Exit 0]
    end

    F -.->|Instant Hook Completion| A

    subgraph Daemon [Detached Background Daemon]
        E --> G[Acquire PID Lock]
        G --> H0{Already Focused\non Startup?}
        H0 -->|Yes| I
        H0 -->|No| H1[Audio Worker: Looped Chime]
        G --> H2[D-Bus: Unity LauncherEntry Badge '1']
        G --> H3[Desktop Notification: notify-send 5s]
        G --> H4[Focus Monitor: Wayland AT-SPI & X11 xprop]
        G --> H5[Activity Monitor: TTY /dev/pts interaction]
        
        H4 -->|User clicks/focuses Terminal or App| I[Trigger Cleanup]
        H5 -->|User types in Terminal| I
        I --> J1[Terminate Audio Process]
        I --> J2[Clear Dock Badge & Urgency]
        I --> J3[Release PID Lock & Terminate]
    end
```

### Engineering Highlights
* **Detached Hook Return**: Returns `{}` immediately after starting the detached daemon. Skipped intermediate events exit in ~40 ms, avoiding agent execution timeouts.
* **Hybrid Wayland & X11 Focus Detection**: Combines event-driven AT-SPI (`gi.repository.Atspi`) with `GLib.MainLoop` and X11/XWayland active window tracking (`_NET_ACTIVE_WINDOW`), ensuring reliable detection across both native Wayland and XWayland environments.
* **Per-Window & Per-Tab TTY Focus Precision**: In multi-window and multi-tab terminals managed by a single server process (such as `gnome-terminal-server`), maps the caller's exact slave PTS (`/dev/pts/N`) to its specific AT-SPI frame and tab (`ACTIVE` + `SELECTED`), with an in-memory `(pid, tty)` cache and inactive-window short-circuit (~2–13 ms) so background tabs/windows still trigger alarms when you are typing in another terminal window.
* **Universal Electron & Modern App Support**: Correctly handles container window states (`ACTIVE` and `FOCUSED`) across Electron-based tools (ChatGPT Desktop, Codex, VS Code, Cursor, Windsurf) and modern terminal emulators (Ghostty, Alacritty, Kitty, Ptyxis, Warp, Tilix, Foot, WezTerm).
* **Instant Startup Check**: Verifies whether the target window already gained focus while the daemon process was forking (~50 ms), completely eliminating false-alarm audio loops.
* **Zero UI Lag / AT-SPI Scoping**: AT-SPI tree inspection is strictly filtered by target process IDs (`term_pids`), eliminating D-Bus accessibility queries to unrelated applications and preventing UI frame drops.
* **Instant Alarm Control CLI (`--stop`)**: Built-in `agent-dock-bell --stop` (or `-k`) command to immediately silence any active background alarm and clear dock urgency badges.
* **Comprehensive System Diagnostics (`--test`)**: Reports environment variables, audio player availability, D-Bus session status, active window ID, and AT-SPI accessibility health.
* **Atomic Kernel Locking**: Daemon exclusivity is managed via `fcntl.flock(LOCK_EX | LOCK_NB)` on the PID file, ensuring race-free concurrency and automatic lock cleanup.
* **Isolated Audio & Notification Sessions**: `AudioLoopWorker` tracks and terminates strictly its own playback process, while one-shot background audio (`pw-play` / `canberra-gtk-play`) and `notify-send` calls spawn in isolated sessions (`start_new_session=True`) so they survive immediate hook exit.
* **Non-fatal Xlib Error Handling**: Installs a safe `XSetErrorHandler` in ctypes libX11 bindings to suppress asynchronous `BadWindow` crashes when windows close dynamically.
* **Process Tree & Ancestor TTY Resolution**: Traverses `/proc/<pid>/status` and `/proc/<pid>/fd` strictly along the caller's ancestor chain to resolve parent terminal processes and exact slave TTYs without cross-window pollution.
* **GNOME Terminal Notification Click**: A session broker keeps the click action available after the hook and alarm daemon exit. When several terminal windows exist, it identifies the source window from its TTY and focuses that window without switching tabs. If the source cannot be identified, the usual notification is shown.
* **Resilient Audio Loop**: Wraps PipeWire (`pw-play`) and Canberra (`canberra-gtk-play`) playback with timeout watchers to prevent device sink hangs.

---

## Repository Structure

```text
agent-dock-bell/
├── .github/workflows/ci.yml       # Automated GitHub Actions test suite
├── pyproject.toml                 # Standard PEP 621 packaging & CLI entrypoints
├── install.sh                     # Zero-dependency installer for ~/.local/bin
├── RULES.md                       # Architecture contracts, anti-gaming rules & quality gates
├── assets/                        # Bundled notification audio assets (.mp3)
├── scripts/
│   ├── run-tests.sh               # Universal test runner (.venv, uv, pytest, unittest)
│   └── quality-gate.sh            # 5-tier automated Quality Gate enforcement
├── src/agent_dock_bell/
│   ├── __init__.py
│   ├── cli.py                     # Detached hook launcher & bounded stdin parsing
│   ├── daemon.py                  # Background daemon lifecycle & PID locking
│   ├── x11.py                     # Wayland AT-SPI & X11 libX11 ctypes bindings
│   ├── dock.py                    # D-Bus Unity LauncherEntry, notifications & terminal bell
│   ├── notification_broker.py     # GNOME Terminal notification actions and focus
│   └── audio.py                   # Looped audio worker with PipeWire & Canberra fallbacks
└── tests/
    ├── test_cli.py                # Agent payload validation (Antigravity, Codex, Hermes)
    ├── test_x11.py                # Hex/int window normalization & ancestry resolution
    ├── test_dock.py               # Desktop notifications & D-Bus badge tests
    ├── test_notification_broker.py # Notification click action tests
    ├── test_audio.py              # Sound command generation & preset resolution
    ├── test_daemon.py             # PID lock lifecycle & cleanup tests
    ├── test_regression.py         # Automated regression test suite (anti-gaming)
    └── test_integration.py        # End-to-end integration test suite
```

### Running Test Suite & Quality Gates

```bash
# Run full test suite (73 tests: Unit + Regression + Integration):
./scripts/run-tests.sh

# Run strict 5-tier Quality Gate verification:
./scripts/quality-gate.sh
```

---

## Supported Agents & Frameworks

| Agent | Hook Event | Integration Point |
| :--- | :--- | :--- |
| **Google Antigravity CLI** | `Stop` | `~/.gemini/config/hooks.json` (validates `fullyIdle: true`) |
| **Codex CLI** | `Stop` | `~/.codex/hooks.json` |
| **Hermes Agent** | `on_session_end` | `~/.hermes/config.yaml` |
| **Custom Bash / CLI** | Trap / Chain | `agent-dock-bell "Task Name"` |

---

## Quick Installation

Clone and install with the included installer:

```bash
git clone https://github.com/your-username/agent-dock-bell.git
cd agent-dock-bell
./install.sh
```

The installer places the binary at `~/.local/bin/agent-dock-bell` and configures convenience wrappers.

---

## Agent Configuration

### 1. Google Antigravity CLI
Add to `~/.gemini/config/hooks.json`:
```json
{
  "prompt-alarm": {
    "Stop": [
      {
        "type": "command",
        "command": "/home/your-user/.local/bin/agent-dock-bell"
      }
    ]
  }
}
```

### 2. Codex CLI
Add to `~/.codex/hooks.json`:
```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/home/your-user/.local/bin/codex-cli-alarm.sh \"Codex CLI\"",
            "timeout": 600
          }
        ]
      }
    ]
  }
}
```

### 3. Hermes Agent
Add to `~/.hermes/config.yaml`:
```yaml
hooks:
  on_session_end:
    - command: "/home/your-user/.local/bin/agent-dock-bell Hermes"
      timeout: 300
```
Then record consent approval:
```bash
hermes hooks list
hermes hooks doctor
```

---

## Customization

You can customize the audio chime and volume using environment variables:

The default sound is the bundled chime:
`assets/universfield-new-notification-02-323592.mp3` (also selectable via presets `"new"` or `"universfield"`).
The resolver automatically searches bundled assets, `~/.local/share/sounds/`, secondary media mounts, and system presets. The terminal hook avoids its separate TTY bell so that it does not play over the configured sound.

```bash
# Available presets: "new" / "universfield" (default), "bell" (Ubuntu Yaru ding), "pop" (instant message), "click" (dry wood tap)
export AGENT_ALARM_SOUND="new"  # Optional override

# Volume level (e.g., 0.5 to 1.5)
export AGENT_ALARM_VOLUME="1.0"

# Maximum audio loop duration before automatic silence (in seconds, default 180)
export AGENT_ALARM_MAX_AUDIO="180"
```

---

## Testing & Quality Gates

Run the comprehensive test suite and automated quality gates:

```bash
# Run full test suite (Unit + Regression + Integration):
./scripts/run-tests.sh

# Run strict 5-tier Quality Gate verification:
./scripts/quality-gate.sh

# Run live self-test diagnostics:
agent-dock-bell --test

# Trigger a forced alert (switch to another window to see it loop and stop on return):
agent-dock-bell --force "Test Run"

# Instantly stop any active alarm daemon, brokers, and clear dock badges:
agent-dock-bell --stop
```

---

## License

MIT License. See [LICENSE](LICENSE) for details.
