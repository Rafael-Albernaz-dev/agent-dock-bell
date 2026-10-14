# agent-dock-bell 🔔

[![Linux](https://img.shields.io/badge/OS-Linux-FCC624?logo=linux&logoColor=black)](#)
[![Python 3](https://img.shields.io/badge/Python-3.8%2B-blue?logo=python&logoColor=white)](#)
[![CI](https://github.com/Rafael-Albernaz-dev/agent-dock-bell/actions/workflows/ci.yml/badge.svg)](https://github.com/Rafael-Albernaz-dev/agent-dock-bell/actions/workflows/ci.yml)
[![Wayland / X11 / D-Bus](https://img.shields.io/badge/IPC-Wayland%20%7C%20X11%20%7C%20D--Bus-orange)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A focus-aware, zero-friction desktop alert daemon designed for **AI CLI Agents** (Google Antigravity CLI, OpenAI Codex CLI, Hermes Agent, and custom developer workflows) on **Ubuntu / Linux (Wayland & X11)**.

When long-running agent reasoning or multi-step tool executions finish, `agent-dock-bell`:
1. 🎵 Plays a subtle, non-intrusive audio chime on a gentle loop.
2. 📌 Sets an unread badge (`"1"`) and urgency hint on your Ubuntu / GNOME Dock icon.
3. 💃 Triggers the native dock icon dance/wiggle animation.
4. 🔔 Displays a native desktop notification (`notify-send`) with agent name and status.
5. ⚡ Stops the alarm when the target terminal regains focus or detectable TTY input occurs.

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
    A[Agent CLI Run Ends\nAntigravity / Codex / Hermes] -->|Hook Event via stdin JSON| B[agent-dock-bell Launcher]
    
    subgraph Launcher [Detached Hook Launcher]
        B --> C{Active Window\nis Terminal?}
        C -->|Yes: User already watching| D[Play Single Chime & Exit 0]
        C -->|No: User is in another app| E[Fork Detached Daemon\nstart_new_session=True]
        E --> F[Print '{}' JSON & Exit 0]
    end

    F -.->|Instant Hook Completion| A

    subgraph Daemon [Detached Background Daemon]
        E --> G[Acquire PID Lock]
        G --> H1[Audio Worker: Looped Chime]
        G --> H2[D-Bus: Unity LauncherEntry Badge '1']
        G --> H3[Desktop Notification: notify-send 5s]
        G --> H4[Focus Monitor: Wayland AT-SPI & X11 xprop]
        G --> H5[Activity Monitor: TTY /dev/pts interaction]
        
        H4 -->|User clicks/focuses Terminal| I[Trigger Cleanup]
        H5 -->|User types in Terminal| I
        I --> J1[Terminate Audio Process]
        I --> J2[Clear Dock Badge & Urgency]
        I --> J3[Release PID Lock & Terminate]
    end
```

### Engineering Highlights
* **Detached Hook Return**: Returns `{}` immediately after starting the detached daemon. Skipped intermediate events exit in ~40 ms, avoiding agent execution timeouts.
* **Wayland & X11 Focus Detection**: Uses event-driven AT-SPI (`gi.repository.Atspi`) with `GLib.MainLoop` and X11 (`_NET_ACTIVE_WINDOW`) in X11 sessions.
* **Zero UI Lag / AT-SPI Scoping**: AT-SPI tree inspection is strictly filtered by terminal PIDs (`term_pids`), eliminating D-Bus accessibility queries to unrelated applications and completely preventing UI stutter/frame drops in Electron apps (such as Codex UI / ChatGPT desktop).
* **Click-to-Focus Notification**: Desktop notifications include the proper Freedesktop `desktop-entry` hint, allowing GNOME Shell to associate the notification with the application and bring the terminal forward when clicked.
* **Atomic Kernel Locking**: Daemon exclusivity is managed via `fcntl.flock(LOCK_EX | LOCK_NB)` on the PID file, ensuring race-free concurrency and automatic lock cleanup even in case of abrupt termination.
* **Isolated Audio Process Management**: `AudioLoopWorker` tracks and terminates strictly its own playback process, avoiding session-wide `pkill` sweeps.
* **Unknown Terminal Fallback**: Plays one chime and sends one notification when no target process or window can be identified, avoiding an alarm that cannot be dismissed by focus.
* **TTY Activity Fallback**: Checks slave TTY access time when available; filesystem access-time policy can limit this signal.
* **Virtualenv Immunity**: Automatically discovers system Python runtimes with accessible GTK/AT-SPI libraries (`get_python_executable`), ensuring hooks invoked from agent venvs (like Hermes) work reliably out of the box.
* **Non-intrusive Desktop Notifications**: Delivers desktop notifications that stay visible and persist in the notification drawer without premature dismissal.
* **Process Tree Resolution**: Traverses `/proc/<pid>/status` to resolve parent terminal emulator processes (such as `gnome-terminal-server`, Tilix, Ptyxis, Alacritty, Kitty) even across tabbed multi-process trees.
* **Scoped Dock Cleanup**: Clears the application's Unity LauncherEntry urgency without changing the global Dash-to-Dock animation setting.
* **Resilient Audio Loop**: Wraps PipeWire (`pw-play`) and Canberra (`canberra-gtk-play`) playback with timeout watchers to prevent device sink hangs.

---

## Repository Structure

```text
agent-dock-bell/
├── .github/workflows/ci.yml       # Automated GitHub Actions test suite
├── pyproject.toml                 # Standard PEP 621 packaging & CLI entrypoints
├── install.sh                     # Zero-dependency installer for ~/.local/bin
├── src/agent_dock_bell/
│   ├── __init__.py
│   ├── cli.py                     # Detached hook launcher & bounded stdin parsing
│   ├── daemon.py                  # Background daemon lifecycle & PID locking
│   ├── x11.py                     # Wayland AT-SPI & X11 libX11 ctypes bindings
│   ├── dock.py                    # D-Bus Unity LauncherEntry, notifications & terminal bell
│   └── audio.py                   # Looped audio worker with PipeWire & Canberra fallbacks
└── tests/
    ├── test_cli.py                # Agent payload validation (Antigravity, Codex, Hermes)
    ├── test_x11.py                # Hex/int window normalization & ancestry resolution
    ├── test_dock.py               # Desktop notifications & D-Bus badge tests
    ├── test_audio.py              # Sound command generation & preset resolution
    └── test_daemon.py             # PID lock lifecycle & cleanup tests
```

### Running Unit Tests

```bash
# Run tests with pytest:
pytest -v
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

```bash
# Available presets: "bell" (Ubuntu Yaru ding), "pop" (instant message), "click" (dry wood tap)
export AGENT_ALARM_SOUND="bell"

# Volume level (e.g., 0.5 to 1.5)
export AGENT_ALARM_VOLUME="1.0"

# Maximum audio loop duration before automatic silence (in seconds, default 180)
export AGENT_ALARM_MAX_AUDIO="180"
```

---

## Testing

To verify the daemon without waiting for an agent run:

```bash
# Run self-test diagnostics:
agent-dock-bell --test

# Trigger a forced alert (switch to another window to see it loop and stop on return):
agent-dock-bell --force "Test Run"

# Instantly stop any active alarm daemon and clear dock badges:
agent-dock-bell --stop
```

---

## License

MIT License. See [LICENSE](LICENSE) for details.
