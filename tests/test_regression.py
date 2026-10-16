"""
Automated Regression Test Suite for agent-dock-bell.
Each test in this file guards against a specific real-world regression that occurred
in past commits, ensuring no "gaming" through overly broad mocks.
"""

import os
import time
import types
from pathlib import Path
from unittest.mock import patch, MagicMock

import agent_dock_bell
from agent_dock_bell.audio import (
    ASSETS_DIR,
    BUNDLED_NEW_SOUND,
    DEFAULT_SOUND,
    PRESET_SOUNDS,
    get_sound_cmd,
    resolve_sound_file,
)
from agent_dock_bell.cli import should_skip_event, build_daemon_environment
from agent_dock_bell.daemon import PIDLock, run_daemon
from agent_dock_bell.dock import send_notification


def test_regression_sound_resolves_to_new_configured_mp3():
    """
    REGRESSION GUARD (Commit de264d1 regression):
    Past commit hardcoded DEFAULT_SOUND to a non-existent Downloads path, causing
    silent fallback to the old Ubuntu Yaru bell (/usr/share/sounds/Yaru/stereo/bell.oga).
    The resolver MUST find the bundled asset or user sound and NEVER fall back to bell.oga
    when the new notification audio exists.
    """
    resolved = resolve_sound_file()
    assert "universfield-new-notification-02-323592.mp3" in resolved, (
        f"Expected new universfield mp3, got: {resolved}"
    )
    assert os.path.isfile(resolved), f"Resolved file does not exist: {resolved}"
    assert os.path.getsize(resolved) > 10000, f"Sound file is suspiciously empty: {resolved}"
    assert "bell.oga" not in resolved


def test_regression_preset_sounds_include_new_and_universfield():
    """
    REGRESSION GUARD:
    Preset mappings must include 'new' and 'universfield' pointing to the configured chime.
    """
    assert "new" in PRESET_SOUNDS
    assert "universfield" in PRESET_SOUNDS
    assert os.path.exists(PRESET_SOUNDS["new"])
    assert resolve_sound_file("new") == PRESET_SOUNDS["new"]
    assert resolve_sound_file("universfield") == PRESET_SOUNDS["universfield"]


def test_regression_environment_override_audio():
    """
    REGRESSION GUARD:
    Explicit AGENT_ALARM_SOUND environment variable must override the default sound.
    """
    with patch.dict(os.environ, {"AGENT_ALARM_SOUND": "pop"}):
        if os.path.exists(PRESET_SOUNDS["pop"]):
            assert resolve_sound_file() == PRESET_SOUNDS["pop"]

    with patch.dict(os.environ, {"AGENT_ALARM_SOUND": BUNDLED_NEW_SOUND}):
        assert resolve_sound_file() == BUNDLED_NEW_SOUND


def test_regression_tty_mtime_does_not_falsely_terminate_daemon():
    """
    REGRESSION GUARD (Commit 8955890 regression):
    Checking st_mtime on pseudo-terminals causes false triggers because the agent
    or shell writes final output to the terminal, bumping mtime immediately after
    the daemon starts. Only st_atime (reads/keystrokes) or window focus should trigger exit.
    """
    call_count = 0

    def fake_stat(path):
        nonlocal call_count
        call_count += 1
        # mtime changes continuously on every call
        mtime = 100.0 + (call_count * 10.0)
        # atime only increases on call 4 (iteration 3)
        atime = 50.0 if call_count < 4 else 60.0
        return types.SimpleNamespace(st_mtime=mtime, st_atime=atime)

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
         patch("time.sleep"), \
         patch("sys.exit") as mock_exit:
        run_daemon("TestAgent", "org.gnome.Terminal.desktop", set(), {1234}, "new",
                   max_wait=5.0, term_ttys={"/dev/pts/1"})
        # If mtime triggered exit, call_count would be 2.
        # Since it survived mtime changes and waited for atime, call_count is >= 4.
        assert call_count >= 4
        mock_exit.assert_called_with(0)


