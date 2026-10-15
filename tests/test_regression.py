"""
Automated Regression Test Suite for agent-dock-bell.
Each test in this file guards against a specific real-world regression that occurred
in past commits, ensuring no "gaming" through overly broad mocks.
"""

import os
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
