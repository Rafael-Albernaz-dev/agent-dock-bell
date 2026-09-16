from unittest.mock import patch
from agent_dock_bell.audio import PRESET_SOUNDS, get_sound_cmd, resolve_sound_file

def test_resolve_sound_file_fallback():
    # Resolves to a valid audio file or fallback even in headless CI environments
    sound = resolve_sound_file("bell")
    assert sound != ""

def test_resolve_sound_file_mocked():
    # With sound files present, resolves directly to chosen preset
    with patch("os.path.exists", return_value=True):
        assert resolve_sound_file("bell") == PRESET_SOUNDS["bell"]
        assert resolve_sound_file("pop") == PRESET_SOUNDS["pop"]
        assert resolve_sound_file("complete") == PRESET_SOUNDS["complete"]

def test_get_sound_cmd_volume():
    cmd_default = get_sound_cmd("/path/to/sound.oga", "1.0")
    assert cmd_default == ["pw-play", "/path/to/sound.oga"]

    cmd_custom = get_sound_cmd("/path/to/sound.oga", "0.7")
    assert cmd_custom == ["pw-play", "--volume", "0.7", "/path/to/sound.oga"]
