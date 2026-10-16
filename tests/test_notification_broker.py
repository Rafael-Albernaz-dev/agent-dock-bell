import sys
import stat
import time
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent_dock_bell.notification_broker import (
    NotificationBroker, _find_frame_for_tty, _terminal_target, focus_gnome_terminal,
)


class Variant:
    def __init__(self, signature, value):
        self.signature = signature
        self.value = value

    def unpack(self):
        return self.value


class Bus:
    def __init__(self):
        self.calls = []

    def call_sync(self, destination, path, interface, method, params, result_type, flags, timeout, cancellable):
        self.calls.append((destination, method, params))
        if method == "GetNameOwner":
            name = params.unpack()[0]
            return Variant("(s)", (":1.shell" if name == "org.gnome.Shell" else ":1.proxy",))
        if method == "Notify":
            return Variant("(u)", (77,))
        raise AssertionError(method)


def test_click_from_shell_after_notification_sender_exits():
    bus = Bus()
    gio = SimpleNamespace(DBusCallFlags=SimpleNamespace(NONE=0))
    glib = SimpleNamespace(Variant=Variant, VariantType=lambda sig: sig)
    broker = NotificationBroker(bus, gio, glib)
    with patch("agent_dock_bell.notification_broker._process_start_time", return_value="800"), \
         patch("agent_dock_bell.notification_broker.focus_gnome_terminal", return_value=True) as focus:
        assert broker.notify({"agent": "Codex CLI", "target": {"pid": 123, "started": "800"}})
        notify = [call for call in bus.calls if call[1] == "Notify"][0][2].unpack()
        assert notify[5] == ["default", "Open terminal"]
        assert notify[6]["suppress-sound"].unpack() is True
        broker.on_signal(None, ":1.proxy", None, None, "ActionInvoked", Variant("(us)", (77, "other")), None)
        focus.assert_not_called()
        broker.on_signal(None, ":1.shell", None, None, "ActionInvoked", Variant("(us)", (77, "default")), None)
        focus.assert_not_called()  # Another action consumed this notification ID.


def test_default_action_focuses_once_and_stale_ids_do_not_focus():
    bus = Bus()
    gio = SimpleNamespace(DBusCallFlags=SimpleNamespace(NONE=0))
    glib = SimpleNamespace(Variant=Variant, VariantType=lambda sig: sig)
    broker = NotificationBroker(bus, gio, glib)
    target = {"pid": 123, "started": "800"}
    with patch("agent_dock_bell.notification_broker._process_start_time", return_value="800"), \
         patch("agent_dock_bell.notification_broker.focus_gnome_terminal", return_value=True) as focus:
        assert broker.notify({"agent": "Codex CLI", "target": target})
        broker.on_signal(None, ":1.unrelated", None, None, "ActionInvoked", Variant("(us)", (77, "default")), None)
        focus.assert_not_called()
        broker.on_signal(None, ":1.shell", None, None, "ActionInvoked", Variant("(us)", (77, "default")), None)
        focus.assert_called_once_with(target)
        broker.on_signal(None, ":1.shell", None, None, "ActionInvoked", Variant("(us)", (77, "default")), None)
        focus.assert_called_once()
        broker.on_owner_change(None, ":1.dbus", None, None, "NameOwnerChanged",
                               Variant("(sss)", ("org.gnome.Shell", ":1.shell", ":1.new")), None)
        assert not broker.pending


def test_reused_terminal_pid_is_not_focused():
    with patch("agent_dock_bell.notification_broker._process_start_time", return_value="new-process"):
        assert not focus_gnome_terminal({"pid": 123, "started": "old-process"})


def test_terminal_target_requires_one_source_tty():
    with patch("builtins.open", create=True) as proc_file, \
         patch("agent_dock_bell.notification_broker._process_start_time", return_value="800"):
        proc_file.return_value.__enter__.return_value.read.return_value = "gnome-terminal-\n"
        assert _terminal_target({123}, set()) is None
        assert _terminal_target({123}, {"/dev/pts/7", "/dev/pts/8"}) is None
        assert _terminal_target({123}, {"/dev/pts/7"}) == {
            "pid": 123, "started": "800", "tty": "/dev/pts/7"}


def test_tty_marker_finds_frame_and_restores_title():
    state = {"title": "Original title"}
    marker = "agent-dock-bell-fixed"
    tab = Mock()
    tab.get_role.return_value = 1
    tab.get_child_count.return_value = 0
    tab.get_name.side_effect = lambda: state["title"]
    frame = Mock()
    frame.get_role.return_value = 2
    frame.get_child_count.return_value = 1
    frame.get_child_at_index.return_value = tab

    def write(fd, data):
        state["title"] = data.decode("utf-8").split(";", 1)[1].rstrip("\x07")
        return len(data)

    atspi = SimpleNamespace(Role=SimpleNamespace(PAGE_TAB=1))
    with patch("agent_dock_bell.notification_broker.uuid.uuid4", return_value=SimpleNamespace(hex="fixed")), \
         patch("agent_dock_bell.notification_broker.os.open", return_value=7), \
         patch("agent_dock_bell.notification_broker.os.fstat", return_value=SimpleNamespace(st_mode=stat.S_IFCHR)), \
         patch("agent_dock_bell.notification_broker.os.write", side_effect=write) as writes, \
         patch("agent_dock_bell.notification_broker.os.close") as close:
        assert _find_frame_for_tty([frame], "/dev/pts/7", atspi) is frame
    assert marker.encode() in writes.call_args_list[0].args[1]
    assert state["title"] == "Original title"
    close.assert_called_once_with(7)


