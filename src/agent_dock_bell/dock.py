"""
Ubuntu/GNOME Dock and Desktop Notification integration via D-Bus and gsettings.
"""

import os
import re
import stat
import subprocess
from typing import Optional, Set

_session_bus = None

def send_terminal_bell(term_ttys: Optional[Set[str]] = None) -> None:
    """
    Send a bell character to one validated slave TTY, or to /dev/tty.
    The terminal may request window attention depending on its bell settings.
    """
    def ring(tty: str) -> bool:
        if not re.fullmatch(r"/dev/pts/[0-9]+|/dev/tty", tty):
            return False
        fd = None
        try:
            fd = os.open(tty, os.O_WRONLY | os.O_NONBLOCK | os.O_NOCTTY | os.O_NOFOLLOW)
            if not stat.S_ISCHR(os.fstat(fd).st_mode):
                return False
            return os.write(fd, b"\a") == 1
        except OSError:
            return False
        finally:
            if fd is not None:
                os.close(fd)

    sent = any(ring(tty) for tty in sorted(term_ttys or set()))
    if not sent:
        ring("/dev/tty")

def set_dock_badge(desktop_id: str, count: int, urgent: bool = True) -> None:
    """
    Emit Unity LauncherEntry update over D-Bus to set dock unread badge count
    and urgency state on Ubuntu Dock (Dash-to-Dock).
    Prefers native Gio D-Bus connection so the D-Bus bus name remains owned
    by the caller process (preventing immediate clearance on subprocess exit).
    """
    desktop_id = desktop_id or "org.gnome.Terminal.desktop"
    global _session_bus
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib

        if _session_bus is None or _session_bus.is_closed():
            _session_bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus = _session_bus
        for app_uri in [f"application://{desktop_id}", desktop_id]:
            params = GLib.Variant("(sa{sv})", (
                app_uri,
                {
                    "count": GLib.Variant("x", count),
                    "count-visible": GLib.Variant("b", count > 0),
                    "urgent": GLib.Variant("b", urgent),
                }
            ))
            bus.emit_signal(
                None,
                "/com/canonical/unity/launcherentry/1",
                "com.canonical.Unity.LauncherEntry",
                "Update",
                params
            )
        if count == 0:
            bus.flush_sync(None)
        return
    except Exception:
        _session_bus = None

    # Fallback to gdbus subprocess
    for app_uri in [f"application://{desktop_id}", desktop_id]:
        c_vis = "true" if count > 0 else "false"
        urg_val = "true" if urgent else "false"
        try:
            subprocess.run(
                [
                    "gdbus", "emit", "--session",
                    "--object-path", "/com/canonical/unity/launcherentry/1",
                    "--signal", "com.canonical.Unity.LauncherEntry.Update",
                    app_uri,
                    f"{{'count': <int64 {count}>, 'count-visible': <{c_vis}>, 'urgent': <{urg_val}>}}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=0.5,
            )
        except Exception:
            pass

def send_notification(agent_name: str, desktop_id: str, wait: bool = True,
                      term_pids: Optional[Set[int]] = None,
                      term_ttys: Optional[Set[str]] = None) -> Optional[int]:
    """
    Send a notification without the desktop-entry hint that can suppress banners.
    GNOME Terminal notifications use a separate click broker when a target is known.
    """
    desktop_id = desktop_id or "org.gnome.Terminal.desktop"
    if term_pids and desktop_id == "org.gnome.Terminal.desktop":
        try:
            from .notification_broker import queue_terminal_notification
            if queue_terminal_notification(agent_name, desktop_id, term_pids, term_ttys):
                return None
        except Exception:
            pass
    icon = desktop_id[:-8] if desktop_id.endswith(".desktop") else desktop_id
    icon = icon or "utilities-terminal"
    cmd = [
        "notify-send",
        "-u", "normal",
        "-t", "5000",
        "-a", agent_name,
        "-i", icon,
        "-h", "boolean:suppress-sound:true",
        "Task Completed!",
        f"{agent_name} has finished executing.",
    ]
    if wait:
        cmd.insert(1, "-p")
        try:
            out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=1.0).decode().strip()
            if out.isdigit():
                return int(out)
        except Exception:
            pass
        return None
    else:
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        return None



def close_notification(nid: Optional[int]) -> None:
    """Dismiss an active desktop notification via D-Bus."""
    if not nid:
        return
    try:
        subprocess.run(
            [
                "gdbus", "call", "--session",
                "--dest", "org.freedesktop.Notifications",
                "--object-path", "/org/freedesktop/Notifications",
                "--method", "org.freedesktop.Notifications.CloseNotification",
                str(nid),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
