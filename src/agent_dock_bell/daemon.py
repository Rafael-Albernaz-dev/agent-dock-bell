"""
Background daemon lifecycle, PID file locking, window focus monitoring, and cleanup.
"""

import os
import fcntl
import re
import signal
import subprocess
import sys
import threading
import time
from typing import Optional, Set

from .audio import AudioLoopWorker
from .dock import send_notification, set_dock_badge
from .x11 import close_display, is_terminal_active_atspi, set_window_urgency, to_int

PID_FILE = f"/tmp/agent-dock-bell-{os.getuid()}.pid"
LEGACY_PID_FILE = f"/tmp/agent-alarm-{os.getuid()}.pid"

class PIDLock:
    """Manages process locking and termination of previous daemon instances."""
    _lock_fd: Optional[int] = None

    @staticmethod
    def kill_existing() -> None:
        for p_file in [PID_FILE, LEGACY_PID_FILE]:
            try:
                with open(p_file) as f:
                    old_pid = int(f.read().strip())
                if old_pid == os.getpid() or old_pid <= 1:
                    continue
                if os.stat(f"/proc/{old_pid}").st_uid != os.getuid():
                    continue
                with open(f"/proc/{old_pid}/cmdline", "rb") as f:
                    argv = f.read().split(b"\0")
                if b"--daemon" in argv and any(b"agent_dock_bell.cli" in arg for arg in argv):
                    os.kill(old_pid, signal.SIGTERM)
            except (OSError, ValueError):
                continue

    @staticmethod
    def acquire() -> bool:
        fd = os.open(PID_FILE, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        deadline = time.monotonic() + 3.0
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        return False
                    time.sleep(0.02)
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode())
            os.fsync(fd)
            PIDLock._lock_fd = fd
            return True
        finally:
            if PIDLock._lock_fd != fd:
                os.close(fd)

    @staticmethod
    def release() -> None:
        fd = PIDLock._lock_fd
        PIDLock._lock_fd = None
        if fd is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

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
    if not PIDLock.acquire():
        return

    audio_worker = AudioLoopWorker(sound_file, volume=volume, max_seconds=max_audio)
    spy_proc: Optional[subprocess.Popen] = None
    spy_thread = None
    xprop_triggered = threading.Event()
    stop_monitoring = threading.Event()
    atspi_triggered = threading.Event()
    atspi_loop = None
    atspi_listener = None
    atspi_thread = None
    cleaned = threading.Event()

    def cleanup() -> None:
        if cleaned.is_set():
            return
        cleaned.set()
        stop_monitoring.set()
        try:
            audio_worker.stop()
        except Exception:
            pass
        if spy_proc:
            try:
                if spy_proc.poll() is None:
                    spy_proc.terminate()
                    try:
                        spy_proc.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        spy_proc.kill()
                        spy_proc.wait(timeout=0.5)
            except Exception:
                pass
        if spy_thread and spy_thread.is_alive():
            spy_thread.join(timeout=0.5)
        if spy_proc and spy_proc.stdout:
            try:
                spy_proc.stdout.close()
            except Exception:
                pass

        if atspi_listener:
            for event_name in ("window:activate", "object:state-changed:active", "object:state-changed:focused"):
                try:
                    atspi_listener.deregister(event_name)
                except Exception:
                    pass
        if atspi_loop:
            try:
                if atspi_loop.is_running():
                    atspi_loop.quit()
            except Exception:
                pass
        if atspi_thread and atspi_thread.is_alive():
            atspi_thread.join(timeout=0.5)

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

        # 3. Notification (the launcher has already sent the terminal bell)
        send_notification(agent_name, desktop_id)

        # 4. Audio loop
        audio_worker.start()

        # 5. Non-blocking X11 focus monitor thread (if DISPLAY exists)
        if os.environ.get("DISPLAY") and os.environ.get("XDG_SESSION_TYPE") != "wayland":
            def _xprop_spy_worker():
                nonlocal spy_proc
                try:
                    if stop_monitoring.is_set():
                        return
                    spy_proc = subprocess.Popen(
                        ["xprop", "-spy", "-root", "_NET_ACTIVE_WINDOW"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        text=True,
                    )
                    if stop_monitoring.is_set():
                        spy_proc.terminate()
                        return
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
                                        ["xprop", "-id", new_active_hex, "_NET_WM_PID"],
                                        stderr=subprocess.DEVNULL,
                                        timeout=0.3,
                                    ).decode()
                                    for pline in p_out.splitlines():
                                        if "_NET_WM_PID" in pline:
                                            try:
                                                n_pid = int(pline.split("=")[1].strip())
                                                if n_pid in term_pids:
                                                    xprop_triggered.set()
                                                    break
                                            except Exception:
                                                pass
                                    if xprop_triggered.is_set():
                                        break
                                except Exception:
                                    pass
                except Exception:
                    pass

            spy_thread = threading.Thread(target=_xprop_spy_worker, daemon=True)
            spy_thread.start()

        # 6. Event-driven AT-SPI focus monitor (Wayland & X11)
        try:
            import gi
            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi, GLib

            def _on_atspi_event(ev):
                try:
                    if not term_pids:
                        return
                    if getattr(ev, "type", "").startswith("object:") and not ev.detail1:
                        return
                    source = ev.source
                    if not source:
                        return
                    app = source.get_application()
                    if not app:
                        return
                    pid = app.get_process_id()
                    if pid in term_pids:
                        atspi_triggered.set()
                except Exception:
                    pass

            if term_pids:
                Atspi.init()
                atspi_listener = Atspi.EventListener.new(_on_atspi_event)
                atspi_listener.register("window:activate")
                atspi_listener.register("object:state-changed:active")
                atspi_listener.register("object:state-changed:focused")
                atspi_loop = GLib.MainLoop()
                atspi_thread = threading.Thread(target=atspi_loop.run, daemon=True)
                atspi_thread.start()
        except Exception:
            if atspi_listener:
                for event_name in ("window:activate", "object:state-changed:active", "object:state-changed:focused"):
                    try:
                        atspi_listener.deregister(event_name)
                    except Exception:
                        pass
            atspi_listener = None
            atspi_loop = None

        # 7. Capture initial TTY stats for activity monitoring
        initial_tty_stats = {}
        if term_ttys:
            for tty in term_ttys:
                try:
                    if os.path.exists(tty):
                        st = os.stat(tty)
                        initial_tty_stats[tty] = st.st_atime
                except Exception:
                    pass

        # 8. Event-Driven Multi-Signal Focus & Activity Monitor Loop
        start_wait = time.monotonic()
        last_atspi_poll = time.monotonic()
        while time.monotonic() - start_wait < max_wait:
            # Check A: X11 active window focus
            if xprop_triggered.is_set():
                break

            # Check B: Event-driven AT-SPI window focus (Wayland & X11)
            if atspi_triggered.is_set():
                atspi_triggered.clear()
                if is_terminal_active_atspi(term_pids):
                    break

            # Check C: Low-frequency AT-SPI polling fallback (every 1.5s, targeted strictly at term_pids)
            now = time.monotonic()
            if now - last_atspi_poll >= 1.5:
                last_atspi_poll = now
                if term_pids and is_terminal_active_atspi(term_pids):
                    break

            # Check D: TTY interaction (user pressed any key or entered input)
            tty_activity = False
            for tty, i_atime in initial_tty_stats.items():
                try:
                    if os.path.exists(tty):
                        st = os.stat(tty)
                        if st.st_atime > i_atime:
                            tty_activity = True
                            break
                except Exception:
                    pass
            if tty_activity:
                break

            # Check E: Process liveness (if terminal was closed/killed, terminate immediately)
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

            time.sleep(0.1)
    except Exception:
        pass
    finally:
        cleanup()

    sys.exit(0)
