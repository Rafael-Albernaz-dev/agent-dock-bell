import pytest
from agent_dock_bell.x11 import to_int, get_ancestor_pids

def test_to_int_normalization():
    assert to_int("0x3c0000a") == 62914570
    assert to_int("0X3C0000A") == 62914570
    assert to_int(62914570) == 62914570
    assert to_int("12345") == 12345
    assert to_int(None) is None
    assert to_int("not_a_number") is None

def test_get_ancestor_pids():
    ancestors = get_ancestor_pids()
    assert isinstance(ancestors, set)
    # PID 1 (init / systemd) is always in the ancestry tree
    assert 1 in ancestors

def test_detect_terminal_process():
    from unittest.mock import patch, mock_open
    from agent_dock_bell.x11 import detect_terminal_process

    # Test with simulated gnome-terminal process
    def mock_exists(path):
        return "/proc/999/comm" in path

    def mock_file_open(path, *args, **kwargs):
        if "/proc/999/comm" in str(path):
            return mock_open(read_data="gnome-terminal-server\n")()
        return mock_open(read_data="")()

    with patch("os.path.exists", side_effect=mock_exists), \
         patch("builtins.open", side_effect=mock_file_open):
        pids, desktop_id = detect_terminal_process({999})
        assert 999 in pids
        assert desktop_id == "org.gnome.Terminal.desktop"

def test_get_ancestor_ttys():
    from unittest.mock import patch
    from agent_dock_bell.x11 import get_ancestor_ttys

    with patch("os.path.isdir", return_value=True), \
         patch("os.listdir", return_value=["0", "1", "2"]), \
         patch("os.readlink", side_effect=["/dev/pts/5", "/dev/pts/5", "/dev/null"]):
        ttys = get_ancestor_ttys({1234})
        assert "/dev/pts/5" in ttys
        assert "/dev/null" not in ttys

def test_is_terminal_focused():
    from unittest.mock import patch
    from agent_dock_bell.x11 import is_terminal_focused

    # X11 matching window
    assert is_terminal_focused({100, 200}, {500}, current_active_wid=100) is True

    # X11 mismatch, AT-SPI active
    with patch("agent_dock_bell.x11.is_terminal_active_atspi", return_value=True):
        assert is_terminal_focused({100, 200}, {500}, current_active_wid=300) is True

    # Both inactive
    with patch("agent_dock_bell.x11.is_terminal_active_atspi", return_value=False):
        assert is_terminal_focused({100, 200}, {500}, current_active_wid=300) is False
        assert is_terminal_focused(set(), {500}, current_active_wid=0) is False

