// Command agent-dock-bell is the single-binary hook launcher and background
// focus-aware alarm daemon for AI CLI agents on Linux.
package main

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/Rafael-Albernaz-dev/agent-dock-bell/internal/audio"
	"github.com/Rafael-Albernaz-dev/agent-dock-bell/internal/daemon"
	"github.com/Rafael-Albernaz-dev/agent-dock-bell/internal/hook"
	"github.com/Rafael-Albernaz-dev/agent-dock-bell/internal/procinfo"
)

func main() {
	os.Exit(run(os.Args[1:]))
}

func run(args []string) int {
	sub := ""
	if len(args) > 0 {
		sub = strings.ToLower(strings.TrimSpace(args[0]))
	}
	if sub == "daemon" || hasFlag(args, "--daemon") {
		return runDaemonMode()
	}
	if sub == "stop" || sub == "kill" || hasFlag(args, "--stop", "--kill", "-k", "-s") {
		return runStopMode()
	}
	if sub == "status" || sub == "test" || hasFlag(args, "--status", "--test") {
		return runDiagnosticsMode()
	}
	return runHookMode(args)
}

func runDaemonMode() int {
	srv, err := daemon.NewServer(daemon.Config{})
	if err != nil {
		fmt.Fprintf(os.Stderr, "agent-dock-bell daemon init error: %v\n", err)
		return 1
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM, syscall.SIGHUP)
	defer stop()

	if err := srv.Run(ctx); err != nil {
		fmt.Fprintf(os.Stderr, "agent-dock-bell daemon error: %v\n", err)
		return 1
	}
	return 0
}

func runStopMode() int {
	sock := daemon.DefaultSocketPath()
	_, _ = daemon.SendRequest(sock, daemon.Request{Action: "stop"}, 500*time.Millisecond)
	fmt.Println("Stopped active alarms.")
	return 0
}

func runDiagnosticsMode() int {
	fmt.Println("=== agent-dock-bell (dockbell) v2 diagnostics ===")
	sound := audio.ResolveSoundFile("")
	soundOK := false
	if sound != "" {
		if st, err := os.Stat(sound); err == nil && st.Size() > 10000 {
			soundOK = true
		}
	}
	if soundOK {
		fmt.Printf("Audio file: %s (found)\n", sound)
	} else {
		fmt.Printf("Audio file: %s (missing)\n", sound)
		return 1
	}

	caller := procinfo.Discover(os.Getpid())
	fmt.Printf("Caller context: terminal_pid=%d tty=%s desktop_id=%s\n",
		caller.TerminalPID, caller.TTYPath, caller.DesktopID)

	sock := daemon.DefaultSocketPath()
	resp, err := daemon.SendRequest(sock, daemon.Request{Action: "status"}, 500*time.Millisecond)
	if err == nil && resp.OK {
		fmt.Printf("Daemon socket: %s (running, focus_bridge=%v, active_alarm=%v)\n",
			sock, resp.FocusBridgeAvailable, resp.ActiveAlarm)
	} else {
		fmt.Printf("Daemon socket: %s (standby / on-demand)\n", sock)
	}

	fmt.Println("✓ All systems operational.")
	return 0
}

func runHookMode(args []string) int {
	rawStdin := readStdinNonBlocking(150 * time.Millisecond)
	envMap := currentEnvMap()

	decision := hook.Evaluate(rawStdin, args, envMap)
	if !decision.ShouldAlert {
		fmt.Println("{}")
		return 0
	}

	caller := procinfo.Discover(os.Getpid())
	req := daemon.Request{
		Action:       "trigger",
		AgentName:    decision.AgentName,
		TerminalPID:  caller.TerminalPID,
		AncestorPIDs: caller.AncestorPIDs,
		TTYPath:      caller.TTYPath,
		DesktopID:    caller.DesktopID,
		Force:        hasFlag(args, "--force"),
	}

	sock := daemon.DefaultSocketPath()
	if _, err := daemon.SendRequest(sock, req, 30*time.Millisecond); err != nil {
		// Auto-start daemon if not running yet
		if startDetachedDaemon() == nil {
			for i := 0; i < 6; i++ {
				time.Sleep(8 * time.Millisecond)
				if _, err := daemon.SendRequest(sock, req, 25*time.Millisecond); err == nil {
					break
				}
			}
		}
	}

	fmt.Println("{}")
	return 0
}

func startDetachedDaemon() error {
	exe, err := os.Executable()
	if err != nil {
		return err
	}
	cmd := exec.Command(exe, "--daemon")
	cmd.Stdin = nil
	cmd.Stdout = nil
	cmd.Stderr = nil
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	if err := cmd.Start(); err != nil {
		return err
	}
	_ = cmd.Process.Release()
	return nil
}

func readStdinNonBlocking(timeout time.Duration) []byte {
	fi, err := os.Stdin.Stat()
	if err != nil || (fi.Mode()&os.ModeCharDevice) != 0 {
		return nil
	}
	type result struct {
		data []byte
	}
	ch := make(chan result, 1)
	go func() {
		b, _ := hook.ReadBounded(os.Stdin)
		ch <- result{data: b}
	}()

	select {
	case res := <-ch:
		return res.data
	case <-time.After(timeout):
		return nil
	}
}

func currentEnvMap() map[string]string {
	m := make(map[string]string, 32)
	for _, kv := range os.Environ() {
		if k, v, ok := strings.Cut(kv, "="); ok {
			m[k] = v
		}
	}
	return m
}

func hasFlag(args []string, flags ...string) bool {
	for _, a := range args {
		for _, f := range flags {
			if a == f {
				return true
			}
		}
	}
	return false
}
