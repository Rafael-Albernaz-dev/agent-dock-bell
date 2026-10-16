"""
CLI entrypoint and detached alarm launcher.
"""

import os
import select
import sys
import time
from typing import Any, Dict, List, Optional

def get_python_executable() -> str:
    """Return a python executable that has access to system libraries (like gi)."""
    try:
        import gi  # noqa: F401
        return sys.executable
    except ImportError:
        for candidate in ["/usr/bin/python3", "/usr/local/bin/python3"]:
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return sys.executable

def build_daemon_environment() -> Dict[str, str]:
    """Ensure a detached daemon can import the package from this checkout."""
    from pathlib import Path
    env = os.environ.copy()
    source_root = str(Path(__file__).resolve().parents[1])
    python_paths = [path for path in env.get("PYTHONPATH", "").split(os.pathsep) if path]

    if source_root not in python_paths:
        python_paths.insert(0, source_root)
    env["PYTHONPATH"] = os.pathsep.join(python_paths)
    return env

def is_chatgpt_environment(
    args: Optional[List[str]] = None,
    payload: Optional[Dict[str, Any]] = None,
    raw_stdin: str = "",
) -> bool:
    """
    Detect whether agent-dock-bell was invoked from or within ChatGPT Desktop App / ChatGPT UI.
    ChatGPT UI already has its own completion sound and notifications, so agent-dock-bell
    should never alert or fork daemons when running in this environment.
    """
    if args:
        for arg in args:
            if not arg.startswith("--") and "chatgpt" in arg.lower():
                return True

    chrome_desktop = os.environ.get("CHROME_DESKTOP", "").lower()
    if "chatgpt" in chrome_desktop:
        return True

    gio_app = os.environ.get("GIO_LAUNCHED_DESKTOP_FILE", "").lower()
    if "chatgpt" in gio_app:
        return True

    originator = os.environ.get("CODEX_INTERNAL_ORIGINATOR_OVERRIDE", "").lower()
    if "chatgpt" in originator or originator == "codex desktop":
        return True

    cli_path = os.environ.get("CODEX_CLI_PATH", "").lower()
    if "/usr/lib/chatgpt" in cli_path or "chatgpt" in cli_path:
        return True

    if "CODEX_APP_TOOLS_PIPE_PATH" in os.environ:
        return True

    if "BROWSER_USE_CODEX_APP_VERSION" in os.environ or "BROWSER_USE_CODEX_APP_BUILD_FLAVOR" in os.environ:
        return True

    for key in os.environ:
        if key.startswith("CHATGPT_") or key == "CHATGPT_DESKTOP":
            return True

    if payload and isinstance(payload, dict):
        orig = str(payload.get("originator") or payload.get("client") or payload.get("app") or "").lower()
        if "chatgpt" in orig or orig == "codex desktop":
            return True
        extra = payload.get("extra")
        if isinstance(extra, dict):
            extra_orig = str(extra.get("originator") or extra.get("client") or extra.get("app") or "").lower()
            if "chatgpt" in extra_orig or extra_orig == "codex desktop":
                return True

    if raw_stdin and ("chatgpt" in raw_stdin.lower() or "codex desktop" in raw_stdin.lower()):
        return True

    try:
        from .x11 import get_ancestor_pids
        ancestors = get_ancestor_pids()
        for pid in ancestors:
            comm = ""
            comm_path = f"/proc/{pid}/comm"
            if os.path.exists(comm_path):
                with open(comm_path, "r", errors="ignore") as f:
                    comm = f.read().strip().lower()
            if "chatgpt" in comm:
                return True
            cmdline_path = f"/proc/{pid}/cmdline"
            if os.path.exists(cmdline_path):
                with open(cmdline_path, "rb") as f:
                    cmdline = f.read().lower()
                if b"/usr/lib/chatgpt" in cmdline or b"chatgpt" in cmdline:
                    return True
    except Exception:
        pass

    return False

