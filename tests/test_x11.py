import ctypes
import sys
import types
from unittest.mock import patch
from agent_dock_bell.x11 import to_int, get_ancestor_pids

def test_x11_hints_are_freed_after_urgency_update():
    from agent_dock_bell import x11

    hints = x11.XWMHints()

    class FakeX11:
        def __init__(self):
            self.freed = []

        def XGetWMHints(self, display, wid):
            return ctypes.pointer(hints)

        def XSetWMHints(self, display, wid, value):
            pass

        def XFlush(self, display):
            pass

        def XFree(self, value):
            self.freed.append(value)

    fake = FakeX11()
    with patch.object(x11, "_x11", fake), patch.object(x11, "_display", 123):
        x11.set_window_urgency(10, True)
    assert hints.flags & x11.XUrgencyHint
    assert len(fake.freed) == 1

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
    # PID 1 is not a terminal and must not enter focus/liveness filters.
    assert 1 not in ancestors

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

def test_detect_terminal_process_codex_not_mistaken_for_code():
    from unittest.mock import patch, mock_open
    from agent_dock_bell.x11 import detect_terminal_process

    def mock_exists(path):
        return True

    def mock_file_open(path, *args, **kwargs):
        if "/proc/111/comm" in str(path):
            return mock_open(read_data="codex\n")()
        elif "/proc/111/cmdline" in str(path):
            return mock_open(read_data=b"/usr/bin/codex\0--arg\0")()
        elif "/proc/222/comm" in str(path):
            return mock_open(read_data="gnome-terminal-\n")()
        elif "/proc/222/cmdline" in str(path):
            return mock_open(read_data=b"/usr/libexec/gnome-terminal-server\0")()
        return mock_open(read_data="")()

    with patch("os.path.exists", side_effect=mock_exists), \
         patch("builtins.open", side_effect=mock_file_open):
        pids, desktop_id = detect_terminal_process({111, 222})
        # 111 (codex) should NOT match 'code.desktop'
        # 222 (gnome-terminal-server) should match 'org.gnome.Terminal.desktop'
        assert 111 not in pids
        assert 222 in pids
        assert desktop_id == "org.gnome.Terminal.desktop"

def test_detect_terminal_process_chatgpt():
    from unittest.mock import patch, mock_open
    from agent_dock_bell.x11 import detect_terminal_process

    def mock_exists(path):
        return True

    def mock_file_open(path, *args, **kwargs):
        if "/proc/333/comm" in str(path):
            return mock_open(read_data="chatgpt\n")()
        elif "/proc/333/cmdline" in str(path):
            return mock_open(read_data=b"/usr/lib/chatgpt/ChatGPT\0")()
        return mock_open(read_data="")()

    with patch("os.path.exists", side_effect=mock_exists), \
         patch("builtins.open", side_effect=mock_file_open):
        pids, desktop_id = detect_terminal_process({333})
        assert 333 in pids
        assert desktop_id == "chatgpt.desktop"

def test_native_terminal_excludes_embedding_electron_pid():
    from agent_dock_bell.x11 import detect_terminal_process
    from unittest.mock import mock_open

    def fake_open(path, *args, **kwargs):
        values = {
            "/proc/111/comm": "chatgpt\n",
            "/proc/222/comm": "gnome-terminal-\n",
            "/proc/111/cmdline": b"/usr/lib/chatgpt/chatgpt\0",
            "/proc/222/cmdline": b"/usr/libexec/gnome-terminal-server\0",
        }
        return mock_open(read_data=values.get(str(path), ""))()

    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", side_effect=fake_open):
        pids, desktop_id = detect_terminal_process({111, 222})
    assert pids == {222}
    assert desktop_id == "org.gnome.Terminal.desktop"

def test_atspi_ignores_background_webview_and_checks_each_top_level_window():
    from agent_dock_bell.x11 import is_terminal_active_atspi

    role = types.SimpleNamespace(FRAME=1, WINDOW=2, DIALOG=3)
    state = types.SimpleNamespace(ACTIVE=1, FOCUSED=2)

    class Window:
        def __init__(self, window_role, states):
            self.window_role = window_role
            self.states = states

        def get_role(self):
            return self.window_role

        def get_state_set(self):
            return self

        def contains(self, value):
            return value in self.states

    class App:
        def __init__(self, pid, children):
            self.pid = pid
            self.children = children
            self.visited = False

        def get_process_id(self):
            return self.pid

        def get_name(self):
            return "ChatGPT"

        def get_child_count(self):
            self.visited = True
            return len(self.children)

        def get_child_at_index(self, index):
            return self.children[index]

    other = App(999, [Window(role.FRAME, {state.FOCUSED})])
    target = App(123, [Window(4, {state.ACTIVE}), Window(role.FRAME, {state.FOCUSED})])
    desktop = types.SimpleNamespace(
        get_child_count=lambda: 2,
        get_child_at_index=lambda index: [other, target][index],
    )
    atspi = types.SimpleNamespace(Role=role, StateType=state, init=lambda: None,
                                  get_desktop=lambda index: desktop)
    gi = types.ModuleType("gi")
    gi.require_version = lambda *args: None
    repository = types.ModuleType("gi.repository")
    repository.Atspi = atspi
    with patch.dict(sys.modules, {"gi": gi, "gi.repository": repository}):
        assert is_terminal_active_atspi({123}) is True
        assert other.visited is False
        target.children[1] = Window(role.FRAME, set())
        assert is_terminal_active_atspi({123}) is False
        assert is_terminal_active_atspi(set()) is False

