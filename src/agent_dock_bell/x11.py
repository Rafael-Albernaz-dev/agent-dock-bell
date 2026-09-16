"""
X11 protocol, window inspection, and urgency hint management via ctypes libX11.
"""

import os
import re
import subprocess
import ctypes
from ctypes import c_ulong, c_long, c_int, c_void_p, Structure, POINTER
from typing import Optional, Set, Tuple

class XWMHints(Structure):
    _fields_ = [
        ("flags", c_long),
        ("input", c_int),
        ("initial_state", c_int),
        ("icon_pixmap", c_ulong),
        ("icon_window", c_ulong),
        ("icon_x", c_int),
        ("icon_y", c_int),
        ("icon_mask", c_ulong),
        ("window_group", c_ulong),
    ]

XUrgencyHint = 1 << 8

# Initialize libX11 bindings
try:
    _x11 = ctypes.CDLL("libX11.so.6")
    _x11.XOpenDisplay.restype = c_void_p
    _x11.XGetWMHints.restype = POINTER(XWMHints)
    _x11.XSetWMHints.argtypes = [c_void_p, c_ulong, POINTER(XWMHints)]
    _x11.XFree.argtypes = [c_void_p]
    _x11.XFlush.argtypes = [c_void_p]
    _x11.XCloseDisplay.argtypes = [c_void_p]
    _display = _x11.XOpenDisplay(None)
except Exception:
    _x11 = None
    _display = None

def to_int(val) -> Optional[int]:
    """Convert hex string (e.g. '0x3c0000a') or int to a normalized Python int."""
    if val is None:
        return None
    if isinstance(val, int):
        return val
    try:
        s = str(val).strip()
        if s.startswith(("0x", "0X")):
            return int(s, 16)
        return int(s)
    except Exception:
        return None

def set_window_urgency(wid: int, enable: bool = True) -> None:
    """Set or clear the XUrgencyHint on an X11 window."""
    if not (_x11 and _display):
        return
    try:
        wid_int = to_int(wid)
        if wid_int is None:
            return
        hints = _x11.XGetWMHints(_display, wid_int)
        if hints:
            if enable:
                hints.contents.flags |= XUrgencyHint
            else:
                hints.contents.flags &= ~XUrgencyHint
            _x11.XSetWMHints(_display, wid_int, hints)
            _x11.XFlush(_display)
    except Exception:
        pass

def close_display() -> None:
    """Close the X11 connection cleanly."""
    global _display
    if _x11 and _display:
        try:
            _x11.XCloseDisplay(_display)
        except Exception:
            pass
        _display = None

def get_ancestor_pids() -> Set[int]:
    """Traverse /proc/<pid>/status to find all parent process IDs up to init."""
    pids = []
    curr = os.getpid()
    while curr > 1:
        try:
            with open(f"/proc/{curr}/status") as f:
                for line in f:
                    if line.startswith("PPid:"):
                        curr = int(line.split()[1])
                        pids.append(curr)
                        break
        except Exception:
            break
    return set(pids)

def get_active_window_id() -> Optional[str]:
    """Query X11 root window for the currently active window hex ID."""
    try:
        out = subprocess.check_output(
            ["xprop", "-root", "_NET_ACTIVE_WINDOW"],
            stderr=subprocess.DEVNULL
        ).decode()
        m = re.search(r"0x[0-9a-fA-F]+", out)
        if m:
            return m.group(0).lower()
    except Exception:
        pass
    return None

def find_terminal_windows(ancestor_pids: Set[int]) -> Tuple[Set[int], Set[int], str]:
    """
    Search _NET_CLIENT_LIST for windows matching ancestor PIDs or terminal WM_CLASS.
    Returns: (term_wids, term_pids, detected_desktop_id)
    """
    term_wids: Set[int] = set()
    term_pids: Set[int] = set()
    detected_desktop_id = "org.gnome.Terminal.desktop"

    try:
        out = subprocess.check_output(
            ["xprop", "-root", "_NET_CLIENT_LIST"],
            stderr=subprocess.DEVNULL
        ).decode()
        for wid in re.findall(r"0x[0-9a-fA-F]+", out):
            try:
                props = subprocess.check_output(
                    ["xprop", "-id", wid, "_NET_WM_PID", "WM_CLASS"],
                    stderr=subprocess.DEVNULL
                ).decode()
                pid = None
                wm_class = ""
                for line in props.splitlines():
                    if "_NET_WM_PID" in line:
                        try:
                            pid = int(line.split("=")[1].strip())
                        except Exception:
                            pass
                    elif "WM_CLASS" in line:
                        wm_class = line.lower()

                is_match = False
                if pid and pid in ancestor_pids:
                    is_match = True
                    term_pids.add(pid)
                elif any(t in wm_class for t in ["gnome-terminal", "terminal", "ptyxis", "alacritty", "kitty", "tilix"]):
                    is_match = True
                    if pid:
                        term_pids.add(pid)

                if is_match:
                    w_int = to_int(wid)
                    if w_int is not None:
                        term_wids.add(w_int)
                    if "code" in wm_class:
                        detected_desktop_id = "code.desktop"
                    elif "gnome-terminal" in wm_class:
                        detected_desktop_id = "org.gnome.Terminal.desktop"
            except Exception:
                pass
    except Exception:
        pass

    return term_wids, term_pids, detected_desktop_id