def _identify_agent_family(comm: str, cmdline: bytes) -> Optional[str]:
    comm_low = comm.lower().strip()
    cmdline_low = cmdline.lower()

    # Skip alarm scripts/wrappers so they are not treated as the AI agent itself
    for wrapper in (b"agent-dock-bell", b"agent-alarm", b"codex-cli-alarm", b"agent_dock_bell"):
        if wrapper in cmdline_low:
            return None

    # Check Hermes
    if comm_low == "hermes" or comm_low.startswith("hermes"):
        return "hermes"
    if b"/hermes" in cmdline_low or b"hermes-agent" in cmdline_low or b"/.hermes/" in cmdline_low:
        parts = cmdline_low.split(b"\0")
        if parts:
            first_part = parts[0]
            second_part = parts[1] if len(parts) > 1 else b""
            if b"hermes" in first_part or b"hermes" in second_part:
                return "hermes"

    # Check Codex
    if comm_low == "codex" or comm_low.startswith("codex"):
        return "codex"
    if b"/codex" in cmdline_low or b"codex-exec" in cmdline_low:
        parts = cmdline_low.split(b"\0")
        if parts:
            first_part = parts[0]
            second_part = parts[1] if len(parts) > 1 else b""
            if b"codex" in first_part or b"codex" in second_part:
                return "codex"

    # Check Antigravity / AGY
    if comm_low in ("antigravity", "agy") or comm_low.startswith("antigravity"):
        return "antigravity"
    if b"/antigravity" in cmdline_low or b"/agy" in cmdline_low or b"antigravity-cli" in cmdline_low:
        parts = cmdline_low.split(b"\0")
        if parts:
            first_part = parts[0]
            second_part = parts[1] if len(parts) > 1 else b""
            if b"antigravity" in first_part or b"agy" in first_part or b"antigravity" in second_part or b"agy" in second_part:
                return "antigravity"

    # Check ChatGPT
    if comm_low == "chatgpt":
        return "chatgpt"
    if b"/usr/lib/chatgpt" in cmdline_low or b"chatgpt" in cmdline_low:
        parts = cmdline_low.split(b"\0")
        if parts and (b"chatgpt" in parts[0] or (len(parts) > 1 and b"chatgpt" in parts[1])):
            return "chatgpt"

    # Check Cursor / Windsurf / Aider / Claude
    for name in ("cursor", "windsurf", "aider", "claude"):
        name_b = name.encode("ascii")
        if comm_low == name or comm_low.startswith(name):
            return name
        parts = cmdline_low.split(b"\0")
        if parts and (name_b in parts[0] or (len(parts) > 1 and name_b in parts[1])):
            return name

    return None

def is_nested_agent_execution(
    args: Optional[List[str]] = None,
    payload: Optional[Dict[str, Any]] = None,
    raw_stdin: str = "",
) -> bool:
    """
    Detect whether agent-dock-bell was invoked from a nested agent execution
    (e.g., Codex CLI executed as a tool under Hermes or Antigravity, or child subagents).
    Nested subordinate runs must never trigger completion alarms or fork daemons.
    """
    # 1. Environment markers
    if os.environ.get("HERMES_DELEGATED_CHILD_CONTEXT"):
        return True
    if "CODEX_APP_TOOLS_PIPE_PATH" in os.environ:
        return True
    if os.environ.get("ANTIGRAVITY_SUBAGENT") or os.environ.get("SUBAGENT_ID"):
        return True

    # 2. Payload markers
    if payload and isinstance(payload, dict):
        if payload.get("tool_name"):
            return True
        extra = payload.get("extra")
        if isinstance(extra, dict):
            if extra.get("tool_name"):
                return True
            task_id = str(extra.get("task_id") or "")
            if task_id.startswith(("bg-", "subagent", "review-", "cron-", "heartbeat-", "task-sub-")):
                return True
            if extra.get("failed") is True:
                return True
            if extra.get("interrupted") is True:
                return True
            if extra.get("completed") is False:
                return True
            turn_exit_reason = extra.get("turn_exit_reason")
            if turn_exit_reason:
                r_low = str(turn_exit_reason).strip().lower()
                if r_low.startswith((
                    "guardrail", "halt", "budget", "exhaust", "interrupt",
                    "error", "timeout", "max_iterations", "session_persistence",
                    "local_processing", "repeated_outer"
                )):
                    return True
                if not (
                    r_low.startswith("text_response(")
                    or r_low.startswith("partial_stream")
                    or r_low.startswith("fallback_prior")
                    or r_low in ("stop", "end_turn", "completed")
                ):
                    return True

        task_id = str(payload.get("task_id") or "")
        if task_id.startswith(("bg-", "subagent", "review-", "cron-", "heartbeat-", "task-sub-")):
            return True

        if payload.get("failed") is True:
            return True
        if payload.get("interrupted") is True:
            return True
        if payload.get("completed") is False:
            return True

        turn_exit_reason = payload.get("turn_exit_reason")
        if turn_exit_reason:
            r_low = str(turn_exit_reason).strip().lower()
            if r_low.startswith((
                "guardrail", "halt", "budget", "exhaust", "interrupt",
                "error", "timeout", "max_iterations", "session_persistence",
                "local_processing", "repeated_outer"
            )):
                return True
            if not (
                r_low.startswith("text_response(")
                or r_low.startswith("partial_stream")
                or r_low.startswith("fallback_prior")
                or r_low in ("stop", "end_turn", "completed")
            ):
                return True

        if (
            payload.get("parent_session_id")
            or payload.get("parentSessionId")
            or payload.get("parent_id")
            or payload.get("child_role")
            or payload.get("is_subagent")
            or payload.get("is_background")
        ):
            return True

    # 3. Process Ancestry Hierarchy
    try:
        from .x11 import get_ancestor_process_chain
        chain = get_ancestor_process_chain()
    except Exception:
        chain = []

    if chain:
        agent_nodes = []
        for pid, comm, cmdline in chain:
            fam = _identify_agent_family(comm, cmdline)
            if fam:
                agent_nodes.append((pid, fam))

        if agent_nodes:
            reporting_family = None
            if args:
                for arg in args:
                    if not arg.startswith("--"):
                        arg_low = arg.lower()
                        for fam in ("hermes", "codex", "antigravity", "chatgpt", "cursor", "windsurf", "aider", "claude"):
                            if fam in arg_low:
                                reporting_family = fam
                                break
                        if reporting_family:
                            break

            if reporting_family:
                for pid, fam in agent_nodes:
                    if fam != reporting_family:
                        return True
                if len(agent_nodes) > 1:
                    return True
            else:
                if len(agent_nodes) > 1:
                    return True

    return False

