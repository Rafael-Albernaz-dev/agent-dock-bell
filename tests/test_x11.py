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
