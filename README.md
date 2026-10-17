# agent-dock-bell (`dockbell`) 🔔

[![Linux](https://img.shields.io/badge/OS-Linux%20%28GNOME%2045%2B%29-FCC624?logo=linux&logoColor=black)](#)
[![Go](https://img.shields.io/badge/Go-1.23%2B-00ADD8?logo=go&logoColor=white)](#)
[![CI](https://github.com/Rafael-Albernaz-dev/agent-dock-bell/actions/workflows/ci.yml/badge.svg)](https://github.com/Rafael-Albernaz-dev/agent-dock-bell/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

An ultra-lightweight, zero-polling desktop alert daemon in **Go + GNOME Shell 46** designed for **AI CLI Agents** (Google Antigravity CLI, OpenAI Codex CLI, Hermes Agent) on Linux.

Built to eliminate multi-project procrastination with **0% idle CPU** and **< 0.5% active OS/terminal overhead**:
1. **Already looking at the terminal?** Plays a single subtle chime (`1x`) and suppresses visual notification popups.
2. **Working in another window?**
   - Displays a native desktop notification (`org.freedesktop.Notifications`) with click-to-focus.
   - Plays a gentle looped chime (`pw-play` every 3s, capped at 120s).
   - **Stops immediately** when you click the notification (which raises/activates your terminal window), focus the terminal window (`Alt+Tab` / click), close the terminal tab, or type in the terminal.

---

## Architecture Highlights

```mermaid
flowchart LR
    subgraph CLI ["1. Agent Hook Client (< 5 ms)"]
        H["Antigravity / Codex / Hermes"] -->|stdin JSON| C["dockbell / agent-dock-bell"]
    end

    subgraph Daemon ["2. User Daemon (systemd --user)"]
        C -->|"Unix Socket\n$XDG_RUNTIME_DIR/agent-dock-bell.sock"| D["dockbell daemon"]
        D -->|"D-Bus org.freedesktop.Notifications"| N["GNOME Shell Notification Banner"]
        D -->|"Managed child PID"| A["pw-play (3s loop, 120s cap)"]
        D -->|"Read-only stat(st_atime)"| T["/dev/pts/N"]
    end

    subgraph Extension ["3. GNOME Shell 46 Extension"]
        E["focus-bridge@agent-dock-bell\n(notify::focus-window)"] -->|"D-Bus Signal: FocusChanged(pid, wmClass)"| D
        D -->|"D-Bus Call: ActivateWindow(pid)\nGetFocusedPID()"| E
    end
```

- **Single Static Go Binary (`dockbell` / `agent-dock-bell`)**: Hook invocations complete in **~2–10 ms** over a Unix domain socket (`$XDG_RUNTIME_DIR/agent-dock-bell.sock`) and never block agent execution.
- **Zero GNOME Terminal Interference**: Zero AT-SPI accessibility queries, zero X11 polling (`xprop`), and zero open file descriptors or ANSI writes to `/dev/pts/*`.
- **Hard Kernel Resource Guardrails**: Managed by `systemd --user` (`agent-dock-bell.service`) with `CPUQuota=1%`, `MemoryMax=25M`, and `TasksMax=16`.
- **Deterministic Structured Hook Filter**: Parses full stdin JSON payloads up to `10 MB` without fragile raw-text substring heuristics.

See [`ARCHITECTURE.md`](ARCHITECTURE.md) and [`RULES.md`](RULES.md) for full specifications.

---

## Installation & Quick Commands (`dockbell`)

```bash
./install.sh
```

### CLI Commands

```bash
# Check daemon health, audio asset, caller terminal PID/TTY, and FocusBridge status
dockbell status

# Immediately silence any active alarm
dockbell stop

# Inspect systemd service & structured JSON logs
systemctl --user status agent-dock-bell.service
journalctl --user -u agent-dock-bell.service -f
```

---

## Mandatory Quality Gates & Testing

Run the 6-stage quality gate suite (static analysis, forbidden-pattern guardrails, `-race` unit/regression tests, isolated `dbus-run-session` end-to-end integration tests, and `< 25 ms` latency verification):

```bash
./scripts/quality-gate.sh
```
