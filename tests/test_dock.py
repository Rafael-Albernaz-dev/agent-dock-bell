import stat
import sys
import types
from types import SimpleNamespace
from unittest.mock import patch

from agent_dock_bell.dock import (
    close_notification,
    send_notification,
    send_terminal_bell,
    set_dock_badge,
)

def test_send_notification_command():
    with patch("subprocess.check_output", return_value=b"42\n") as mock_run:
        nid = send_notification("Hermes", "org.gnome.Terminal.desktop")
        assert nid == 42
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "notify-send" in cmd
        assert "-a" in cmd and "Hermes" in cmd
        assert "-t" in cmd and "5000" in cmd
        assert "-i" in cmd and "org.gnome.Terminal" in cmd
        # Ensure desktop-entry hint is not passed so GNOME displays the global banner
        assert not any("string:desktop-entry:" in arg for arg in cmd)

def test_send_notification_nowait():
    with patch("subprocess.Popen") as mock_popen:
        nid = send_notification("Codex CLI", "org.gnome.Terminal.desktop", wait=False)
        assert nid is None
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert "notify-send" in cmd
        assert "-p" not in cmd
        assert not any("string:desktop-entry:" in arg for arg in cmd)

def test_gnome_terminal_click_broker_keeps_notification_sender_alive():
    with patch("agent_dock_bell.notification_broker.queue_terminal_notification", return_value=True) as broker, \
         patch("subprocess.check_output") as legacy:
        assert send_notification("Codex CLI", "org.gnome.Terminal.desktop", term_pids={123},
                                 term_ttys={"/dev/pts/7"}) is None
    broker.assert_called_once_with("Codex CLI", "org.gnome.Terminal.desktop", {123}, {"/dev/pts/7"})
    legacy.assert_not_called()

def test_gnome_terminal_click_broker_failure_preserves_banner():
    with patch("agent_dock_bell.notification_broker.queue_terminal_notification", return_value=False), \
         patch("subprocess.check_output", return_value=b"42\n") as legacy:
        assert send_notification("Codex CLI", "org.gnome.Terminal.desktop", term_pids={123}) == 42
    assert not any("string:desktop-entry:" in arg for arg in legacy.call_args.args[0])
    assert "boolean:suppress-sound:true" in legacy.call_args.args[0]

def test_close_notification():
    with patch("subprocess.run") as mock_run:
        close_notification(42)
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "CloseNotification" in args[-2]
        assert "42" in args[-1]

def test_close_notification_none():
    with patch("subprocess.run") as mock_run:
        close_notification(None)
        mock_run.assert_not_called()

def test_send_terminal_bell_slave_tty():
    with patch("os.open", return_value=7) as mock_open, \
         patch("os.fstat", return_value=SimpleNamespace(st_mode=stat.S_IFCHR)), \
         patch("os.write", return_value=1) as mock_write, \
         patch("os.close") as mock_close:
        send_terminal_bell({"/dev/pts/3"})
        assert mock_open.call_args.args[0] == "/dev/pts/3"
        mock_write.assert_called_once_with(7, b"\a")
        mock_close.assert_called_once_with(7)

def test_send_terminal_bell_fallback_to_dev_tty():
    with patch("os.open", return_value=7) as mock_open, \
         patch("os.fstat", return_value=SimpleNamespace(st_mode=stat.S_IFCHR)), \
         patch("os.write", return_value=1) as mock_write, \
         patch("os.close"):
        send_terminal_bell(set())
        assert mock_open.call_args.args[0] == "/dev/tty"
        mock_write.assert_called_once_with(7, b"\a")

def test_send_terminal_bell_rejects_regular_file():
    with patch("os.open", return_value=7) as mock_open, \
         patch("os.fstat", return_value=SimpleNamespace(st_mode=stat.S_IFREG)), \
         patch("os.write") as mock_write, \
         patch("os.close"):
        send_terminal_bell({"/tmp/important"})
        assert mock_open.call_args.args[0] == "/dev/tty"
        mock_write.assert_not_called()

def test_set_dock_badge_subprocess_fallback():
    # In environments where gi / PyGObject is not available (like isolated test venv)
    with patch("subprocess.run") as mock_run:
        set_dock_badge("org.gnome.Terminal.desktop", 1, urgent=True)
        assert mock_run.call_count == 2
        calls = [c[0][0] for c in mock_run.call_args_list]
        assert any("application://org.gnome.Terminal.desktop" in arg for cmd in calls for arg in cmd)
        assert any("org.gnome.Terminal.desktop" in arg for cmd in calls for arg in cmd)

def test_set_dock_badge_reuses_gio_connection():
    from agent_dock_bell import dock

    class Bus:
        def __init__(self):
            self.signals = []
            self.flushes = 0

        def is_closed(self):
            return False

        def emit_signal(self, *args):
            self.signals.append(args)

        def flush_sync(self, cancellable):
            self.flushes += 1

    bus = Bus()
    calls = []
    gio = SimpleNamespace(BusType=SimpleNamespace(SESSION=1),
                          bus_get_sync=lambda *args: (calls.append(args), bus)[1])
    glib = SimpleNamespace(Variant=lambda signature, value: (signature, value))
    gi = types.ModuleType("gi")
    gi.require_version = lambda *args: None
    repository = types.ModuleType("gi.repository")
    repository.Gio = gio
    repository.GLib = glib
    old_bus = dock._session_bus
    try:
        dock._session_bus = None
        with patch.dict(sys.modules, {"gi": gi, "gi.repository": repository}), \
             patch("subprocess.run") as fallback:
            set_dock_badge("org.gnome.Terminal.desktop", 1, urgent=True)
            set_dock_badge("org.gnome.Terminal.desktop", 0, urgent=False)
        assert len(calls) == 1
        assert len(bus.signals) == 4
        assert bus.flushes == 1
        fallback.assert_not_called()
    finally:
        dock._session_bus = old_bus
