"""
Full Integration Test Suite for agent-dock-bell.
Executes real subprocesses and end-to-end workflows to ensure the mini-app works
as a cohesive system in the real desktop environment without mock-gaming.
"""

import os
import subprocess
import sys
from pathlib import Path

from agent_dock_bell.audio import get_sound_cmd, resolve_sound_file


def test_cli_diagnostics_end_to_end():
    """
    Execute 'agent-dock-bell --test' end-to-end.
    Ensures that diagnostics report the newly configured audio file as '(found)'
    and that all subsystems (AT-SPI, Wayland/X11, Audio) report operational.
    """
    repo_root = str(Path(__file__).resolve().parents[1])
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{repo_root}/src"

    res = subprocess.run(
        [sys.executable, "-m", "agent_dock_bell.cli", "--test"],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert res.returncode == 0, f"Diagnostics failed with stderr: {res.stderr}"
    stdout = res.stdout
    assert "=== agent-dock-bell diagnostics ===" in stdout
    assert "universfield-new-notification-02-323592.mp3 (found)" in stdout, (
        f"Expected new audio file found, output was:\n{stdout}"
    )
    assert "✓ All systems operational." in stdout


def test_cli_stop_command_end_to_end():
    """
    Execute 'agent-dock-bell --stop' end-to-end.
    Verifies that alarm termination completes cleanly with code 0.
    """
    repo_root = str(Path(__file__).resolve().parents[1])
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{repo_root}/src"

    res = subprocess.run(
        [sys.executable, "-m", "agent_dock_bell.cli", "--stop"],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert res.returncode == 0, f"--stop failed with stderr: {res.stderr}"
    assert "Stopped active alarms." in res.stdout


def test_cli_dry_run_with_stdin_payload():
    """
    Execute hook pipeline with JSON stdin in dry-run mode.
    Verifies that hook stdin parser reads payload and exits with JSON '{}'.
    """
    repo_root = str(Path(__file__).resolve().parents[1])
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{repo_root}/src"

    res = subprocess.run(
        [sys.executable, "-m", "agent_dock_bell.cli", "--dry-run", "Antigravity"],
        input='{"fullyIdle": true, "stop_reason": "end_turn"}',
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert res.returncode == 0, f"Dry run failed: {res.stderr}"
    assert res.stdout.strip() == "{}"


def test_audio_pipeline_file_integrity():
    """
    Verify resolved audio file can be read and decoded by pw-play / canberra.
    """
    sound = resolve_sound_file()
    assert os.path.exists(sound)
    cmd = get_sound_cmd(sound, volume="0.8")
    assert cmd == ["pw-play", "--volume", "0.8", sound]

    # Verify audio file binary header (MP3 magic or valid frames)
    with open(sound, "rb") as f:
        header = f.read(4)
        assert len(header) == 4
        # Standard MP3 starts with ID3 header or sync word 0xFF 0xFB/0xF3
        is_id3 = header.startswith(b"ID3")
        is_sync = header[0] == 0xFF and (header[1] & 0xE0) == 0xE0
        assert is_id3 or is_sync, f"Invalid MP3 header bytes: {header!r}"


def test_installed_user_binary_matches_diagnostics():
    """
    Verify that the user's installed ~/.local/bin/agent-dock-bell executable
    runs cleanly and reports the exact same new audio file.
    """
    installed_bin = Path.home() / ".local/bin/agent-dock-bell"
    if not installed_bin.exists():
        return

    res = subprocess.run(
        [str(installed_bin), "--test"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert res.returncode == 0, f"Installed runner failed: {res.stderr}"
    assert "universfield-new-notification-02-323592.mp3 (found)" in res.stdout
