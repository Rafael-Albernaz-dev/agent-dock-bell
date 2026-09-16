from agent_dock_bell.audio import get_sound_cmd, resolve_sound_file

def test_resolve_sound_file_presets():
    bell = resolve_sound_file("bell")
    assert bell != ""
    assert "bell.oga" in bell

def test_get_sound_cmd_volume():
    cmd_default = get_sound_cmd("/path/to/sound.oga", "1.0")
    assert cmd_default == ["pw-play", "/path/to/sound.oga"]

    cmd_custom = get_sound_cmd("/path/to/sound.oga", "0.7")
    assert cmd_custom == ["pw-play", "--volume", "0.7", "/path/to/sound.oga"]
