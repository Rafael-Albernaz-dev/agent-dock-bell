"""
Background daemon lifecycle, PID file locking, window focus monitoring, and cleanup.
"""

import os
import re
import signal
import subprocess
import sys
import threading
import time
from typing import Optional, Set

from .audio import AudioLoopWorker, kill_audio_processes
from .dock import reset_dock_dance, send_notification, set_dock_badge
from .x11 import close_display, is_terminal_active_atspi, set_window_urgency, to_int

PID_FILE = f"/tmp/agent-dock-bell-{os.getuid()}.pid"
LEGACY_PID_FILE = f"/tmp/agent-alarm-{os.getuid()}.pid"

class PIDLock:
    """Manages process locking and termination of previous daemon instances."""

    @staticmethod
    def kill_existing() -> None:
        my_pid = os.getpid()

        # 1. Kill PID recorded in PID files
        for p_file in [PID_FILE, LEGACY_PID_FILE]:
            if os.path.exists(p_file):
                try:
                    with open(p_file) as f:
                        old_pid = int(f.read().strip())
                    if old_pid != my_pid:
                        try:
                            os.kill(old_pid, signal.SIGTERM)
                            for _ in range(15):
                                time.sleep(0.02)
                                try:
                                    os.kill(old_pid, 0)
                                except OSError:
                                    break
                            else:
                                try:
                                    os.kill(old_pid, signal.SIGKILL)
                                except OSError:
                                    pass
                        except OSError:
                            pass
                except Exception:
                    pass
                try:
                    if os.path.exists(p_file):
                        os.remove(p_file)
                except Exception:
                    pass

        # 2. Sweep other orphan daemon processes for current user
        try:
            out = subprocess.check_output(
                ["pgrep", "-u", str(os.getuid()), "-f", "agent_dock_bell.*--daemon"],
                stderr=subprocess.DEVNULL,
            ).decode()
            for p_str in out.split():
                try:
                    p = int(p_str.strip())
                    if p != my_pid:
                        os.kill(p, signal.SIGTERM)
                        time.sleep(0.02)
                        try:
                            os.kill(p, signal.SIGKILL)
                        except OSError:
                            pass
                except Exception:
                    pass
        except Exception:
            pass

        # 3. Kill lingering audio processes
        kill_audio_processes()

    @staticmethod
    def acquire() -> None:
        try:
            with open(PID_FILE, "w") as f:
                f.write(str(os.getpid()))
        except Exception:
            pass

    @staticmethod
    def release() -> None:
        for p_file in [PID_FILE, LEGACY_PID_FILE]:
            try:
                if os.path.exists(p_file):
                    with open(p_file) as f:
                        pid_in_file = int(f.read().strip())
                    if pid_in_file == os.getpid():
                        os.remove(p_file)
            except Exception:
                pass

