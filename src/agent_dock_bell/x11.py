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
    _x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    _x11.XOpenDisplay.restype = c_void_p
    _x11.XGetWMHints.argtypes = [c_void_p, c_ulong]
    _x11.XGetWMHints.restype = POINTER(XWMHints)
    _x11.XSetWMHints.argtypes = [c_void_p, c_ulong, POINTER(XWMHints)]
    _x11.XFree.argtypes = [c_void_p]
    _x11.XFlush.argtypes = [c_void_p]
    _x11.XCloseDisplay.argtypes = [c_void_p]
    _x11.XSetErrorHandler.argtypes = [c_void_p]
    _x11.XSetErrorHandler.restype = c_void_p

    _X_ERROR_HANDLER_T = ctypes.CFUNCTYPE(c_int, c_void_p, c_void_p)
    def _null_x_error(display, error_event):
        return 0
    _c_null_x_error = _X_ERROR_HANDLER_T(_null_x_error)
    _x11.XSetErrorHandler(_c_null_x_error)

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
            try:
                if enable:
                    hints.contents.flags |= XUrgencyHint
                else:
                    hints.contents.flags &= ~XUrgencyHint
                _x11.XSetWMHints(_display, wid_int, hints)
                _x11.XFlush(_display)
            finally:
                _x11.XFree(hints)
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
            parent = None
            with open(f"/proc/{curr}/status") as f:
                for line in f:
                    if line.startswith("PPid:"):
                        parent = int(line.split()[1])
                        break
            if parent is None or parent == curr:
                break
            curr = parent
            if curr > 1:
                pids.append(curr)
        except Exception:
            break
    return set(pids)

def get_active_window_id() -> Optional[str]:
    """Query X11 root window for the currently active window hex ID."""
    if not os.environ.get("DISPLAY"):
        return None
    try:
        out = subprocess.check_output(
            ["xprop", "-root", "_NET_ACTIVE_WINDOW"],
            stderr=subprocess.DEVNULL, timeout=0.3
        ).decode()
        m = re.search(r"0x[0-9a-fA-F]+", out)
        if m:
            return m.group(0).lower()
    except Exception:
        pass
    return None

VSCODE_DESKTOP_ID = (
    "com.microsoft.VSCode.desktop"
    if os.path.exists("/usr/share/applications/com.microsoft.VSCode.desktop")
    else "code.desktop"
)

KNOWN_TERMINAL_BINARIES = {
    "gnome-terminal-server": "org.gnome.Terminal.desktop",
    "gnome-terminal": "org.gnome.Terminal.desktop",
    "ptyxis": "org.gnome.Ptyxis.desktop",
    "alacritty": "Alacritty.desktop",
    "kitty": "kitty.desktop",
    "tilix": "com.gexperts.Tilix.desktop",
    "konsole": "org.kde.konsole.desktop",
    "foot": "foot.desktop",
    "wezterm": "org.wezfurlong.wezterm.desktop",
    "wezterm-gui": "org.wezfurlong.wezterm.desktop",
    "xfce4-terminal": "xfce4-terminal.desktop",
    "mate-terminal": "mate-terminal.desktop",
    "terminator": "terminator.desktop",
    "xterm": "xterm.desktop",
    "lxterminal": "lxterminal.desktop",
    "qterminal": "qterminal.desktop",
    "ghostty": "com.mitchellh.ghostty.desktop",
    "warp": "dev.warp.Warp.desktop",
    "rio": "rio.desktop",
    "blackbox": "com.raggesilver.BlackBox.desktop",
    "contour": "org.contour-terminal.contour.desktop",
    "hyper": "hyper.desktop",
    "tabby": "tabby.desktop",
    "rxvt": "rxvt.desktop",
    "urxvt": "urxvt.desktop",
    "st": "st.desktop",
    "chatgpt": "chatgpt.desktop",
    "code": VSCODE_DESKTOP_ID,
    "code-insiders": "code-insiders.desktop",
    "vscodium": "vscodium.desktop",
    "cursor": "cursor.desktop",
    "windsurf": "windsurf.desktop",
    "zed": "dev.zed.Zed.desktop",
}

EMBEDDED_TERMINAL_BINARIES = {
    "code",
    "code-insiders",
    "vscodium",
    "chatgpt",
    "cursor",
    "windsurf",
    "zed",
}

def detect_terminal_process(ancestor_pids: Set[int]) -> Tuple[Set[int], str]:
    """Inspect /proc to discover terminal emulator processes and desktop IDs among ancestors."""
    native_pids: Set[int] = set()
    embedded_pids: Set[int] = set()
    detected_desktop_id = "org.gnome.Terminal.desktop"
    native_term_found = False

    for pid in ancestor_pids:
        try:
            comm = ""
            bin_name = ""
            comm_path = f"/proc/{pid}/comm"
            if os.path.exists(comm_path):
                with open(comm_path, "r", errors="ignore") as f:
                    comm = f.read().strip().lower()
            cmdline_path = f"/proc/{pid}/cmdline"
            if os.path.exists(cmdline_path):
                with open(cmdline_path, "rb") as f:
                    raw = f.read()
                    args = [a.decode("utf-8", "replace").lower() for a in raw.split(b"\0") if a]
                    if args:
                        bin_name = os.path.basename(args[0])

            for term_bin, d_id in KNOWN_TERMINAL_BINARIES.items():
                is_match = False
                if bin_name == term_bin or comm == term_bin:
                    is_match = True
                elif len(comm) == 15 and term_bin.startswith(comm):
                    is_match = True

                if is_match:
                    is_embedded = term_bin in EMBEDDED_TERMINAL_BINARIES
                    (embedded_pids if is_embedded else native_pids).add(pid)
                    # An embedded terminal is a fallback only when no native terminal exists.
                    if not native_term_found or not is_embedded:
                        detected_desktop_id = d_id
                        if not is_embedded:
                            native_term_found = True
                    break
        except Exception:
            pass
    return (native_pids or embedded_pids), detected_desktop_id

