import io
import os
import signal
import sys
import threading
import time
import types
from unittest.mock import patch

from agent_dock_bell import daemon
from agent_dock_bell.daemon import PIDLock

def test_pid_lock_lifecycle(tmp_path, monkeypatch):
    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(daemon, "PID_FILE", str(pid_file))
    # Acquire lock
    assert PIDLock.acquire() is True
    assert pid_file.exists()

    with pid_file.open() as f:
        pid = int(f.read().strip())
    assert pid == os.getpid()

    # Release lock
    PIDLock.release()
    # Keep the inode stable so a concurrent daemon cannot lock a removed file.
    assert pid_file.exists()

def test_pid_lock_kill_existing(tmp_path, monkeypatch):
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("99999")
    monkeypatch.setattr(daemon, "PID_FILE", str(pid_file))
    monkeypatch.setattr(daemon, "LEGACY_PID_FILE", str(tmp_path / "missing.pid"))
    real_open = open

    def fake_open(path, mode="r", *args, **kwargs):
        if str(path) == "/proc/99999/cmdline":
            return io.BytesIO(b"python\0-m\0agent_dock_bell.cli\0--daemon\0")
        return real_open(path, mode, *args, **kwargs)

    with patch("builtins.open", side_effect=fake_open), \
         patch("os.stat", return_value=type("Stat", (), {"st_uid": os.getuid()})()), \
         patch("os.kill") as mock_kill:
        PIDLock.kill_existing()
        mock_kill.assert_called_once_with(99999, signal.SIGTERM)

def test_pid_lock_does_not_signal_reused_pid(tmp_path, monkeypatch):
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("99999")
    monkeypatch.setattr(daemon, "PID_FILE", str(pid_file))
    monkeypatch.setattr(daemon, "LEGACY_PID_FILE", str(tmp_path / "missing.pid"))
    real_open = open

    def fake_open(path, mode="r", *args, **kwargs):
        if str(path) == "/proc/99999/cmdline":
            return io.BytesIO(b"/usr/bin/unrelated\0")
        return real_open(path, mode, *args, **kwargs)

    with patch("builtins.open", side_effect=fake_open), \
         patch("os.stat", return_value=type("Stat", (), {"st_uid": os.getuid()})()), \
         patch("os.kill") as mock_kill:
        PIDLock.kill_existing()
    mock_kill.assert_not_called()

def test_run_daemon_atspi_exit():
    from unittest.mock import patch, MagicMock
    from agent_dock_bell.daemon import run_daemon

    with patch("agent_dock_bell.daemon.PIDLock.acquire", return_value=True), \
         patch("agent_dock_bell.daemon.PIDLock.release"), \
         patch("agent_dock_bell.daemon.AudioLoopWorker"), \
         patch("agent_dock_bell.daemon.set_dock_badge"), \
         patch("agent_dock_bell.daemon.set_window_urgency"), \
         patch("agent_dock_bell.daemon.close_display"), \
         patch("agent_dock_bell.daemon.is_terminal_active_atspi", return_value=True), \
         patch("sys.exit") as mock_exit:
        run_daemon("TestAgent", "org.gnome.Terminal.desktop", set(), {1234}, "bell", max_wait=2)
        mock_exit.assert_called_with(0)

def test_run_daemon_dead_process_exit():
    from unittest.mock import patch
    from agent_dock_bell.daemon import run_daemon

    with patch("agent_dock_bell.daemon.PIDLock.acquire", return_value=True), \
         patch("agent_dock_bell.daemon.PIDLock.release"), \
         patch("agent_dock_bell.daemon.AudioLoopWorker"), \
         patch("agent_dock_bell.daemon.set_dock_badge"), \
         patch("agent_dock_bell.daemon.set_window_urgency"), \
         patch("agent_dock_bell.daemon.close_display"), \
         patch("agent_dock_bell.daemon.is_terminal_active_atspi", return_value=False), \
         patch("os.kill", side_effect=OSError("No such process")), \
         patch("sys.exit") as mock_exit:
        run_daemon("TestAgent", "org.gnome.Terminal.desktop", set(), {99999}, "bell", max_wait=5)
        mock_exit.assert_called_with(0)

def test_atspi_listener_deregistered_and_loop_joined(monkeypatch):
    from agent_dock_bell.daemon import run_daemon

    class Listener:
        def __init__(self):
            self.registered = []
            self.deregistered = []

        def register(self, name):
            self.registered.append(name)

        def deregister(self, name):
            self.deregistered.append(name)

    class Loop:
        def __init__(self):
            self.stop = threading.Event()
            self.started = threading.Event()

        def run(self):
            self.started.set()
            self.stop.wait(2)

        def is_running(self):
            return self.started.is_set() and not self.stop.is_set()

        def quit(self):
            self.stop.set()

    listener = Listener()
    loop = Loop()
    gi = types.ModuleType("gi")
    gi.require_version = lambda *args: None
    repository = types.ModuleType("gi.repository")
    repository.Atspi = types.SimpleNamespace(
        init=lambda: None,
        EventListener=types.SimpleNamespace(new=lambda callback: listener),
    )
    repository.GLib = types.SimpleNamespace(MainLoop=lambda: loop)
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    with patch.dict(sys.modules, {"gi": gi, "gi.repository": repository}), \
         patch("agent_dock_bell.daemon.PIDLock.acquire", return_value=True), \
         patch("agent_dock_bell.daemon.PIDLock.release"), \
         patch("agent_dock_bell.daemon.AudioLoopWorker"), \
         patch("agent_dock_bell.daemon.set_dock_badge"), \
         patch("agent_dock_bell.daemon.send_notification"), \
         patch("agent_dock_bell.daemon.close_display"), \
         patch("os.kill", return_value=None), \
         patch("sys.exit"):
        run_daemon("Test", "org.gnome.Terminal.desktop", set(), {123}, "bell", max_wait=0)
    assert set(listener.registered) == set(listener.deregistered)
    assert len(listener.registered) == 3
    assert loop.stop.is_set()