def test_x11_window_discovery_does_not_claim_unrelated_terminal():
    from agent_dock_bell.x11 import find_terminal_windows

    def xprop(args, **kwargs):
        if "-root" in args:
            return b"_NET_CLIENT_LIST(WINDOW): window id # 0x123\n"
        return b'_NET_WM_PID(CARDINAL) = 999\nWM_CLASS(STRING) = "gnome-terminal"\n'

    with patch("agent_dock_bell.x11.detect_terminal_process", return_value=(set(), "org.gnome.Terminal.desktop")), \
         patch("subprocess.check_output", side_effect=xprop), \
         patch.dict("os.environ", {"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11"}):
        windows, pids, _ = find_terminal_windows({111})
    assert windows == set()
    assert pids == set()

def test_atspi_electron_active_frame_detected_as_focused():
    """Verify that Electron/ChatGPT top-level frame with ACTIVE state is detected as active."""
    from agent_dock_bell.x11 import is_terminal_active_atspi

    role = types.SimpleNamespace(FRAME=1, WINDOW=2, DIALOG=3)
    state = types.SimpleNamespace(ACTIVE=1, FOCUSED=2)

    class Window:
        def __init__(self, window_role, states):
            self.window_role = window_role
            self.states = states

        def get_role(self):
            return self.window_role

        def get_state_set(self):
            return self

        def contains(self, value):
            return value in self.states

    class App:
        def __init__(self, pid, children):
            self.pid = pid
            self.children = children

        def get_process_id(self):
            return self.pid

        def get_name(self):
            return "ChatGPT"

        def get_child_count(self):
            return len(self.children)

        def get_child_at_index(self, index):
            return self.children[index]

    # ChatGPT frame has ACTIVE but not FOCUSED (standard Electron frame state on Linux)
    chatgpt = App(933148, [Window(role.FRAME, {state.ACTIVE})])
    desktop = types.SimpleNamespace(
        get_child_count=lambda: 1,
        get_child_at_index=lambda index: [chatgpt][index],
    )
    atspi = types.SimpleNamespace(Role=role, StateType=state, init=lambda: None,
                                  get_desktop=lambda index: desktop)
    gi = types.ModuleType("gi")
    gi.require_version = lambda *args: None
    repository = types.ModuleType("gi.repository")
    repository.Atspi = atspi
    with patch.dict(sys.modules, {"gi": gi, "gi.repository": repository}):
        assert is_terminal_active_atspi({933148}) is True

def test_x11_window_discovery_on_wayland_with_display():
    """Verify XWayland windows are discovered under Wayland when DISPLAY is available."""
    from agent_dock_bell.x11 import find_terminal_windows

    def xprop(args, **kwargs):
        if "-root" in args:
            return b"_NET_CLIENT_LIST(WINDOW): window id # 0x1200004\n"
        return b'_NET_WM_PID(CARDINAL) = 933148\nWM_CLASS(STRING) = "chatgpt", "Chatgpt"\n'

    with patch("agent_dock_bell.x11.detect_terminal_process", return_value=({933148}, "chatgpt.desktop")), \
         patch("subprocess.check_output", side_effect=xprop), \
         patch.dict("os.environ", {"DISPLAY": ":0", "XDG_SESSION_TYPE": "wayland"}):
        windows, pids, desktop_id = find_terminal_windows({933148})
    assert 0x1200004 in windows
    assert 933148 in pids
    assert desktop_id == "chatgpt.desktop"

def test_is_terminal_focused_resolves_pid_from_xprop():
    """Verify is_terminal_focused checks _NET_WM_PID for active window not yet in term_wids."""
    from agent_dock_bell.x11 import is_terminal_focused

    def fake_xprop(args, **kwargs):
        return b"_NET_WM_PID(CARDINAL) = 933148\n"

    term_wids = set()
    with patch("subprocess.check_output", side_effect=fake_xprop):
        assert is_terminal_focused(term_wids, {933148}, current_active_wid=0x1200004) is True
    assert 0x1200004 in term_wids