def test_regression_tty_atime_triggers_clean_exit():
    """
    REGRESSION GUARD:
    When user enters keystrokes on the TTY (st_atime updates), the daemon MUST exit cleanly.
    """
    call_count = 0

    def fake_stat(path):
        nonlocal call_count
        call_count += 1
        # atime increases on 2nd stat call
        atime = 50.0 if call_count <= 1 else 60.0
        return types.SimpleNamespace(st_mtime=100.0, st_atime=atime)

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
        run_daemon("TestAgent", "org.gnome.Terminal.desktop", set(), {1234}, "new",
                   max_wait=2.0, term_ttys={"/dev/pts/1"})
        mock_exit.assert_called_with(0)


def test_regression_notification_omits_desktop_entry_hint():
    """
    REGRESSION GUARD (Commit 8955890 regression):
    desktop-entry hint in notify-send suppresses the top banner in GNOME Shell.
    Banner notifications must omit string:desktop-entry to display as a top popup.
    """
    with patch("subprocess.check_output", return_value=b"77\n") as mock_run:
        nid = send_notification("Antigravity", "code.desktop", wait=True)
        assert nid == 77
        cmd = mock_run.call_args[0][0]
        assert not any("string:desktop-entry:" in arg for arg in cmd), (
            f"Found forbidden desktop-entry hint in cmd: {cmd}"
        )
        assert "-u" in cmd and "normal" in cmd
        assert "-h" in cmd and "boolean:suppress-sound:true" in cmd


def test_regression_subagent_events_are_skipped():
    """
    REGRESSION GUARD (Commit 8955890 regression):
    Background reviews and subagent runs must not trigger the main completion chime.
    """
    # 1. Antigravity subagent
    assert should_skip_event("", {"parent_session_id": "abc-123"}, []) is True
    assert should_skip_event("", {"is_subagent": True}, []) is True
    assert should_skip_event("", {"is_background": True}, []) is True

    # 2. Background review origin
    assert should_skip_event("", {"write_origin": "background_review"}, []) is True
    assert should_skip_event("", {"extra": {"write_origin": "review"}}, []) is True

    # 3. Intermediate tool use
    assert should_skip_event("", {"stop_reason": "tool_use"}, []) is True

    # 4. Legit final completion turn
    assert should_skip_event("", {"fullyIdle": True, "stop_reason": "end_turn"}, []) is False


def test_regression_chatgpt_ui_never_triggers_alert():
    """
    REGRESSION GUARD:
    ChatGPT UI already has its own completion sound and notifications.
    agent-dock-bell must NEVER play sound, send desktop banners, or fork daemons
    when invoked from ChatGPT UI or with ChatGPT in args/env/ancestry.
    """
    from unittest.mock import mock_open
    import sys

    # 1. By CLI argument
    assert should_skip_event("", None, ["ChatGPT UI"]) is True
    assert should_skip_event("", None, ["ChatGPT"]) is True

    # 2. By environment variables inherited from ChatGPT Desktop App
    with patch.dict(os.environ, {"CHROME_DESKTOP": "chatgpt.desktop"}):
        assert should_skip_event("", None, ["Codex CLI"]) is True

    with patch.dict(os.environ, {"CODEX_INTERNAL_ORIGINATOR_OVERRIDE": "Codex Desktop"}):
        assert should_skip_event("", None, ["Codex CLI"]) is True

    with patch.dict(os.environ, {"CODEX_CLI_PATH": "/usr/lib/chatgpt/resources/codex"}):
        assert should_skip_event("", None, ["Codex CLI"]) is True

    # 3. By payload
    assert should_skip_event("", {"originator": "Codex Desktop"}, ["Codex CLI"]) is True
    assert should_skip_event("", {"client": "chatgpt"}, ["Codex CLI"]) is True

    # 4. By process ancestry
    def fake_open(path, *args, **kwargs):
        if "/proc/999/comm" in str(path):
            return mock_open(read_data="ChatGPT\n")()
        elif "/proc/999/cmdline" in str(path):
            return mock_open(read_data=b"/usr/lib/chatgpt/ChatGPT\0")()
        return mock_open(read_data="")()

    with patch("agent_dock_bell.x11.get_ancestor_pids", return_value={999}), \
         patch("os.path.exists", return_value=True), \
         patch("builtins.open", side_effect=fake_open):
        assert should_skip_event("", None, ["Codex CLI"]) is True

    # 5. Full CLI execution exits 0 with no audio or subprocesses
    from agent_dock_bell.cli import main
    with patch.object(sys, "argv", ["agent-dock-bell", "ChatGPT UI"]), \
         patch("agent_dock_bell.cli.read_hook_stdin", return_value=("", None)), \
         patch("agent_dock_bell.audio.play_single_sound") as mock_sound, \
         patch("agent_dock_bell.dock.send_notification") as mock_notify, \
         patch("subprocess.Popen") as mock_popen:
        try:
            main()
        except SystemExit as exc:
            assert exc.code == 0
    mock_sound.assert_not_called()
    mock_notify.assert_not_called()
    mock_popen.assert_not_called()


