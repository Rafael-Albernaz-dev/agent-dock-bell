package procinfo

import (
	"os"
	"testing"
)

func TestDiscover_CurrentProcess(t *testing.T) {
	t.Parallel()
	ctx := Discover(os.Getpid())
	if len(ctx.AncestorPIDs) == 0 {
		t.Fatalf("expected non-empty AncestorPIDs")
	}
	if ctx.AncestorPIDs[0] != os.Getpid() {
		t.Fatalf("expected first PID to be %d, got %d", os.Getpid(), ctx.AncestorPIDs[0])
	}
	if !IsProcessAlive(os.Getpid()) {
		t.Fatalf("expected current process to be alive")
	}
}

func TestReadAccessTime(t *testing.T) {
	t.Parallel()
	f, err := os.CreateTemp(t.TempDir(), "tty-sim-*")
	if err != nil {
		t.Fatal(err)
	}
	_ = f.Close()

	atime, ok := ReadAccessTime(f.Name())
	if !ok || atime.IsZero() {
		t.Fatalf("expected valid access time, got ok=%v atime=%v", ok, atime)
	}
}

func TestSharesProcessTree(t *testing.T) {
	t.Parallel()
	self := os.Getpid()
	ppid := os.Getppid()
	if !SharesProcessTree(self, ppid, []int{self, ppid}) {
		t.Fatalf("expected self (%d) and ppid (%d) to share process tree", self, ppid)
	}
	if SharesProcessTree(9999999, ppid, []int{ppid}) {
		t.Fatalf("expected non-existent PID not to match")
	}
}