def run_daemon(
    agent_name: str,
    desktop_id: str,
    term_wids: Set[int],
    term_pids: Set[int],
    sound_file: str,
    volume: str = "1.0",
    max_audio: int = 180,
    max_wait: int = 600,
    term_ttys: Optional[Set[str]] = None,
) -> None:
    """Run the detached background daemon until the terminal gains focus or receives interaction."""
    PIDLock.acquire()

    audio_worker = AudioLoopWorker(sound_file, volume=volume, max_seconds=max_audio)
    notification_id = None
    spy_proc: Optional[subprocess.Popen] = None
    xprop_triggered = threading.Event()
    stop_monitoring = threading.Event()

    def cleanup() -> None:
        stop_monitoring.set()
        try:
            audio_worker.stop()
        except Exception:
            pass
        try:
            kill_audio_processes(sound_file)
        except Exception:
            pass

        if spy_proc and spy_proc.poll() is None:
            try:
                spy_proc.terminate()
            except Exception:
                pass

        try:
            set_dock_badge(desktop_id, 0, urgent=False)
        except Exception:
            pass
        for wid in term_wids:
            try:
                set_window_urgency(wid, False)
            except Exception:
                pass

        # Desktop notifications expire naturally (5s) or persist in the notification center;
        # do not forcefully close them on focus cleanup.
        try:
            reset_dock_dance()
        except Exception:
            pass
        try:
            PIDLock.release()
        except Exception:
            pass
        try:
            close_display()
        except Exception:
            pass

    def on_signal(signum, frame):
        cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGHUP, on_signal)

    try:
        # 1. Dock badge & urgency
        set_dock_badge(desktop_id, 1, urgent=True)

        # 2. X11 Window urgency
        for wid in term_wids:
            set_window_urgency(wid, True)

        # 3. Notification
        notification_id = send_notification(agent_name, desktop_id)

        # 4. Audio loop
        audio_worker.start()

        # 5. Non-blocking X11 focus monitor thread (if DISPLAY exists)
        if os.environ.get("DISPLAY"):
            def _xprop_spy_worker():
                nonlocal spy_proc
                try:
                    spy_proc = subprocess.Popen(
                        ["xprop", "-spy", "-root", "_NET_ACTIVE_WINDOW"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        text=True,
                    )
                    for line in spy_proc.stdout:
                        if stop_monitoring.is_set():
                            break
                        m = re.search(r"0x[0-9a-fA-F]+", line)
                        if m:
                            new_active_hex = m.group(0)
                            new_active_int = to_int(new_active_hex)
                            if new_active_int and new_active_int != 0:
                                if new_active_int in term_wids:
                                    xprop_triggered.set()
                                    break
                                try:
                                    p_out = subprocess.check_output(
                                        ["xprop", "-id", new_active_hex, "_NET_WM_PID", "WM_CLASS"],
                                        stderr=subprocess.DEVNULL,
                                    ).decode()
                                    is_term = False
                                    for pline in p_out.splitlines():
                                        if "_NET_WM_PID" in pline:
                                            try:
                                                n_pid = int(pline.split("=")[1].strip())
                                                if n_pid in term_pids:
                                                    is_term = True
                                                    break
                                            except Exception:
                                                pass
                                        elif "WM_CLASS" in pline:
                                            if any(t in pline.lower() for t in ["gnome-terminal", "terminal", "ptyxis", "alacritty", "kitty", "tilix", "konsole"]):
                                                is_term = True
                                                break
                                    if is_term:
                                        xprop_triggered.set()
                                        break
                                except Exception:
                                    pass
                except Exception:
                    pass

            spy_thread = threading.Thread(target=_xprop_spy_worker, daemon=True)
            spy_thread.start()

        # 6. Capture initial TTY stats for activity monitoring
        initial_tty_stats = {}
        if term_ttys:
            for tty in term_ttys:
                try:
                    if os.path.exists(tty):
                        st = os.stat(tty)
                        initial_tty_stats[tty] = (st.st_atime, st.st_mtime)
                except Exception:
                    pass

        # 7. Multi-Signal Focus & Activity Polling Loop
        start_wait = time.time()
        while time.time() - start_wait < max_wait:
            # Check A: X11 active window focus
            if xprop_triggered.is_set():
                break

            # Check B: AT-SPI window focus (Wayland & X11)
            if is_terminal_active_atspi(term_pids):
                break

            # Check C: TTY interaction (user pressed any key or entered input)
            tty_activity = False
            for tty, (i_atime, i_mtime) in initial_tty_stats.items():
                try:
                    if os.path.exists(tty):
                        st = os.stat(tty)
                        if st.st_atime > i_atime or st.st_mtime > i_mtime:
                            tty_activity = True
                            break
                except Exception:
                    pass
            if tty_activity:
                break

            # Check D: Process liveness (if terminal was closed/killed, terminate immediately)
            if term_pids:
                any_alive = False
                for pid in term_pids:
                    try:
                        os.kill(pid, 0)
                        any_alive = True
                        break
                    except OSError:
                        pass
                if not any_alive:
                    break

            time.sleep(0.2)
    except Exception:
        pass
    finally:
        cleanup()

    sys.exit(0)