def test_regression_electron_multi_frame_prevents_infinite_audio_loop():
    """
    REGRESSION GUARD:
    Electron applications (ChatGPT, VS Code) expose multiple top-level frames in AT-SPI
    (len(frames) > 1) without any Role.TERMINAL widget in the tree.
    Previously, multi-frame fallback required Role.TERMINAL, which caused AT-SPI
    to never recognize that the user focused ChatGPT, making the audio play infinitely.
    The focus detector must recognize the active frame, and run_daemon must exit cleanly.
    """
    import sys
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

        def get_child_count(self):
            return 0

        def get_child_at_index(self, index):
            return None

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

    # Real-world 4-frame Electron structure without Role.TERMINAL
    active_win = Window(role.FRAME, {state.ACTIVE})
    chatgpt = App(933148, [
        Window(role.FRAME, set()),
        active_win,
        Window(role.FRAME, set()),
        Window(role.FRAME, set()),
    ])
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
        # Must detect active frame despite len(frames) == 4 and no Role.TERMINAL
        assert is_terminal_active_atspi({933148}) is True

    # Verify run_daemon terminates cleanly without infinite loop when window is active
    with patch("agent_dock_bell.daemon.PIDLock.acquire", return_value=True), \
         patch("agent_dock_bell.daemon.PIDLock.release"), \
         patch("agent_dock_bell.daemon.AudioLoopWorker") as mock_audio_cls, \
         patch("agent_dock_bell.daemon.set_dock_badge"), \
         patch("agent_dock_bell.daemon.set_window_urgency"), \
         patch("agent_dock_bell.daemon.close_display"), \
         patch("agent_dock_bell.daemon.is_terminal_focused", return_value=True), \
         patch("sys.exit") as mock_exit:
        run_daemon("Codex CLI", "chatgpt.desktop", set(), {933148}, "new")
        mock_exit.assert_called_with(0)
        mock_audio_cls.return_value.start.assert_not_called()