def should_skip_event(raw_stdin: str, payload: Optional[Dict[str, Any]], args: List[str]) -> bool:
    """Inspect stdin payload to skip test runs, subagents, or intermediate tool calls."""
    if "--force" in args:
        return False

    if "--test" in args or "--dry-run" in args:
        return True

    # ChatGPT UI has its own completion sounds and notifications; skip alerts entirely
    if is_chatgpt_environment(args, payload, raw_stdin):
        return True

    # Nested agent execution (e.g. Codex running under Hermes, subagents, child tasks)
    if is_nested_agent_execution(args, payload, raw_stdin):
        return True

    if raw_stdin:
        if "test-session" in raw_stdin or "synthetic" in raw_stdin:
            return True
        if payload is None and raw_stdin.strip().startswith("{"):
            return True

    if isinstance(payload, dict):
        # 1. Antigravity CLI Stop Hook:
        if payload.get("fullyIdle") is False:
            return True

        # 2. Skip subagent or background review turns
        if (
            payload.get("parent_session_id")
            or payload.get("parentSessionId")
            or payload.get("parent_id")
            or payload.get("child_role")
            or payload.get("is_subagent")
            or payload.get("is_background")
        ):
            return True

        origin = payload.get("write_origin") or payload.get("write_context")
        if origin in ("background_review", "review"):
            return True

        # 3. Hermes and Codex lifecycle hooks:
        event_name = (
            payload.get("hook_event_name")
            or payload.get("hookEventName")
            or payload.get("hook-event-name")
        )
        if event_name and event_name not in ("on_session_end", "Stop", "stop"):
            return True
        completed = payload.get("completed")
        interrupted = payload.get("interrupted")
        failed = payload.get("failed")
        extra = payload.get("extra")
        if isinstance(extra, dict):
            if "completed" in extra:
                completed = extra.get("completed")
            if "interrupted" in extra:
                interrupted = extra.get("interrupted")
            if "failed" in extra:
                failed = extra.get("failed")
            if (
                extra.get("parent_session_id")
                or extra.get("parentSessionId")
                or extra.get("parent_id")
                or extra.get("child_role")
                or extra.get("is_subagent")
                or extra.get("is_background")
                or extra.get("write_origin") in ("background_review", "review")
                or extra.get("write_context") in ("background_review", "review")
            ):
                return True
        if completed is False or interrupted is True or failed is True:
            return True

        # 4. Codex CLI Stop Hook:
        stop_reason = (
            payload.get("stop_reason")
            or payload.get("stopReason")
            or payload.get("stop-reason")
        )
        if stop_reason in ("tool_use", "intermediate"):
            return True

    return False

