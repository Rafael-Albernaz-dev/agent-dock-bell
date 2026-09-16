"""
Audio subsystem for playing completion chimes in loop with PipeWire and Canberra fallbacks.
"""

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import List, Optional

PRESET_SOUNDS = {
    "bell": "/usr/share/sounds/Yaru/stereo/bell.oga",                  # Subtle Ubuntu ding (0.28s)
    "pop": "/usr/share/sounds/Yaru/stereo/message-new-instant.oga",    # Clean message bubble pop (0.42s)
    "click": str(Path.home() / ".local/share/sounds/click-dry.ogg"),  # Dry acoustic wood tap (0.38s)
    "complete": "/usr/share/sounds/freedesktop/stereo/complete.oga",   # Classic freedesktop chime
}

def resolve_sound_file(sound_choice: Optional[str] = None) -> str:
    """Resolve sound file path from choice or environment variable."""
    if not sound_choice:
        sound_choice = os.environ.get("AGENT_ALARM_SOUND", "bell").strip()

    if sound_choice in PRESET_SOUNDS and os.path.exists(PRESET_SOUNDS[sound_choice]):
        return PRESET_SOUNDS[sound_choice]
    elif os.path.isfile(sound_choice):
        return sound_choice
    elif os.path.exists(PRESET_SOUNDS["bell"]):
        return PRESET_SOUNDS["bell"]
    elif os.path.exists(PRESET_SOUNDS["complete"]):
        return PRESET_SOUNDS["complete"]
    return PRESET_SOUNDS.get("click", "")

def get_sound_cmd(sound_file: str, volume: str = "1.0") -> List[str]:
    """Build pw-play command with optional volume flag."""
    cmd = ["pw-play"]
    if volume and volume not in ("1.0", "1", ""):
        cmd.extend(["--volume", str(volume)])
    cmd.append(sound_file)
    return cmd

def play_single_sound(sound_file: str, volume: str = "1.0") -> None:
    """Play the completion sound once with timeout protection."""
    if not sound_file or not os.path.exists(sound_file):
        return
    cmd = get_sound_cmd(sound_file, volume)
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2.0)
        if r.returncode != 0:
            subprocess.run(["canberra-gtk-play", "-f", sound_file], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2.0)
    except Exception:
        try:
            subprocess.run(["canberra-gtk-play", "-f", sound_file], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2.0)
        except Exception:
            pass

class AudioLoopWorker:
    """Background worker that loops audio chime until stopped or max duration reached."""

    def __init__(self, sound_file: str, volume: str = "1.0", max_seconds: int = 180, interval: float = 3.0):
        self.sound_file = sound_file
        self.volume = volume
        self.max_seconds = max_seconds
        self.interval = interval
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self._active_proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        with self._lock:
            if self._active_proc and self._active_proc.poll() is None:
                try:
                    self._active_proc.terminate()
                    self._active_proc.wait(timeout=0.2)
                except Exception:
                    try:
                        self._active_proc.kill()
                    except Exception:
                        pass
                self._active_proc = None

    def _run(self) -> None:
        start_time = time.time()
        while not self.stop_event.is_set():
            if time.time() - start_time > self.max_seconds:
                break

            p = None
            with self._lock:
                if self.stop_event.is_set():
                    break
                try:
                    cmd = get_sound_cmd(self.sound_file, self.volume)
                    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    try:
                        p = subprocess.Popen(["canberra-gtk-play", "-f", self.sound_file], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except Exception:
                        p = None
                self._active_proc = p

            if p:
                try:
                    p.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    try:
                        p.kill()
                        p.wait(timeout=0.5)
                    except Exception:
                        pass
                with self._lock:
                    self._active_proc = None

            if self.stop_event.wait(self.interval):
                break

def kill_audio_processes(sound_file: str) -> None:
    """Kill lingering audio playback processes owned by current user."""
    uid = str(os.getuid())
    if sound_file:
        try:
            subprocess.run(["pkill", "-u", uid, "-f", sound_file], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    try:
        subprocess.run(["pkill", "-u", uid, "-x", "pw-play"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["pkill", "-u", uid, "-x", "canberra-gtk-play"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
