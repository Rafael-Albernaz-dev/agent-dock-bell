import os
from agent_dock_bell.daemon import PIDLock, PID_FILE

def test_pid_lock_lifecycle():
    # Acquire lock
    PIDLock.acquire()
    assert os.path.exists(PID_FILE)

    with open(PID_FILE) as f:
        pid = int(f.read().strip())
    assert pid == os.getpid()

    # Release lock
    PIDLock.release()
    assert not os.path.exists(PID_FILE)

def test_pid_lock_kill_existing():
    from unittest.mock import patch, mock_open

    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data="99999")), \
         patch("os.kill") as mock_kill, \
         patch("os.remove"), \
         patch("subprocess.check_output", return_value=b""), \
         patch("agent_dock_bell.daemon.kill_audio_processes") as mock_kill_audio:
        PIDLock.kill_existing()
        assert mock_kill.call_count >= 1
        assert mock_kill_audio.call_count == 1

def test_run_daemon_atspi_exit():
    from unittest.mock import patch, MagicMock
    from agent_dock_bell.daemon import run_daemon

    with patch("agent_dock_bell.daemon.PIDLock.acquire"), \
         patch("agent_dock_bell.daemon.PIDLock.release"), \
         patch("agent_dock_bell.daemon.AudioLoopWorker"), \
         patch("agent_dock_bell.daemon.set_dock_badge"), \
         patch("agent_dock_bell.daemon.set_window_urgency"), \
         patch("agent_dock_bell.daemon.reset_dock_dance"), \
         patch("agent_dock_bell.daemon.close_display"), \
         patch("agent_dock_bell.daemon.kill_audio_processes"), \
         patch("agent_dock_bell.daemon.is_terminal_active_atspi", return_value=True), \
         patch("sys.exit") as mock_exit:
        run_daemon("TestAgent", "org.gnome.Terminal.desktop", set(), {1234}, "bell", max_wait=2)
        mock_exit.assert_called_with(0)

def test_run_daemon_dead_process_exit():
    from unittest.mock import patch
    from agent_dock_bell.daemon import run_daemon

    with patch("agent_dock_bell.daemon.PIDLock.acquire"), \
         patch("agent_dock_bell.daemon.PIDLock.release"), \
         patch("agent_dock_bell.daemon.AudioLoopWorker"), \
         patch("agent_dock_bell.daemon.set_dock_badge"), \
         patch("agent_dock_bell.daemon.set_window_urgency"), \
         patch("agent_dock_bell.daemon.reset_dock_dance"), \
         patch("agent_dock_bell.daemon.close_display"), \
         patch("agent_dock_bell.daemon.kill_audio_processes"), \
         patch("agent_dock_bell.daemon.is_terminal_active_atspi", return_value=False), \
         patch("os.kill", side_effect=OSError("No such process")), \
         patch("sys.exit") as mock_exit:
        run_daemon("TestAgent", "org.gnome.Terminal.desktop", set(), {99999}, "bell", max_wait=5)
        mock_exit.assert_called_with(0)


