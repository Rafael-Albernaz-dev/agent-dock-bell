"""Keep clickable GNOME Terminal notifications alive beyond the alarm daemon."""

import json
import os
import re
import socket
import stat
import subprocess
import sys
import time
import uuid
from collections import OrderedDict
from typing import Dict, Optional


SOCKET_NAME = "\0agent-dock-bell-notifications-%d-v1" % os.getuid()
MAX_REQUEST = 4096
MAX_PENDING = 256
PENDING_SECONDS = 24 * 60 * 60


def _process_start_time(pid: int) -> Optional[str]:
    try:
        with open("/proc/%d/stat" % pid) as proc_stat:
            return proc_stat.read().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def _terminal_target(pids, ttys=None) -> Optional[Dict[str, object]]:
    valid_ttys = sorted(tty for tty in (ttys or set())
                        if isinstance(tty, str) and re.fullmatch(r"/dev/pts/[0-9]+", tty))
    if len(valid_ttys) != 1:
        return None
    for pid in sorted(pids):
        if not isinstance(pid, int) or pid <= 1:
            continue
        try:
            with open("/proc/%d/comm" % pid) as proc_comm:
                comm = proc_comm.read().strip()
        except OSError:
            continue
        if comm not in ("gnome-terminal-", "gnome-terminal"):
            continue
        started = _process_start_time(pid)
        if started is not None:
            return {"pid": pid, "started": started, "tty": valid_ttys[0]}
    return None


def _send_request(payload: dict) -> bool:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(0.5)
        client.connect(SOCKET_NAME)
        client.sendall(json.dumps(payload).encode("utf-8") + b"\n")
        return client.recv(16).startswith(b"ok\n")