def test_run_daemon_tty_activity_exit():
    from agent_dock_bell.daemon import run_daemon

    call_count = 0
    def fake_stat(path):
        nonlocal call_count
        call_count += 1
        atime = 100.0 if call_count <= 1 else 105.0
        return types.SimpleNamespace(st_mtime=50.0, st_atime=atime)

    with patch("agent_dock_bell.daemon.PIDLock.acquire", return_value=True), \
         patch("agent_dock_bell.daemon.PIDLock.release"), \
         patch("agent_dock_bell.daemon.AudioLoopWorker"), \
         patch("agent_dock_bell.daemon.set_dock_badge"), \
         patch("agent_dock_bell.daemon.set_window_urgency"), \
         patch("agent_dock_bell.daemon.close_display"), \
         patch("agent_dock_bell.daemon.is_terminal_active_atspi", return_value=False), \
         patch("os.path.exists", return_value=True), \
         patch("os.stat", side_effect=fake_stat), \
         patch("os.kill", return_value=None), \
         patch("sys.exit") as mock_exit:
        run_daemon("TestAgent", "org.gnome.Terminal.desktop", set(), {1234}, "bell", max_wait=5, term_ttys={"/dev/pts/1"})
        mock_exit.assert_called_with(0)

def test_run_daemon_exits_immediately_if_already_focused_on_startup():
    from unittest.mock import patch, MagicMock
    from agent_dock_bell.daemon import run_daemon

    mock_audio = MagicMock()
    with patch("agent_dock_bell.daemon.PIDLock.acquire", return_value=True), \
         patch("agent_dock_bell.daemon.PIDLock.release"), \
         patch("agent_dock_bell.daemon.AudioLoopWorker", return_value=mock_audio), \
         patch("agent_dock_bell.daemon.set_dock_badge") as mock_badge, \
         patch("agent_dock_bell.daemon.set_window_urgency") as mock_urgency, \
         patch("agent_dock_bell.daemon.send_notification") as mock_notify, \
         patch("agent_dock_bell.daemon.is_terminal_focused", return_value=True), \
         patch("agent_dock_bell.daemon.close_display"), \
         patch("sys.exit") as mock_exit:
        run_daemon("TestAgent", "org.gnome.Terminal.desktop", {123}, {456}, "bell")
        mock_exit.assert_called_with(0)
        mock_audio.start.assert_not_called()
        mock_notify.assert_not_called()


def test_run_daemon_atspi_triggered_event_immediately_exits_and_cleans_up():
    from unittest.mock import patch, MagicMock
    from agent_dock_bell.daemon import run_daemon

    captured_callback = []

    class Listener:
        def register(self, name):
            pass
        def deregister(self, name):
            pass

    class Loop:
        def run(self):
            pass
        def is_running(self):
            return False
        def quit(self):
            pass

    gi = types.ModuleType("gi")
    gi.require_version = lambda *args: None
    repository = types.ModuleType("gi.repository")

    def mock_new_listener(cb):
        captured_callback.append(cb)
        return Listener()

    repository.Atspi = types.SimpleNamespace(
        init=lambda: None,
        EventListener=types.SimpleNamespace(new=mock_new_listener),
    )
    repository.GLib = types.SimpleNamespace(MainLoop=lambda: Loop())

    mock_audio = MagicMock()
    with patch.dict(sys.modules, {"gi": gi, "gi.repository": repository}), \
         patch.dict(os.environ, {"DISPLAY": ""}), \
         patch("agent_dock_bell.daemon.PIDLock.acquire", return_value=True), \
         patch("agent_dock_bell.daemon.PIDLock.release"), \
         patch("agent_dock_bell.daemon.AudioLoopWorker", return_value=mock_audio), \
         patch("agent_dock_bell.daemon.set_dock_badge") as mock_badge, \
         patch("agent_dock_bell.daemon.set_window_urgency"), \
         patch("agent_dock_bell.daemon.send_notification"), \
         patch("agent_dock_bell.daemon.is_terminal_focused", return_value=False), \
         patch("agent_dock_bell.daemon.is_terminal_active_atspi", return_value=False), \
         patch("agent_dock_bell.daemon.close_display"), \
         patch("os.kill", return_value=None), \
         patch("sys.exit") as mock_exit:

        def trigger_user_click():
            time.sleep(0.05)
            assert captured_callback
            cb = captured_callback[0]
            # Simulate user clicking terminal: object:state-changed:focused with detail1=1 on pid 1234
            ev = types.SimpleNamespace(
                type="object:state-changed:focused",
                detail1=1,
                source=types.SimpleNamespace(get_process_id=lambda: 1234, get_application=lambda: None),
            )
            cb(ev)

        threading.Thread(target=trigger_user_click, daemon=True).start()
        run_daemon("TestAgent", "org.gnome.Terminal.desktop", set(), {1234}, "bell", max_wait=3)

        mock_exit.assert_called_with(0)
        # Verify dock badge was set to 1 initially, and cleared to 0 on exit
        badge_calls = [call.args for call in mock_badge.call_args_list]
        assert ("org.gnome.Terminal.desktop", 1) in [c[:2] for c in badge_calls]
        assert ("org.gnome.Terminal.desktop", 0) in [c[:2] for c in badge_calls]



