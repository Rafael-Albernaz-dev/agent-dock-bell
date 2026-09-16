# agent-dock-bell 🔔

[![Linux](https://img.shields.io/badge/OS-Linux-FCC624?logo=linux&logoColor=black)](#)
[![Python 3](https://img.shields.io/badge/Python-3.8%2B-blue?logo=python&logoColor=white)](#)
[![X11 / D-Bus](https://img.shields.io/badge/IPC-X11%20%7C%20D--Bus-orange)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A focus-aware, zero-friction desktop alert daemon designed for **AI CLI Agents** (Google Antigravity CLI, OpenAI Codex CLI, Hermes Agent, and custom developer workflows).

When long-running agent reasoning or multi-step tool executions finish, `agent-dock-bell`:
1. 🎵 Plays a subtle, non-intrusive audio chime on a gentle loop.
2. 📌 Sets an unread badge (`"1"`) and urgency hint on your Ubuntu / GNOME Dock icon.
3. 💃 Triggers the native dock icon dance/wiggle animation.
4. ⚡ **Instantly silences and resets everything the exact millisecond you click or switch back to the terminal.**

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
    
    subgraph Launcher [High-Speed Launcher < 50ms]
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
        G --> H3[ctypes / X11: Set XUrgencyHint]
        G --> H4[xprop -spy: Root Window Focus Loop]
        
        H4 -->|User clicks/focuses Terminal| I[Trigger Cleanup]
        I --> J1[Terminate Audio Process]
        I --> J2[Clear Dock Badge & Urgency]
        I --> J3[Reset Dash-to-Dock Animation Actor]
        I --> J4[Close Desktop Notification]
        I --> J5[Release PID Lock & Terminate]
    end
```

### Engineering Highlights
* **`< 50ms` Hook Return**: Exits immediately with `{}` to satisfy agent lifecycle timeouts and prevent `SIGKILL`.
* **Modular Software Design**: Clean separation between D-Bus, X11 ctypes bindings, audio streaming, daemon locking, and CLI logic.
* **Zero External Dependencies**: Built entirely with the Python standard library and POSIX syscalls (`libX11.so.6`).
* **Process Tree Resolution**: Traverses `/proc/<pid>/status` to resolve parent terminal emulator processes (such as `gnome-terminal-server`, Tilix, Ptyxis, Alacritty, Kitty) even across tabbed multi-process trees.
* **Clutter Animation Actor Reset**: Toggles `dash-to-dock`'s `dance-urgent-applications` setting on cleanup to ensure GNOME Shell Clutter timelines do not linger after focus is restored.
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
│   ├── cli.py                     # High-speed hook launcher (<50ms) & stdin parsing
│   ├── daemon.py                  # Background daemon lifecycle & PID locking
│   ├── x11.py                     # libX11 ctypes bindings, XUrgencyHint & window focus
│   ├── dock.py                    # D-Bus Unity LauncherEntry, notifications & Clutter reset
│   └── audio.py                   # Looped audio worker with PipeWire & Canberra fallbacks
└── tests/
    ├── test_cli.py                # Agent payload validation (Antigravity, Codex, Hermes)
    ├── test_x11.py                # Hex/int window normalization & ancestry resolution
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
```

---

## License

MIT License. See [LICENSE](LICENSE) for details.