def queue_terminal_notification(agent_name: str, desktop_id: str, pids, ttys=None) -> bool:
    """Ask the session broker to show a clickable notification."""
    if desktop_id != "org.gnome.Terminal.desktop":
        return False
    target = _terminal_target(pids, ttys)
    if target is None:
        return False
    payload = {"agent": agent_name[:120], "target": target}
    try:
        return _send_request(payload)
    except (OSError, ValueError):
        pass

    from .cli import build_daemon_environment, get_python_executable
    try:
        subprocess.Popen(
            [get_python_executable(), "-m", "agent_dock_bell.notification_broker"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
            env=build_daemon_environment(),
        )
    except OSError:
        return False
    for _ in range(20):
        time.sleep(0.05)
        try:
            return _send_request(payload)
        except (OSError, ValueError):
            continue
    return False


def _find_showing_terminal(node, Atspi, budget) -> object:
    if budget[0] <= 0:
        return None
    budget[0] -= 1
    try:
        state = node.get_state_set()
        if node.get_role() == Atspi.Role.TERMINAL and state.contains(Atspi.StateType.SHOWING):
            return node
        for index in range(min(node.get_child_count(), 30)):
            found = _find_showing_terminal(node.get_child_at_index(index), Atspi, budget)
            if found is not None:
                return found
    except Exception:
        pass
    return None


def _find_frame_for_tty(frames, tty: str, Atspi, return_tab: bool = False):
    if not isinstance(tty, str) or not re.fullmatch(r"/dev/pts/[0-9]+", tty):
        return None
    tabs = []
    for frame in frames:
        stack = [(frame, 0)]
        budget = 160
        while stack and budget:
            node, depth = stack.pop()
            budget -= 1
            try:
                if node.get_role() == Atspi.Role.PAGE_TAB:
                    tabs.append((frame, node, node.get_name()))
                if depth < 8:
                    for index in range(min(node.get_child_count(), 30) - 1, -1, -1):
                        stack.append((node.get_child_at_index(index), depth + 1))
            except Exception:
                continue
    if not tabs:
        for frame in frames:
            try:
                tabs.append((frame, frame, frame.get_name()))
            except Exception:
                pass
    if not tabs:
        return None

    try:
        fd = os.open(tty, os.O_WRONLY | os.O_NONBLOCK | os.O_NOCTTY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        if not stat.S_ISCHR(os.fstat(fd).st_mode):
            return None
        marker = "agent-dock-bell-" + uuid.uuid4().hex
        for _ in range(4):
            os.write(fd, ("\x1b]0;%s\x07" % marker).encode("ascii"))
            for _ in range(5):
                for frame, tab, original in tabs:
                    try:
                        if marker in tab.get_name():
                            safe_title = re.sub(r"[\x00-\x1f\x7f]", "", original)
                            os.write(fd, ("\x1b]0;%s\x07" % safe_title).encode("utf-8"))
                            return (frame, tab) if return_tab else frame
                    except Exception:
                        continue
                time.sleep(0.01)
    except OSError:
        pass
    finally:
        os.close(fd)
    return None


def focus_gnome_terminal(target: dict) -> bool:
    """Focus a visible terminal widget, without selecting or changing tabs."""
    pid = target.get("pid")
    if not isinstance(pid, int) or _process_start_time(pid) != target.get("started"):
        return False
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi

        Atspi.init()
        desktop = Atspi.get_desktop(0)
        for index in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(index)
            if not app or app.get_process_id() != pid:
                continue
            frames = []
            for window_index in range(app.get_child_count()):
                window = app.get_child_at_index(window_index)
                if window.get_role() not in (Atspi.Role.FRAME, Atspi.Role.WINDOW):
                    continue
                state = window.get_state_set()
                if state.contains(Atspi.StateType.ACTIVE) or state.contains(Atspi.StateType.SHOWING):
                    frames.append(window)
            if len(frames) == 1:
                frame = frames[0]
            else:
                frame = _find_frame_for_tty(frames, target.get("tty"), Atspi)
            if frame is None:
                return False
            widget = _find_showing_terminal(frame, Atspi, [160])
            if widget is not None:
                component = widget.get_component_iface()
                if component and component.grab_focus():
                    return True
    except Exception:
        pass
    return False


class NotificationBroker:
    def __init__(self, bus, Gio, GLib):
        self.bus = bus
        self.Gio = Gio
        self.GLib = GLib
        self.pending = OrderedDict()
        self.owners = set()
        self._refresh_owners()

    def _refresh_owners(self):
        self.owners.clear()
        for name in ("org.freedesktop.Notifications", "org.gnome.Shell"):
            try:
                result = self.bus.call_sync(
                    "org.freedesktop.DBus", "/org/freedesktop/DBus",
                    "org.freedesktop.DBus", "GetNameOwner",
                    self.GLib.Variant("(s)", (name,)),
                    self.GLib.VariantType("(s)"), self.Gio.DBusCallFlags.NONE, 1000, None,
                )
                self.owners.add(result.unpack()[0])
            except Exception:
                pass

    def notify(self, payload: dict) -> bool:
        if not isinstance(payload, dict):
            return False
        agent = payload.get("agent")
        target = payload.get("target")
        if not isinstance(agent, str) or not agent or not isinstance(target, dict):
            return False
        if not isinstance(target.get("pid"), int) or _process_start_time(target["pid"]) != target.get("started"):
            return False
        self._expire()
        try:
            params = self.GLib.Variant("(susssasa{sv}i)", (
                agent, 0, "org.gnome.Terminal", "Task Completed!",
                "%s has finished executing." % agent,
                ["default", "Open terminal"],
                {
                    "suppress-sound": self.GLib.Variant("b", True),
                    "urgency": self.GLib.Variant("y", 1),
                },
                5000,
            ))
            result = self.bus.call_sync(
                "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications", "Notify", params,
                self.GLib.VariantType("(u)"), self.Gio.DBusCallFlags.NONE, 1500, None,
            )
            nid = result.unpack()[0]
            if not isinstance(nid, int) or nid <= 0:
                return False
            self.pending[nid] = (target, time.monotonic())
            while len(self.pending) > MAX_PENDING:
                self.pending.popitem(last=False)
            return True
        except Exception:
            return False

    def _expire(self):
        cutoff = time.monotonic() - PENDING_SECONDS
        for nid, (_, created) in list(self.pending.items()):
            if created < cutoff:
                self.pending.pop(nid, None)

    def on_signal(self, connection, sender, path, interface, signal, params, user_data):
        if sender not in self.owners:
            return
        if signal == "NotificationClosed":
            self.pending.pop(params.unpack()[0], None)
        elif signal == "ActionInvoked":
            nid, action = params.unpack()
            item = self.pending.pop(nid, None)
            if action == "default" and item and time.monotonic() - item[1] <= PENDING_SECONDS:
                focus_gnome_terminal(item[0])

    def on_owner_change(self, connection, sender, path, interface, signal, params, user_data):
        name, old_owner, new_owner = params.unpack()
        if name in ("org.freedesktop.Notifications", "org.gnome.Shell"):
            self.pending.clear()
            self._refresh_owners()


def serve() -> None:
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(SOCKET_NAME)
    except OSError:
        server.close()
        return
    server.listen(8)
    server.setblocking(False)
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    broker = NotificationBroker(bus, Gio, GLib)
    bus.signal_subscribe(None, "org.freedesktop.Notifications", "ActionInvoked",
                         "/org/freedesktop/Notifications", None,
                         Gio.DBusSignalFlags.NONE, broker.on_signal, None)
    bus.signal_subscribe(None, "org.freedesktop.Notifications", "NotificationClosed",
                         "/org/freedesktop/Notifications", None,
                         Gio.DBusSignalFlags.NONE, broker.on_signal, None)
    bus.signal_subscribe("org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
                         "/org/freedesktop/DBus", None,
                         Gio.DBusSignalFlags.NONE, broker.on_owner_change, None)

    def on_client(source, condition):
        client = None
        try:
            client, _ = server.accept()
            client.settimeout(0.2)
            credentials = client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            if int.from_bytes(credentials[4:8], sys.byteorder) != os.getuid():
                return True
            request = bytearray()
            while b"\n" not in request and len(request) <= MAX_REQUEST:
                chunk = client.recv(MAX_REQUEST + 1 - len(request))
                if not chunk:
                    break
                request.extend(chunk)
            if len(request) > MAX_REQUEST or not request.endswith(b"\n"):
                return True
            payload = json.loads(request.decode("utf-8"))
            client.sendall(b"ok\n" if broker.notify(payload) else b"error\n")
        except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
            pass
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
        return True

    GLib.io_add_watch(server.fileno(), GLib.IO_IN, on_client)
    try:
        GLib.MainLoop().run()
    finally:
        server.close()


if __name__ == "__main__":
    serve()
