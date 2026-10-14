import os
import subprocess
import sys
import threading
import time
from unittest.mock import patch

from agent_dock_bell.cli import build_daemon_environment, read_hook_stdin, should_skip_event

def test_daemon_environment_imports_package_from_another_directory():
    result = subprocess.run(
        [sys.executable, "-c", "import agent_dock_bell.cli"],
        cwd="/tmp",
        env=build_daemon_environment(),
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr

def test_should_skip_test_flags():
    assert should_skip_event("", None, ["--test"]) is True
    assert should_skip_event("", None, ["--dry-run"]) is True
    assert should_skip_event("", None, ["Antigravity CLI"]) is False

def test_should_skip_synthetic_stdin():
    assert should_skip_event('{"extra": "test-session"}', None, []) is True
    assert should_skip_event('{"event": "synthetic payload"}', None, []) is True

def test_should_skip_antigravity_not_fully_idle():
    # When background tasks or subagents are running, fullyIdle is False
    payload_running = {"fullyIdle": False, "executionNum": 1}
    assert should_skip_event("", payload_running, []) is True

    # When prompt is truly finished, fullyIdle is True
    payload_idle = {"fullyIdle": True, "executionNum": 1}
    assert should_skip_event("", payload_idle, []) is False

def test_should_skip_hermes_intermediate_events():
    # Intermediate LLM call should be ignored
    payload_intermediate = {"hook_event_name": "post_llm_call"}
    assert should_skip_event("", payload_intermediate, []) is True

    # Final session end should be processed
    payload_session_end = {
        "hook_event_name": "on_session_end",
        "extra": {"completed": True, "interrupted": False},
    }
    assert should_skip_event("", payload_session_end, []) is False

    # Interrupted or uncompleted session should be ignored
    payload_interrupted = {
        "hook_event_name": "on_session_end",
        "extra": {"completed": False, "interrupted": True},
    }
    assert should_skip_event("", payload_interrupted, []) is True

    # Real Hermes canonical payload with top-level fields
    payload_hermes_root_ok = {
        "hook_event_name": "on_session_end",
        "completed": True,
        "interrupted": False,
    }
    assert should_skip_event("", payload_hermes_root_ok, []) is False

    payload_hermes_root_interrupted = {
        "hook_event_name": "on_session_end",
        "completed": False,
        "interrupted": True,
    }
    assert should_skip_event("", payload_hermes_root_interrupted, []) is True

    # Hermes background review or subagent turns must be skipped
    payload_hermes_background = {
        "hook_event_name": "on_session_end",
        "parent_session_id": "parent-sess-123",
        "completed": True,
    }
    assert should_skip_event("", payload_hermes_background, []) is True

    payload_hermes_review = {
        "hook_event_name": "on_session_end",
        "extra": {"write_origin": "background_review", "completed": True},
    }
    assert should_skip_event("", payload_hermes_review, []) is True

    payload_hermes_child = {
        "hook_event_name": "on_session_end",
        "child_role": "subagent",
        "completed": True,
    }
    assert should_skip_event("", payload_hermes_child, []) is True

def test_should_skip_codex_tool_use():
    payload_tool = {"stop_reason": "tool_use"}
    assert should_skip_event("", payload_tool, []) is True

    # CamelCase stopReason from Codex
    payload_tool_camel = {"stopReason": "tool_use"}
    assert should_skip_event("", payload_tool_camel, []) is True

    payload_done = {"stop_reason": "end_turn"}
    assert should_skip_event("", payload_done, []) is False

    payload_done_camel = {"stopReason": "end_turn"}
    assert should_skip_event("", payload_done_camel, []) is False

def test_should_process_codex_stop_hook_payload():
    payload_stop = {
        "session_id": "session-123",
        "cwd": "/tmp/project",
        "hook_event_name": "Stop",
        "turn_id": "turn-123",
        "stop_hook_active": False,
        "last_assistant_message": "Task completed.",
    }

    assert should_skip_event("", payload_stop, []) is False

    # CamelCase hookEventName
    payload_stop_camel = {
        "sessionId": "session-123",
        "hookEventName": "Stop",
        "turnId": "turn-123",
    }
    assert should_skip_event("", payload_stop_camel, []) is False

def test_fragmented_hook_payload_is_read_before_decision(monkeypatch):
    read_fd, write_fd = os.pipe()
    payload = b'{"hookEventName":"Stop","stopReason":"tool_use"}'
    with os.fdopen(read_fd, "r") as reader:
        monkeypatch.setattr(sys, "stdin", reader)
        os.write(write_fd, payload[:15])

        def finish_write():
            time.sleep(0.005)
            os.write(write_fd, payload[15:])
            os.close(write_fd)

        writer = threading.Thread(target=finish_write)
        writer.start()
        raw, parsed = read_hook_stdin(timeout=0.1)
        writer.join()
    assert raw == payload.decode()
    assert parsed["stopReason"] == "tool_use"
    assert should_skip_event(raw, parsed, []) is True

def test_incomplete_hook_payload_does_not_start_alarm():
    assert should_skip_event('{"hookEventName":', None, []) is True

def test_unknown_terminal_uses_one_shot_alert_without_daemon():
    from agent_dock_bell.cli import main

    with patch.object(sys, "argv", ["agent-dock-bell", "Test Agent"]), \
         patch("agent_dock_bell.cli.read_hook_stdin", return_value=("", None)), \
         patch("agent_dock_bell.x11.get_ancestor_pids", return_value={123}), \
         patch("agent_dock_bell.x11.find_terminal_windows", return_value=(set(), set(), "org.gnome.Terminal.desktop")), \
         patch("agent_dock_bell.x11.get_ancestor_ttys", return_value=set()), \
         patch("agent_dock_bell.x11.get_active_window_id", return_value=None), \
         patch("agent_dock_bell.x11.is_terminal_focused", return_value=False), \
         patch("agent_dock_bell.audio.resolve_sound_file", return_value="bell"), \
         patch("agent_dock_bell.audio.play_single_sound") as sound, \
         patch("agent_dock_bell.dock.send_terminal_bell"), \
         patch("agent_dock_bell.dock.send_notification") as notify, \
         patch("agent_dock_bell.daemon.PIDLock.kill_existing") as kill_existing, \
         patch("subprocess.Popen") as spawn:
        try:
            main()
        except SystemExit as exc:
            assert exc.code == 0
    sound.assert_called_once()
    notify.assert_called_once()
    kill_existing.assert_not_called()
    spawn.assert_not_called()

def test_cli_stop_flag_kills_existing_and_exits(capsys):
    from agent_dock_bell.cli import main

    with patch.object(sys, "argv", ["agent-dock-bell", "--stop"]), \
         patch("agent_dock_bell.daemon.PIDLock.kill_existing") as kill_existing, \
         patch("agent_dock_bell.dock.set_dock_badge") as mock_badge, \
         patch("agent_dock_bell.x11.close_display") as mock_close:
        try:
            main()
        except SystemExit as exc:
            assert exc.code == 0
    kill_existing.assert_called_once()
    assert mock_badge.call_count >= 1
    mock_close.assert_called_once()
    captured = capsys.readouterr()
    assert "Stopped active alarms" in captured.out

def test_cli_test_flag_prints_diagnostics(capsys):
    from agent_dock_bell.cli import main

    with patch.object(sys, "argv", ["agent-dock-bell", "--test"]), \
         patch("agent_dock_bell.audio.resolve_sound_file", return_value="/tmp/test.oga"), \
         patch("agent_dock_bell.x11.get_ancestor_pids", return_value={10}), \
         patch("agent_dock_bell.x11.find_terminal_windows", return_value=(set(), {10}, "test.desktop")), \
         patch("agent_dock_bell.x11.get_active_window_id", return_value="0x123"):
        try:
            main()
        except SystemExit as exc:
            assert exc.code == 0
    captured = capsys.readouterr()
    assert "diagnostics" in captured.out
    assert "All systems operational" in captured.out

def test_should_skip_event_force_bypasses_all_filters():
    from agent_dock_bell.cli import should_skip_event

    assert should_skip_event('{"hookEventName":', None, ["--force"]) is False
    assert should_skip_event('{"extra": "test-session"}', None, ["--force"]) is False
    assert should_skip_event("", {"fullyIdle": False}, ["--force"]) is False

def test_already_active_kills_lingering_daemon():
    from agent_dock_bell.cli import main

    with patch.object(sys, "argv", ["agent-dock-bell", "Test Agent"]), \
         patch("agent_dock_bell.cli.read_hook_stdin", return_value=("", None)), \
         patch("agent_dock_bell.x11.get_ancestor_pids", return_value={123}), \
         patch("agent_dock_bell.x11.find_terminal_windows", return_value=({456}, {123}, "org.gnome.Terminal.desktop")), \
         patch("agent_dock_bell.x11.get_ancestor_ttys", return_value=set()), \
         patch("agent_dock_bell.x11.get_active_window_id", return_value="0x1c8"), \
         patch("agent_dock_bell.x11.is_terminal_focused", return_value=True), \
         patch("agent_dock_bell.audio.resolve_sound_file", return_value="bell"), \
         patch("agent_dock_bell.audio.play_single_sound") as sound, \
         patch("agent_dock_bell.dock.send_terminal_bell"), \
         patch("agent_dock_bell.dock.send_notification") as notify, \
         patch("agent_dock_bell.daemon.PIDLock.kill_existing") as kill_existing, \
         patch("subprocess.Popen") as spawn:
        try:
            main()
        except SystemExit as exc:
            assert exc.code == 0
    sound.assert_called_once()
    notify.assert_called_once()
    kill_existing.assert_called_once()
    spawn.assert_not_called()