def test_focus_skips_hidden_widget_and_uses_visible_terminal():
    class Node:
        def __init__(self, role, states=(), children=(), pid=0, component=None):
            self.role, self.states, self.children = role, states, children
            self.pid, self.component = pid, component

        def get_role(self):
            return self.role

        def get_state_set(self):
            return SimpleNamespace(contains=lambda state: state in self.states)

        def get_child_count(self):
            return len(self.children)

        def get_child_at_index(self, index):
            return self.children[index]

        def get_process_id(self):
            return self.pid

        def get_component_iface(self):
            return self.component

    role = SimpleNamespace(TERMINAL=1, FRAME=2, WINDOW=3)
    state = SimpleNamespace(SHOWING=1, ACTIVE=2)
    visible = Mock()
    visible.grab_focus.return_value = True
    wrong = Mock()
    wrong.grab_focus.return_value = True
    wrong_frame = Node(role.FRAME, (state.SHOWING,), (
        Node(role.TERMINAL, (state.SHOWING,), component=wrong),
    ))
    frame = Node(role.FRAME, (state.SHOWING,), (
        Node(role.TERMINAL), Node(role.TERMINAL, (state.SHOWING,), component=visible),
    ))
    desktop = Node(0, children=(Node(0, children=(wrong_frame, frame), pid=123),))
    atspi = SimpleNamespace(Role=role, StateType=state, init=lambda: None,
                            get_desktop=lambda index: desktop)
    gi = ModuleType("gi")
    gi.require_version = lambda name, version: None
    repository = ModuleType("gi.repository")
    repository.Atspi = atspi
    gi.repository = repository
    with patch("agent_dock_bell.notification_broker._process_start_time", return_value="800"), \
         patch("agent_dock_bell.notification_broker._find_frame_for_tty", return_value=frame) as locate, \
         patch.dict(sys.modules, {"gi": gi, "gi.repository": repository}):
        assert focus_gnome_terminal({"pid": 123, "started": "800", "tty": "/dev/pts/7"})
    with patch("agent_dock_bell.notification_broker._process_start_time", return_value="800"), \
         patch("agent_dock_bell.notification_broker._find_frame_for_tty", return_value=None), \
         patch.dict(sys.modules, {"gi": gi, "gi.repository": repository}):
        assert not focus_gnome_terminal({"pid": 123, "started": "800", "tty": "/dev/pts/7"})
    locate.assert_called_once()
    visible.grab_focus.assert_called_once_with()
    wrong.grab_focus.assert_not_called()


def test_action_invoked_dismisses_alarm_and_clears_badge():
    from agent_dock_bell.notification_broker import NotificationBroker
    bus = Mock()
    Gio = Mock()
    GLib = Mock()
    broker = NotificationBroker(bus, Gio, GLib)
    broker.owners = {":1.50"}
    target = {"pid": 123, "started": "800", "tty": "/dev/pts/7"}
    broker.pending[10] = (target, time.monotonic())

    with patch("agent_dock_bell.notification_broker.focus_gnome_terminal") as mock_focus, \
         patch("agent_dock_bell.daemon.PIDLock.kill_existing") as mock_kill, \
         patch("agent_dock_bell.dock.set_dock_badge") as mock_badge:
        params = Mock()
        params.unpack.return_value = (10, "default")
        broker.on_signal(bus, ":1.50", "/org/freedesktop/Notifications",
                         "org.freedesktop.Notifications", "ActionInvoked", params, None)

        mock_focus.assert_called_once_with(target)
        mock_kill.assert_called_once()
        mock_badge.assert_called_once_with("org.gnome.Terminal.desktop", 0, urgent=False)
        assert 10 not in broker.pending


def test_notification_closed_user_dismiss_kills_daemon_and_clears_badge():
    from agent_dock_bell.notification_broker import NotificationBroker
    bus = Mock()
    Gio = Mock()
    GLib = Mock()
    broker = NotificationBroker(bus, Gio, GLib)
    broker.owners = {":1.50"}
    target = {"pid": 123, "started": "800", "tty": "/dev/pts/7"}
    broker.pending[10] = (target, time.monotonic())

    with patch("agent_dock_bell.daemon.PIDLock.kill_existing") as mock_kill, \
         patch("agent_dock_bell.dock.set_dock_badge") as mock_badge:
        params = Mock()
        params.unpack.return_value = (10, 2)  # reason 2 = dismissed by user
        broker.on_signal(bus, ":1.50", "/org/freedesktop/Notifications",
                         "org.freedesktop.Notifications", "NotificationClosed", params, None)

        mock_kill.assert_called_once()
        mock_badge.assert_called_once_with("org.gnome.Terminal.desktop", 0, urgent=False)
        assert 10 not in broker.pending