def test_regression_hermes_tool_and_background_executions_skipped():
    """
    REGRESSION GUARD:
    Hermes Agent executes intermediate tool calls (terminal, execute_code, delegate_task)
    and background reviews/subagents during prompt processing.
    agent-dock-bell must NEVER sound the alarm mid-prompt on tool executions,
    background reviews, subagents, or failed turns.
    Only genuine text response completions on session end must trigger.
    """
    # 1. Hermes tool execution hooks (pre/post tool calls)
    tool_payload = {
        "hook_event_name": "post_tool_call",
        "tool_name": "execute_code",
        "tool_input": {"code": "print(1)"},
        "session_id": "sess-hermes-1",
        "cwd": "/tmp",
    }
    assert should_skip_event("", tool_payload, ["Hermes"]) is True

    terminal_tool_payload = {
        "hook_event_name": "pre_tool_call",
        "tool_name": "terminal",
        "session_id": "sess-hermes-1",
    }
    assert should_skip_event("", terminal_tool_payload, ["Hermes"]) is True

    # 2. Hermes background reviews and subagents
    subagent_payload = {
        "hook_event_name": "on_session_end",
        "session_id": "sess-hermes-1",
        "extra": {
            "task_id": "subagent-0-8a7c2e",
            "turn_id": "sess-hermes-1:subagent-0:1",
            "completed": True,
            "failed": False,
            "turn_exit_reason": "text_response(finish_reason=stop)",
        },
    }
    assert should_skip_event("", subagent_payload, ["Hermes"]) is True

    bg_task_payload = {
        "hook_event_name": "on_session_end",
        "session_id": "sess-hermes-1",
        "extra": {
            "task_id": "bg-review-task-42",
            "completed": True,
            "failed": False,
        },
    }
    assert should_skip_event("", bg_task_payload, ["Hermes"]) is True

    # 3. Hermes abnormal halts and failures
    guardrail_payload = {
        "hook_event_name": "on_session_end",
        "session_id": "sess-hermes-1",
        "extra": {
            "completed": False,
            "turn_exit_reason": "guardrail_halt",
        },
    }
    assert should_skip_event("", guardrail_payload, ["Hermes"]) is True

    persistence_failed_payload = {
        "hook_event_name": "on_session_end",
        "session_id": "sess-hermes-1",
        "extra": {
            "completed": True,
            "failed": True,
            "turn_exit_reason": "session_persistence_failed",
        },
    }
    assert should_skip_event("", persistence_failed_payload, ["Hermes"]) is True

    budget_exhausted_payload = {
        "hook_event_name": "on_session_end",
        "session_id": "sess-hermes-1",
        "extra": {
            "completed": False,
            "turn_exit_reason": "budget_exhausted",
        },
    }
    assert should_skip_event("", budget_exhausted_payload, ["Hermes"]) is True

    # 4. Legit Hermes text response completion must NOT be skipped
    with patch("agent_dock_bell.x11.get_ancestor_process_chain", return_value=[]):
        legit_payload = {
            "hook_event_name": "on_session_end",
            "session_id": "sess-hermes-1",
            "extra": {
                "task_id": "3b29c99e-71da-45e0-8263-fb713b118b67",
                "turn_id": "sess-hermes-1:3b29c99e:8a12bc",
                "completed": True,
                "failed": False,
                "interrupted": False,
                "turn_exit_reason": "text_response(finish_reason=stop)",
            },
        }
        assert should_skip_event("", legit_payload, ["Hermes"]) is False


