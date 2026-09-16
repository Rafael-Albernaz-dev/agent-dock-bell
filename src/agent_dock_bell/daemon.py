"""
Background daemon lifecycle, PID file locking, window focus monitoring, and cleanup.
"""

import os
import re
import signal
import subprocess
import sys
import time
from typing import Set

from .audio import AudioLoopWorker, kill_audio_processes
from .dock import close_notification, reset_dock_dance, send_notification, set_dock_badge
from .x11 import close_display, set_window_urgency, to_int

PID_FILE = f"/tmp/agent-dock-bell-{os.getuid()}.pid"
LEGACY_PID_FILE = f"/tmp/agent-alarm-{os.getuid()}.pid"

class PIDLock:
    """Manages process locking and termination of previous daemon instances."""

    @staticmethod
    def kill_existing() -> None:
        for p_file in [PID_FILE, LEGACY_PID_FILE]:
            if os.path.exists(p_file):
                try:
                    with open(p_file) as f:
                        old_pid = int(f.read().strip())
                    if old_pid != os.getpid():
                        os.kill(old_pid, signal.SIGTERM)
                        for _ in range(10):
                            time.sleep(0.02)
                            try:
                                os.kill(old_pid, 0)
                            except OSError:
                                break
                except Exception:
                    pass
                try:
                    if os.path.exists(p_file):
                        os.remove(p_file)
                except Exception:
                    pass

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
) -> None:
    """Run the detached background daemon until the terminal gains focus."""
    PIDLock.acquire()

    audio_worker = AudioLoopWorker(sound_file, volume=volume, max_seconds=max_audio)
    notification_id = None
    spy_proc: Optional[subprocess.Popen] = None

    def cleanup() -> None:
        audio_worker.stop()
        kill_audio_processes(sound_file)

        if spy_proc and spy_proc.poll() is None:
            try:
                spy_proc.terminate()
            except Exception:
                pass

        set_dock_badge(desktop_id, 0, urgent=False)
        for wid in term_wids:
            set_window_urgency(wid, False)

        close_notification(notification_id)
        reset_dock_dance()
        PIDLock.release()
        close_display()

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

        # 5. Monitor window focus
        try:
            spy_proc = subprocess.Popen(
                ["xprop", "-spy", "-root", "_NET_ACTIVE_WINDOW"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )

            start_wait = time.time()
            for line in spy_proc.stdout:
                if time.time() - start_wait > max_wait:
                    break

                m = re.search(r"0x[0-9a-fA-F]+", line)
                if m:
                    new_active_hex = m.group(0)
                    new_active_int = to_int(new_active_hex)
                    if new_active_int and new_active_int != 0:
                        if new_active_int in term_wids:
                            break

                        # Check if window belongs to terminal process
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
                                    if any(t in pline.lower() for t in ["gnome-terminal", "terminal", "ptyxis", "alacritty", "kitty"]):
                                        is_term = True
                                        break
                            if is_term:
                                break
                        except Exception:
                            pass
        except Exception:
            pass
    finally:
        cleanup()

    sys.exit(0)