def get_ancestor_ttys(ancestor_pids: Set[int]) -> Set[str]:
    """Inspect open file descriptors of ancestors in /proc to find terminal slave TTYs (pts)."""
    ttys: Set[str] = set()
    for pid in ancestor_pids:
        fd_dir = f"/proc/{pid}/fd"
        if not os.path.isdir(fd_dir):
            continue
        try:
            for entry in os.listdir(fd_dir):
                target_path = os.path.join(fd_dir, entry)
                try:
                    target = os.readlink(target_path)
                    if target.startswith("/dev/pts/") or (target.startswith("/dev/tty") and not target.endswith("null")):
                        ttys.add(target)
                except Exception:
                    pass
        except Exception:
            pass
    return ttys

def is_terminal_active_atspi(term_pids: Set[int]) -> bool:
    """
    Query AT-SPI to check if any terminal window currently has active focus on Wayland or X11.
    Strictly filters by term_pids to minimize D-Bus traffic, prevent cross-app lag, and avoid
    false positive focus triggers from Chromium/Electron background frames.
    """
    if not term_pids:
        return False
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
        Atspi.init()
        desktop = Atspi.get_desktop(0)
        if not desktop:
            return False
        child_count = desktop.get_child_count()
        for i in range(child_count):
            try:
                app = desktop.get_child_at_index(i)
                if not app:
                    continue
                pid = app.get_process_id()

                if pid not in term_pids:
                    continue

                win_count = app.get_child_count()
                for j in range(win_count):
                    try:
                        win = app.get_child_at_index(j)
                        if not win:
                            continue

                        # Top-level window containers have role FRAME, WINDOW, or DIALOG.
                        # Internal WebContents or panels should not be treated as top-level windows.
                        role = win.get_role()
                        if role not in (Atspi.Role.FRAME, Atspi.Role.WINDOW, Atspi.Role.DIALOG):
                            continue

                        state_set = win.get_state_set()
                        if (
                            state_set.contains(Atspi.StateType.ACTIVE)
                            or state_set.contains(Atspi.StateType.FOCUSED)
                        ):
                            return True
                    except Exception:
                        continue
            except Exception:
                continue
    except Exception:
        pass
    return False

def is_terminal_focused(
    term_wids: Set[int],
    term_pids: Set[int],
    current_active_wid: Optional[int] = None,
) -> bool:
    """
    Determine if terminal window is currently focused via X11 active window or Wayland AT-SPI.
    """
    if current_active_wid is not None and current_active_wid != 0:
        if current_active_wid in term_wids:
            return True
        try:
            p_raw = subprocess.check_output(
                ["xprop", "-id", hex(current_active_wid), "_NET_WM_PID"],
                stderr=subprocess.DEVNULL,
                timeout=0.2,
            )
            p_out = p_raw.decode("utf-8", "replace") if isinstance(p_raw, bytes) else str(p_raw)
            for line in p_out.splitlines():
                if "_NET_WM_PID" in line:
                    n_pid = int(line.split("=")[1].strip())
                    if n_pid in term_pids:
                        term_wids.add(current_active_wid)
                        return True
        except Exception:
            pass
    return is_terminal_active_atspi(term_pids)

def find_terminal_windows(ancestor_pids: Set[int]) -> Tuple[Set[int], Set[int], str]:
    """
    Search _NET_CLIENT_LIST for windows matching ancestor PIDs or terminal WM_CLASS,
    and inspect /proc to identify terminal processes even on Wayland without X11 windows.
    Returns: (term_wids, term_pids, detected_desktop_id)
    """
    term_wids: Set[int] = set()
    proc_pids, detected_desktop_id = detect_terminal_process(ancestor_pids)
    term_pids: Set[int] = set(proc_pids)

    if not os.environ.get("DISPLAY"):
        return term_wids, term_pids, detected_desktop_id

    try:
        out = subprocess.check_output(
            ["xprop", "-root", "_NET_CLIENT_LIST"],
            stderr=subprocess.DEVNULL, timeout=0.3
        ).decode()
        for wid in re.findall(r"0x[0-9a-fA-F]+", out):
            try:
                props = subprocess.check_output(
                    ["xprop", "-id", wid, "_NET_WM_PID", "WM_CLASS"],
                    stderr=subprocess.DEVNULL, timeout=0.3
                ).decode()
                pid = None
                wm_classes = []
                for line in props.splitlines():
                    if "_NET_WM_PID" in line:
                        try:
                            pid = int(line.split("=")[1].strip())
                        except Exception:
                            pass
                    elif "WM_CLASS" in line:
                        wm_classes = [c.lower() for c in re.findall(r'"([^"]*)"', line)]

                is_match = False
                if pid and pid in term_pids:
                    is_match = True
                    term_pids.add(pid)

                if is_match:
                    w_int = to_int(wid)
                    if w_int is not None:
                        term_wids.add(w_int)
                    if not detected_desktop_id or detected_desktop_id == "org.gnome.Terminal.desktop":
                        for c in wm_classes:
                            if "chatgpt" in c:
                                detected_desktop_id = "chatgpt.desktop"
                                break
                            elif c in ("code", "visual studio code", "vscodium"):
                                detected_desktop_id = VSCODE_DESKTOP_ID
                                break
                            elif "cursor" in c:
                                detected_desktop_id = "cursor.desktop"
                                break
                            elif "windsurf" in c:
                                detected_desktop_id = "windsurf.desktop"
                                break
            except Exception:
                pass
    except Exception:
        pass

    return term_wids, term_pids, detected_desktop_id
