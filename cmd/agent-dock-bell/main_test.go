package main

import (
	"bytes"
	"context"
	"io"
	"log/slog"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/Rafael-Albernaz-dev/agent-dock-bell/internal/daemon"
)

func TestCLI_EndToEndSubprocess(t *testing.T) {
	sockPath := filepath.Join(t.TempDir(), "cli-e2e.sock")
	t.Setenv("AGENT_DOCK_BELL_SOCKET", sockPath)

	repoRoot, _ := filepath.Abs(filepath.Join("..", ".."))
	soundFile := filepath.Join(repoRoot, "assets", "universfield-new-notification-02-323592.mp3")

	srv, err := daemon.NewServer(daemon.Config{
		SocketPath:    sockPath,
		SoundFile:     soundFile,
		PlayerBin:     "true",
		AudioInterval: 50 * time.Millisecond,
		MaxAudioWait:  2 * time.Second,
		Logger:        slog.New(slog.NewJSONHandler(io.Discard, nil)),
	})
	if err != nil {
		t.Fatalf("NewServer: %v", err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan struct{})
	go func() {
		defer close(done)
		_ = srv.Run(ctx)
	}()
	time.Sleep(25 * time.Millisecond)

	// Build real binary
	binPath := filepath.Join(t.TempDir(), "agent-dock-bell")
	buildCmd := exec.Command("go", "build", "-o", binPath, ".")
	if out, err := buildCmd.CombinedOutput(); err != nil {
		t.Fatalf("go build failed: %v\n%s", err, string(out))
	}

	// 1. Run `status` subcommand diagnostics
	diagCmd := exec.Command(binPath, "status")
	diagOut, err := diagCmd.CombinedOutput()
	if err != nil {
		t.Fatalf("status failed: %v\n%s", err, string(diagOut))
	}
	if !strings.Contains(string(diagOut), "universfield-new-notification-02-323592.mp3 (found)") {
		t.Fatalf("expected audio file found in diagnostics output:\n%s", string(diagOut))
	}
	if !strings.Contains(string(diagOut), "running") {
		t.Fatalf("expected daemon socket running in diagnostics output:\n%s", string(diagOut))
	}

	// 2. Trigger alarm via real binary with JSON payload on stdin (--force to ensure alarm starts)
	hookCmd := exec.Command(binPath, "--force", "Antigravity")
	hookCmd.Stdin = bytes.NewBufferString(`{"hook_event_name":"Stop","fullyIdle":true}`)
	hookOut, err := hookCmd.CombinedOutput()
	if err != nil {
		t.Fatalf("hook invocation failed: %v\n%s", err, string(hookOut))
	}
	if strings.TrimSpace(string(hookOut)) != "{}" {
		t.Fatalf("expected '{}', got %q", string(hookOut))
	}

	st, err := daemon.SendRequest(sockPath, daemon.Request{Action: "status"}, 100*time.Millisecond)
	if err != nil || !st.ActiveAlarm {
		t.Fatalf("expected ActiveAlarm=true after CLI hook trigger, got %+v (err=%v)", st, err)
	}

	// 3. Stop alarm via real binary `stop` subcommand
	stopCmd := exec.Command(binPath, "stop")
	stopOut, err := stopCmd.CombinedOutput()
	if err != nil {
		t.Fatalf("stop failed: %v\n%s", err, string(stopOut))
	}
	if !strings.Contains(string(stopOut), "Stopped active alarms.") {
		t.Fatalf("unexpected stop output: %q", string(stopOut))
	}

	stAfter, err := daemon.SendRequest(sockPath, daemon.Request{Action: "status"}, 100*time.Millisecond)
	if err != nil || stAfter.ActiveAlarm {
		t.Fatalf("expected ActiveAlarm=false after --stop, got %+v (err=%v)", stAfter, err)
	}

	cancel()
	<-done
}
