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

def should_skip_event(raw_stdin: str, payload: Optional[Dict[str, Any]], args: List[str]) -> bool:
    """Inspect stdin payload to skip test runs, subagents, or intermediate tool calls."""
    if "--force" in args:
        return False

    if "--test" in args or "--dry-run" in args:
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
        extra = payload.get("extra")
        if isinstance(extra, dict):
            if "completed" in extra:
                completed = extra.get("completed")
            if "interrupted" in extra:
                interrupted = extra.get("interrupted")
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
        if completed is False or interrupted is True:
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
    from .dock import send_notification, send_terminal_bell
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
        # User is already looking at the terminal window
        PIDLock.kill_existing()
        play_single_sound(sound_file, volume, background=True)
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
