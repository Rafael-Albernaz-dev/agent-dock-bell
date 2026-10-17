package audio

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestResolveSoundFile_AndMP3Header(t *testing.T) {
	t.Parallel()
	repoRoot, err := filepath.Abs(filepath.Join("..", ".."))
	if err != nil {
		t.Fatal(err)
	}
	sound := ResolveSoundFile(repoRoot)
	if sound == "" {
		t.Fatalf("expected non-empty resolved sound file")
	}
	if !strings.HasSuffix(sound, BundledAssetFilename) {
		t.Fatalf("expected bundled asset %q, got %q", BundledAssetFilename, sound)
	}

	f, err := os.Open(sound)
	if err != nil {
		t.Fatalf("open sound: %v", err)
	}
	defer f.Close()

	header := make([]byte, 4)
	if _, err := f.Read(header); err != nil {
		t.Fatalf("read header: %v", err)
	}
	isID3 := string(header[:3]) == "ID3"
	isSync := header[0] == 0xFF && (header[1]&0xE0) == 0xE0
	if !isID3 && !isSync {
		t.Fatalf("invalid MP3 header bytes: %v", header)
	}
}

func TestLoop_CancelsImmediately(t *testing.T) {
	t.Parallel()
	repoRoot, _ := filepath.Abs(filepath.Join("..", ".."))
	sound := ResolveSoundFile(repoRoot)

	ctx, cancel := context.WithCancel(context.Background())
	var ticks atomic.Int32

	done := make(chan struct{})
	start := time.Now()
	go func() {
		defer close(done)
		Loop(ctx, LoopConfig{
			PlayerBin:   "true", // fast POSIX binary for unit test
			SoundFile:   sound,
			Interval:    40 * time.Millisecond,
			MaxDuration: 5 * time.Second,
			OnTick: func(i int) {
				ticks.Store(int32(i))
				if i >= 2 {
					cancel()
				}
			},
		})
	}()

	select {
	case <-done:
	case <-time.After(1 * time.Second):
		t.Fatalf("Loop did not terminate promptly after cancel")
	}

	if got := ticks.Load(); got < 2 {
		t.Fatalf("expected at least 2 ticks, got %d", got)
	}
	if elapsed := time.Since(start); elapsed > 500*time.Millisecond {
		t.Fatalf("Loop took too long to cancel: %v", elapsed)
	}
}
