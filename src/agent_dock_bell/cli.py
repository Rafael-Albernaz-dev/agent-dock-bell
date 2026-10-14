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
    if "--test" in args or "--dry-run" in args:
        return True

    if raw_stdin:
        if "test-session" in raw_stdin or "synthetic" in raw_stdin:
            return True
        if payload is None:
            return True

    if isinstance(payload, dict):
        # 1. Antigravity CLI Stop Hook:
        if payload.get("fullyIdle") is False:
            return True

        # 2. Hermes and Codex lifecycle hooks:
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
        if completed is False or interrupted is True:
            return True

        # 3. Codex CLI Stop Hook:
        stop_reason = (
            payload.get("stop_reason")
            or payload.get("stopReason")
            or payload.get("stop-reason")
        )
        if stop_reason in ("tool_use", "intermediate"):
            return True

    return False

def read_hook_stdin(timeout: float = 0.02) -> tuple:
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
    term_ttys = get_ancestor_ttys(ancestors | term_pids)

    # A bell may request window attention when supported by the terminal.
    send_terminal_bell(term_ttys)

    current_active = get_active_window_id()
    current_active_int = to_int(current_active)

    is_already_active = is_terminal_focused(term_wids, term_pids, current_active_int)

    sound_file = resolve_sound_file()
    volume = os.environ.get("AGENT_ALARM_VOLUME", "1.0").strip()

    if not term_wids and not term_pids:
        # Without an identified window/process, a persistent alarm cannot be
        # dismissed reliably when the user returns to the terminal.
        play_single_sound(sound_file, volume, background=True)
        send_notification(agent_name, desktop_id, wait=False)
        print("{}")
        sys.exit(0)

    if is_already_active and not ("--force" in args):
        # User is already looking at the terminal window
        play_single_sound(sound_file, volume, background=True)
        send_notification(agent_name, desktop_id, wait=False)
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
