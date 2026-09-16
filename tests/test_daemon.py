import os
from agent_dock_bell.daemon import PIDLock, PID_FILE

def test_pid_lock_lifecycle():
    # Acquire lock
    PIDLock.acquire()
    assert os.path.exists(PID_FILE)

    with open(PID_FILE) as f:
        pid = int(f.read().strip())
    assert pid == os.getpid()

    # Release lock
    PIDLock.release()
    assert not os.path.exists(PID_FILE)
