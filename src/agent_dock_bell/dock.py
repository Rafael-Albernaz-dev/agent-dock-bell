"""
Ubuntu/GNOME Dock and Desktop Notification integration via D-Bus and gsettings.
"""

import subprocess
import time
from typing import Optional

def set_dock_badge(desktop_id: str, count: int, urgent: bool = True) -> None:
    """
    Emit Unity LauncherEntry update over D-Bus to set dock unread badge count
    and urgency state on Ubuntu Dock (Dash-to-Dock).
    """
    for app_uri in [f"application://{desktop_id}", desktop_id]:
        c_vis = "true" if count > 0 else "false"
        urg_val = "true" if urgent else "false"
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
        )

def send_notification(agent_name: str, desktop_id: str) -> Optional[int]:
    """Send a desktop notification via notify-send and return the notification ID."""
    try:
        cmd = [
            "notify-send", "-p",
            "-u", "normal",
            "-a", agent_name,
            "-i", "org.gnome.Terminal",
            "-h", f"string:desktop-entry:{desktop_id}",
            "Task Completed!",
            f"{agent_name} has finished executing.",
        ]
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip()
        if out.isdigit():
            return int(out)
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

def reset_dock_dance() -> None:
    """
    Briefly toggle dance-urgent-applications in dash-to-dock to destroy the
    Clutter animation actor in GNOME Shell, immediately halting the dock icon wiggle.
    """
    try:
        subprocess.run(
            [
                "gsettings", "set", "org.gnome.shell.extensions.dash-to-dock",
                "dance-urgent-applications", "false",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.05)
        subprocess.run(
            [
                "gsettings", "set", "org.gnome.shell.extensions.dash-to-dock",
                "dance-urgent-applications", "true",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