def test_regression_nested_agent_tool_execution_skipped():
    """
    REGRESSION GUARD (User reported bug):
    When Hermes Agent invokes 'codex exec' as a terminal tool call during a prompt turn,
    codex triggers its stop hook upon finishing, calling 'agent-dock-bell "Codex CLI"'.
    Previously, agent-dock-bell saw that the terminal was away and forked a daemon,
    ringing the bell mid-prompt while Hermes still had ~10 turns left to complete.
    agent-dock-bell must detect that Codex is running under Hermes (nested execution)
    and cleanly exit 0 without playing audio, sending notifications, or forking daemons.
    Conversely, standalone Codex executions must continue alerting normally.
    """
    import sys
    from agent_dock_bell.cli import main

    # Exact real-world process hierarchy observed on user's machine:
    # PID 595390: agent-dock-bell
    # PID 595389: bash (codex-cli-alarm.sh)
    # PID 595387: codex (codex exec --sandbox workspace-write ...)
    # PID 133459: hermes (/home/toru/.hermes/hermes-agent/venv/bin/python /home/toru/.hermes/hermes-agent/hermes)
    # PID 1000:   bash
    # PID 500:    gnome-terminal-server
    hermes_ancestor_chain = [
        (595389, "bash", b"/bin/bash\0/home/toru/.local/bin/codex-cli-alarm.sh\0Codex CLI\0"),
        (595387, "codex", b"/home/toru/.local/bin/codex\0exec\0--sandbox\0workspace-write\0"),
        (133459, "hermes", b"/home/toru/.hermes/hermes-agent/venv/bin/python\0/home/toru/.hermes/hermes-agent/hermes\0"),
        (1000, "bash", b"/bin/bash\0"),
        (500, "gnome-terminal-server", b"/usr/libexec/gnome-terminal-server\0"),
    ]

    codex_stop_payload = {
        "hook_event_name": "Stop",
        "session_id": "codex-child-sess",
        "stop_reason": "end_turn",
    }

    # 1. should_skip_event must detect Hermes above Codex and return True
    with patch("agent_dock_bell.x11.get_ancestor_process_chain", return_value=hermes_ancestor_chain):
        assert should_skip_event("", codex_stop_payload, ["Codex CLI"]) is True

    # 2. Full main() execution must exit 0 with NO audio, NO notification, and NO forked daemon
    with patch.object(sys, "argv", ["agent-dock-bell", "Codex CLI"]), \
         patch("agent_dock_bell.x11.get_ancestor_process_chain", return_value=hermes_ancestor_chain), \
         patch("agent_dock_bell.cli.read_hook_stdin", return_value=("", codex_stop_payload)), \
         patch("agent_dock_bell.audio.play_single_sound") as mock_sound, \
         patch("agent_dock_bell.dock.send_notification") as mock_notify, \
         patch("subprocess.Popen") as mock_popen:
        try:
            main()
        except SystemExit as exc:
            assert exc.code == 0
    mock_sound.assert_not_called()
    mock_notify.assert_not_called()
    mock_popen.assert_not_called()

    # 3. Antigravity ancestor spawning Codex must also be skipped
    antigravity_ancestor_chain = [
        (595387, "codex", b"/home/toru/.local/bin/codex\0exec\0"),
        (4000, "antigravity", b"/usr/bin/antigravity\0"),
    ]
    with patch("agent_dock_bell.x11.get_ancestor_process_chain", return_value=antigravity_ancestor_chain):
        assert should_skip_event("", codex_stop_payload, ["Codex CLI"]) is True

    # 4. Standalone Codex (user running codex directly in terminal) must NOT be skipped
    standalone_codex_chain = [
        (595389, "bash", b"/bin/bash\0/home/toru/.local/bin/codex-cli-alarm.sh\0Codex CLI\0"),
        (595387, "codex", b"/home/toru/.local/bin/codex\0"),
        (1000, "bash", b"/bin/bash\0"),
        (500, "gnome-terminal-server", b"/usr/libexec/gnome-terminal-server\0"),
    ]
    with patch("agent_dock_bell.x11.get_ancestor_process_chain", return_value=standalone_codex_chain):
        assert should_skip_event("", codex_stop_payload, ["Codex CLI"]) is False


