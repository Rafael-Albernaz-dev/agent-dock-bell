import subprocess
from unittest.mock import patch
from agent_dock_bell.dock import send_notification, set_dock_badge, close_notification

def test_send_notification_command():
    with patch("subprocess.check_output", return_value=b"42\n") as mock_run:
        nid = send_notification("Hermes", "org.gnome.Terminal.desktop")
        assert nid == 42
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "notify-send" in cmd
        assert "-a" in cmd and "Hermes" in cmd
        assert "-t" in cmd and "5000" in cmd
        # Ensure desktop-entry hint is not passed so GNOME does not suppress the banner
        assert not any("string:desktop-entry:" in arg for arg in cmd)

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

def test_set_dock_badge():
    with patch("subprocess.run") as mock_run:
        set_dock_badge("org.gnome.Terminal.desktop", 1, urgent=True)
        assert mock_run.call_count == 2