def read_hook_stdin(timeout: float = 0.2) -> tuple:
    """Read a small complete JSON payload without waiting for pipe EOF."""
    if sys.stdin.isatty():
        return "", None
    try:
        import fcntl
        fd = sys.stdin.fileno()
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    except (OSError, ValueError):
        return "", None

    data = bytearray()
    deadline = time.monotonic() + timeout
    try:
        while len(data) < 65536:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ready, _, _ = select.select([fd], [], [], remaining)
            if not ready:
                break
            chunk = os.read(fd, min(8192, 65536 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            try:
                import json
                raw = data.decode("utf-8")
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    return raw, payload
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
    except OSError:
        pass
    finally:
        fcntl.fcntl(fd, fcntl.F_SETFL, flags)
    return data.decode("utf-8", "replace"), None

def main() -> None:
    args = sys.argv[1:]

    # --------------------------------------------------------------------------
    # 0. Stop/Kill Daemon Mode
    # --------------------------------------------------------------------------
    if any(arg in args for arg in ("--stop", "--kill", "-k", "-s")):
        from .daemon import PIDLock
        from .dock import set_dock_badge
        from .x11 import close_display
        PIDLock.kill_existing()
        for d_id in ("org.gnome.Terminal.desktop", "chatgpt.desktop", "code.desktop", "cursor.desktop"):
            try:
                set_dock_badge(d_id, 0, urgent=False)
            except Exception:
                pass
        try:
            close_display()
        except Exception:
            pass
        try:
            import subprocess
            subprocess.run(
                ["pkill", "-u", str(os.getuid()), "-f", "agent_dock_bell.notification_broker"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass
        print("Stopped active alarms.")
        sys.exit(0)

    # --------------------------------------------------------------------------
    # 0.1 Self-test Diagnostics Mode
    # --------------------------------------------------------------------------
    if "--test" in args:
        from .audio import resolve_sound_file
        from .x11 import find_terminal_windows, get_active_window_id, get_ancestor_pids
        print("=== agent-dock-bell diagnostics ===")
        print(f"Session: {os.environ.get('XDG_SESSION_TYPE', 'unknown')} (DISPLAY={os.environ.get('DISPLAY', 'none')}, WAYLAND={os.environ.get('WAYLAND_DISPLAY', 'none')})")
        sound = resolve_sound_file()
        print(f"Audio file: {sound} ({'found' if os.path.exists(sound) else 'missing'})")
        ancestors = get_ancestor_pids()
        wids, pids, desktop_id = find_terminal_windows(ancestors)
        print(f"Terminal detected: desktop_id={desktop_id}, pids={pids}, wids={wids}")
        active_wid = get_active_window_id()
        print(f"Active window: {active_wid}")
        atspi_ok = False
        try:
            import gi
            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi
            Atspi.init()
            atspi_ok = True
        except Exception:
            atspi_ok = False
        print(f"AT-SPI initialized: {atspi_ok}")
        print("✓ All systems operational.")
        sys.exit(0)

    # --------------------------------------------------------------------------
    # 1. Daemon Execution Mode
    # --------------------------------------------------------------------------
    if "--daemon" in args:
        from .audio import resolve_sound_file
        from .daemon import run_daemon
        from .x11 import find_terminal_windows, get_ancestor_pids, get_ancestor_ttys, to_int
        d_idx = args.index("--daemon")
        agent_name = args[d_idx + 1] if len(args) > d_idx + 1 else "AI Agent"
        desktop_id = args[d_idx + 2] if len(args) > d_idx + 2 else "org.gnome.Terminal.desktop"
        wids_raw = args[d_idx + 3] if len(args) > d_idx + 3 else ""
        pids_raw = args[d_idx + 4] if len(args) > d_idx + 4 else ""
        ttys_raw = args[d_idx + 5] if len(args) > d_idx + 5 else ""

        term_wids = {to_int(w) for w in wids_raw.split(",") if to_int(w) is not None}
        term_pids = {to_int(p) for p in pids_raw.split(",") if to_int(p) is not None}
        term_ttys = {t.strip() for t in ttys_raw.split(",") if t.strip()}

        if not term_wids and not term_pids:
            ancestors = get_ancestor_pids()
            wids, pids, d_id = find_terminal_windows(ancestors)
            term_wids = wids
            term_pids = pids
            if not term_ttys:
                term_ttys = get_ancestor_ttys(ancestors)
            if d_id:
                desktop_id = d_id

        if not term_wids and not term_pids:
            return

        if not ("--force" in args) and (
            desktop_id == "chatgpt.desktop"
            or is_chatgpt_environment(args)
            or is_nested_agent_execution(args)
        ):
            sys.exit(0)
            return

        sound_file = resolve_sound_file()
        volume = os.environ.get("AGENT_ALARM_VOLUME", "1.0").strip()
        max_audio = int(os.environ.get("AGENT_ALARM_MAX_AUDIO", "180"))
        max_wait = int(os.environ.get("AGENT_ALARM_MAX_WAIT", "600"))

        run_daemon(agent_name, desktop_id, term_wids, term_pids, sound_file, volume, max_audio, max_wait, term_ttys=term_ttys)
        return

    # --------------------------------------------------------------------------
    # 2. Hook launcher
    # --------------------------------------------------------------------------
    raw_stdin, payload = read_hook_stdin()

    if should_skip_event(raw_stdin, payload, args):
        print("{}")
        sys.exit(0)

    from .audio import play_single_sound, resolve_sound_file
    from .daemon import PIDLock
    from .dock import send_notification, send_terminal_bell, set_dock_badge
    import subprocess
    from .x11 import (
        find_terminal_windows, get_active_window_id, get_ancestor_pids,
        get_ancestor_ttys, is_terminal_focused, to_int,
    )

    # Determine Agent Name
    agent_name = "AI Agent"
    for arg in args:
        if not arg.startswith("--"):
            agent_name = arg.strip()
            break

    ancestors = get_ancestor_pids()
    term_wids, term_pids, desktop_id = find_terminal_windows(ancestors)
    term_ttys = get_ancestor_ttys(ancestors)
    if not term_ttys and term_pids:
        term_ttys = get_ancestor_ttys(term_pids)

    if desktop_id == "chatgpt.desktop" and not ("--force" in args):
        print("{}")
        sys.exit(0)

    # The TTY bell is audible in GNOME Terminal and would play the old chime
    # alongside the configured sound. Dock urgency still requests attention.
    if desktop_id != "org.gnome.Terminal.desktop":
        send_terminal_bell(term_ttys)

    current_active = get_active_window_id()
    current_active_int = to_int(current_active)

    is_already_active = is_terminal_focused(
        term_wids, term_pids, current_active_int, term_ttys=term_ttys
    )

    sound_file = resolve_sound_file()
    volume = os.environ.get("AGENT_ALARM_VOLUME", "1.0").strip()

    if not term_wids and not term_pids:
        # Without an identified window/process, a persistent alarm cannot be
        # dismissed reliably when the user returns to the terminal.
        play_single_sound(sound_file, volume, background=True)
        send_notification(agent_name, desktop_id, wait=False, term_pids=term_pids, term_ttys=term_ttys)
        print("{}")
        sys.exit(0)

    if is_already_active and not ("--force" in args):
        # User is already looking at the terminal window: suppress visual notifications
        PIDLock.kill_existing()
        play_single_sound(sound_file, volume, background=True)
        if os.environ.get("AGENT_ALARM_NOTIFY_ACTIVE", "0").strip() in ("1", "true", "yes"):
            send_notification(agent_name, desktop_id, wait=False, term_pids=term_pids, term_ttys=term_ttys)
        print("{}")
        sys.exit(0)


    # User is away / focused on another window: Fork detached background daemon
    PIDLock.kill_existing()

    wids_arg = ",".join(str(w) for w in term_wids)
    pids_arg = ",".join(str(p) for p in term_pids)
    ttys_arg = ",".join(term_ttys)
    daemon_cmd = [
        get_python_executable(),
        "-m",
        "agent_dock_bell.cli",
        "--daemon",
        agent_name,
        desktop_id,
        wids_arg,
        pids_arg,
        ttys_arg,
    ]

    subprocess.Popen(
        daemon_cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
        env=build_daemon_environment(),
    )

    print("{}")
    sys.exit(0)

if __name__ == "__main__":
    main()