def test_regression_terminal_click_or_banner_click_dismisses_alarm_and_clears_badge():
    """
    REGRESSION GUARD:
    Users frequently experienced the sound and dock badge continuing indefinitely
    even after having clicked on the terminal window or notification banner.
    Verify:
    1. Clicking anywhere on the terminal window stops daemon & resets dock badge to 0.
    2. Clicking the notification banner stops daemon & resets dock badge to 0.
    3. Dismissing the notification banner stops daemon & resets dock badge to 0.
    """
    from agent_dock_bell.daemon import run_daemon
    from agent_dock_bell.notification_broker import NotificationBroker
    from unittest.mock import Mock, MagicMock

    # Part 1: Daemon termination on terminal click / focus
    mock_audio = MagicMock()
    with patch("agent_dock_bell.daemon.PIDLock.acquire", return_value=True), \
         patch("agent_dock_bell.daemon.PIDLock.release"), \
         patch("agent_dock_bell.daemon.AudioLoopWorker", return_value=mock_audio), \
         patch("agent_dock_bell.daemon.set_dock_badge") as mock_badge, \
         patch("agent_dock_bell.daemon.set_window_urgency"), \
         patch("agent_dock_bell.daemon.send_notification"), \
         patch("agent_dock_bell.daemon.is_terminal_focused", return_value=False), \
         patch("agent_dock_bell.daemon.is_terminal_active_atspi", return_value=True), \
         patch("agent_dock_bell.daemon.close_display"), \
         patch.dict(os.environ, {"DISPLAY": ""}), \
         patch("os.kill", return_value=None), \
         patch("sys.exit") as mock_exit:
        run_daemon("Hermes", "org.gnome.Terminal.desktop", set(), {132563}, "new", max_wait=2.0)
        mock_exit.assert_called_with(0)
        badge_calls = [c.args for c in mock_badge.call_args_list]
        assert ("org.gnome.Terminal.desktop", 1) in [c[:2] for c in badge_calls]
        assert ("org.gnome.Terminal.desktop", 0) in [c[:2] for c in badge_calls]

    # Part 2: Notification banner click (ActionInvoked)
    bus = Mock()
    Gio = Mock()
    GLib = Mock()
    broker = NotificationBroker(bus, Gio, GLib)
    broker.owners = {":1.20"}
    target = {"pid": 132563, "started": "999", "tty": "/dev/pts/0"}
    broker.pending[42] = (target, time.monotonic())

    with patch("agent_dock_bell.notification_broker.focus_gnome_terminal") as mock_focus, \
         patch("agent_dock_bell.daemon.PIDLock.kill_existing") as mock_kill, \
         patch("agent_dock_bell.dock.set_dock_badge") as mock_badge:
        params = Mock()
        params.unpack.return_value = (42, "default")
        broker.on_signal(bus, ":1.20", "/org/freedesktop/Notifications",
                         "org.freedesktop.Notifications", "ActionInvoked", params, None)
        mock_focus.assert_called_once_with(target)
        mock_kill.assert_called_once()
        mock_badge.assert_called_once_with("org.gnome.Terminal.desktop", 0, urgent=False)
        assert 42 not in broker.pending

    # Part 3: Notification banner dismissed by user (NotificationClosed reason=2)
    broker.pending[43] = (target, time.monotonic())
    with patch("agent_dock_bell.daemon.PIDLock.kill_existing") as mock_kill, \
         patch("agent_dock_bell.dock.set_dock_badge") as mock_badge:
        params = Mock()
        params.unpack.return_value = (43, 2)
        broker.on_signal(bus, ":1.20", "/org/freedesktop/Notifications",
                         "org.freedesktop.Notifications", "NotificationClosed", params, None)
        mock_kill.assert_called_once()
        mock_badge.assert_called_once_with("org.gnome.Terminal.desktop", 0, urgent=False)
        assert 43 not in broker.pending


def test_regression_no_visual_notification_spam_when_user_active():
    """
    REGRESSION GUARD:
    When the user is active in the terminal window, agent execution completion
    must NEVER trigger a visual desktop notification popup.
    """
    import sys
    from agent_dock_bell.cli import main

    payload = {
        "hook_event_name": "on_session_end",
        "extra": {"completed": True, "failed": False, "interrupted": False},
    }

    with patch.object(sys, "argv", ["agent-dock-bell", "Hermes"]), \
         patch("agent_dock_bell.cli.read_hook_stdin", return_value=("", payload)), \
         patch("agent_dock_bell.x11.get_ancestor_process_chain", return_value=[(132563, "hermes", b"python")]), \
         patch("agent_dock_bell.x11.get_ancestor_pids", return_value={132563}), \
         patch("agent_dock_bell.x11.find_terminal_windows", return_value=({1001}, {132563}, "org.gnome.Terminal.desktop")), \
         patch("agent_dock_bell.x11.get_ancestor_ttys", return_value={"/dev/pts/0"}), \
         patch("agent_dock_bell.x11.get_active_window_id", return_value="0x1001"), \
         patch("agent_dock_bell.x11.is_terminal_focused", return_value=True), \
         patch("agent_dock_bell.audio.resolve_sound_file", return_value="new"), \
         patch("agent_dock_bell.audio.play_single_sound") as mock_sound, \
         patch("agent_dock_bell.dock.send_notification") as mock_notify, \
         patch("agent_dock_bell.daemon.PIDLock.kill_existing") as mock_kill, \
         patch("subprocess.Popen") as mock_popen:
        try:
            main()
        except SystemExit as exc:
            assert exc.code == 0

    mock_sound.assert_called_once()
    mock_notify.assert_not_called()  # NO visual notification spam!
    mock_kill.assert_called_once()
    mock_popen.assert_not_called()