def test_tty_marker_always_restores_title_on_failure():
    from agent_dock_bell.notification_broker import _find_frame_for_tty
    class Node:
        def __init__(self, name):
            self.name = name
        def get_role(self):
            return 1
        def get_name(self):
            return self.name
        def get_child_count(self):
            return 0
        def get_child_at_index(self, idx):
            return None

    frame = Node("MyWindow")
    writes = []
    def fake_write(fd, data):
        writes.append(data)
        return len(data)

    atspi = SimpleNamespace(Role=SimpleNamespace(PAGE_TAB=1))
    with patch("agent_dock_bell.notification_broker.os.open", return_value=7), \
         patch("agent_dock_bell.notification_broker.os.fstat", return_value=SimpleNamespace(st_mode=stat.S_IFCHR)), \
         patch("agent_dock_bell.notification_broker.os.write", side_effect=fake_write), \
         patch("agent_dock_bell.notification_broker.os.close"), \
         patch("agent_dock_bell.notification_broker.time.sleep"):
        res = _find_frame_for_tty([frame], "/dev/pts/9", atspi)
        assert res is None

    # Verify that marker was written, and the final write restored a clean title ("MyWindow")
    assert len(writes) >= 2
    assert b"agent-dock-bell-" in writes[0]
    assert b"MyWindow" in writes[-1]


def test_broker_reuses_replaces_id_for_same_agent():
    bus = Bus()
    gio = SimpleNamespace(DBusCallFlags=SimpleNamespace(NONE=0))
    glib = SimpleNamespace(Variant=Variant, VariantType=lambda sig: sig)
    broker = NotificationBroker(bus, gio, glib)

    with patch("agent_dock_bell.notification_broker._process_start_time", return_value="800"), \
         patch("agent_dock_bell.x11.is_terminal_active_atspi", return_value=False):
        # Call 1
        assert broker.notify({"agent": "Hermes", "target": {"pid": 123, "started": "800"}})
        notify_calls = [call for call in bus.calls if call[1] == "Notify"]
        assert len(notify_calls) == 1
        call1_params = notify_calls[0][2].unpack()
        assert call1_params[1] == 0  # First call has replaces_id = 0

        # Advance monotonic clock by 2s so debounce does not filter it
        with patch("time.monotonic", return_value=time.monotonic() + 2.0):
            assert broker.notify({"agent": "Hermes", "target": {"pid": 123, "started": "800"}})
            notify_calls2 = [call for call in bus.calls if call[1] == "Notify"]
            assert len(notify_calls2) == 2
            call2_params = notify_calls2[1][2].unpack()
            assert call2_params[1] == 77  # Second call replaces previous ID (77)!


def test_broker_debounces_rapid_notifications():
    bus = Bus()
    gio = SimpleNamespace(DBusCallFlags=SimpleNamespace(NONE=0))
    glib = SimpleNamespace(Variant=Variant, VariantType=lambda sig: sig)
    broker = NotificationBroker(bus, gio, glib)

    with patch("agent_dock_bell.notification_broker._process_start_time", return_value="800"), \
         patch("agent_dock_bell.x11.is_terminal_active_atspi", return_value=False):
        # Call 1 fires Notify
        assert broker.notify({"agent": "Hermes", "target": {"pid": 123, "started": "800"}})
        notify_calls = [call for call in bus.calls if call[1] == "Notify"]
        assert len(notify_calls) == 1

        # Rapid consecutive call (0.2s later) succeeds without re-firing Notify on DBus
        with patch("time.monotonic", return_value=time.monotonic() + 0.2):
            assert broker.notify({"agent": "Hermes", "target": {"pid": 123, "started": "800"}})
            notify_calls2 = [call for call in bus.calls if call[1] == "Notify"]
            assert len(notify_calls2) == 1  # No duplicate DBus Notify call!


def test_broker_suppresses_notification_when_terminal_active():
    bus = Bus()
    gio = SimpleNamespace(DBusCallFlags=SimpleNamespace(NONE=0))
    glib = SimpleNamespace(Variant=Variant, VariantType=lambda sig: sig)
    broker = NotificationBroker(bus, gio, glib)

    with patch("agent_dock_bell.notification_broker._process_start_time", return_value="800"), \
         patch("agent_dock_bell.x11.is_terminal_active_atspi", return_value=True):
        # When terminal is active, notify returns True but suppresses DBus notification
        assert broker.notify({"agent": "Hermes", "target": {"pid": 123, "started": "800"}})
        notify_calls = [call for call in bus.calls if call[1] == "Notify"]
        assert len(notify_calls) == 0


