# Architecture Rules, Guardrails & Quality Gates (`agent-dock-bell` v2)

This document defines the non-negotiable engineering rules, OS/terminal performance guardrails, anti-regression constraints, and mandatory quality gates for `agent-dock-bell`.

---

## 1. Core Scope & Purpose

`agent-dock-bell` is an ultra-lightweight anti-procrastination alert daemon for multi-agent CLI workflows (Google Antigravity CLI, OpenAI Codex CLI, Hermes Agent).

When an agent completes a turn:
1. If the user is **already focused** on that terminal window: play a single subtle chime (`1x`) in the background and show **no** notification banner.
2. If the user is **focused on another window**:
   - Display a native desktop notification (`org.freedesktop.Notifications`) with a default click action.
   - Play a gentle looped chime (`pw-play` every 3 seconds, hard-capped at `120s`).
   - Stop the looped chime **immediately** when:
     - The terminal window regains focus (via GNOME Shell `FocusBridge` signal or TTY input activity), **or**
     - The user clicks the notification (which activates/raises the terminal window and stops the alarm), **or**
     - The user dismisses the notification or runs `dockbell stop` (`agent-dock-bell --stop`).

---

## 2. Hard Performance & Safety Guardrails (< 0.5% OS/Terminal Impact)

> [!CAUTION]
> The following patterns caused desktop freezes, terminal input lag, and silent regressions in v1 and are **strictly forbidden** in this repository. Gate 2 of `./scripts/quality-gate.sh` fails the build automatically if any of these appear in source code.

1. **FORBIDDEN: AT-SPI (`libatspi`, `gi.repository.Atspi`, `org.a11y.atspi`)**
   - Never query or subscribe to AT-SPI accessibility trees. Traversing accessibility nodes blocks the main GTK thread of `gnome-terminal-server` and triggers global D-Bus event storms across Chrome/Electron/VS Code.
2. **FORBIDDEN: Writing to User Pseudo-Terminals (`/dev/pts/*`)**
   - Never open `/dev/pts/*` with `O_WRONLY` or `O_RDWR`, and never write OSC title markers or BEL characters to a user's terminal.
   - `/dev/pts/*` may only be inspected via read-only `os.Stat()` (`st_atime`) to detect user keystrokes.
3. **FORBIDDEN: X11 Polling (`xprop`, `libX11`) & Broad Process Sweeps (`pkill`)**
   - Never spawn `xprop -spy` or link against `libX11`.
   - Never run `pkill` or kill processes by command-line substring matching. The daemon owns its child `pw-play` PID directly and terminates only that process.
4. **FORBIDDEN: Raw Stdin Substring Filtering**
   - Never inspect raw stdin strings for substrings like `"chatgpt"`, `"synthetic"`, `"test-session"`, or `"claude"`. Conversational text in agent transcripts frequently contains these words.
   - Hook filtering MUST parse structured JSON fields only, reading full stdin up to `10 MB` (never truncating at `64 KB`).
5. **Hard Resource & Latency Budgets**
   - **Hook CLI latency**: `< 25 ms` typical, `50 ms` hard timeout. A hook must never block an AI agent or fail with a non-zero exit code.
   - **Systemd cgroup limits**: `CPUQuota=1%`, `MemoryMax=25M`, `TasksMax=16`.
   - **Audio loop cap**: Every alarm loop MUST enforce a `context.WithTimeout` ceiling (default `120s`).

---

## 3. Deterministic Hook Filtering Contract

The hook parser (`internal/hook`) evaluates only top-level structured JSON fields:

| Agent / Hook Format | Trigger Condition | Skip Condition (returns `{}` without alerting) |
|---|---|---|
| **Antigravity CLI** (`Stop`) | `hook_event_name == "Stop"` (or omitted) and `fullyIdle != false` | `fullyIdle == false`, `is_subagent == true`, `is_background == true`, non-empty `parent_session_id` / `child_role` |
| **Codex CLI** (`Stop`) | `stop_reason` not in `("tool_use", "intermediate")` | `stop_reason` in `("tool_use", "intermediate")`, `tool_name != ""`, subagent/background task markers |
| **Hermes Agent** (`on_session_end`) | `completed != false` and `interrupted != true` and `failed != true` (includes `stop`, `end_turn`, `max_tokens`, `text_response(...)`) | `completed == false`, `interrupted == true`, `failed == true`, `HERMES_DELEGATED_CHILD_CONTEXT != ""` |

Every decision (`trigger` or `skip`) is logged with a structured reason by the daemon so silent failures are impossible.

---

## 4. Anti-Gaming Testing Policy

> [!IMPORTANT]
> **Zero Mock-Gaming.**
> Unit tests verify pure domain logic (`internal/hook`, `internal/audio`). Integration tests (`internal/daemon`) MUST run against a real isolated `dbus-run-session` bus, a real Unix domain socket, and the compiled `agent-dock-bell` binary.

1. **No Host Desktop Side Effects in Tests**:
   - Running `./scripts/quality-gate.sh` or `go test ./...` MUST NEVER kill alarms on the developer's active desktop session, emit notifications to the user's real GNOME Shell, or depend on `~/.local/bin/agent-dock-bell` being installed.
2. **Real Diagnostics (`--test` / `--status`)**:
   - Diagnostics must verify actual runtime state and never print unconditional success strings when subsystems fail.

---

## 5. Mandatory 6 Quality Gates (`./scripts/quality-gate.sh`)

All 6 gates must pass before any commit or release:

| Gate | Name | Verification |
|---|---|---|
| **Gate 1** | Static Analysis & Formatting | `gofmt -s -l .` (zero unformatted files) + `go vet ./...` |
| **Gate 2** | Forbidden Pattern Guardrail | Fails if source code references `atspi`, `xprop`, `pkill`, `libX11`, or writable `/dev/pts` |
| **Gate 3** | Bundled Audio & GNOME Extension Integrity | Verifies MP3 header/size in `assets/` and syntax/schema of `gnome-extension/` |
| **Gate 4** | Unit & Regression Suite (`-race`) | `go test -race` on hook parser (including `>100KB` payloads, `"ChatGPT"`/`"synthetic"` text, `max_tokens`) and audio lifecycle |
| **Gate 5** | Isolated D-Bus & Socket End-to-End Integration | Runs inside `dbus-run-session`: verifies Hook CLI -> Unix Socket -> Daemon -> D-Bus Notification -> Audio Loop -> `FocusChanged` / `ActionInvoked` -> Window Activation & Instant Stop |
| **Gate 6** | Hook Performance & Latency Budget | Verifies compiled CLI round-trip execution completes in `< 25 ms` and exits `0` with `{}` output |
