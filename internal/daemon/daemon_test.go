package daemon

import (
	"context"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/godbus/dbus/v5"
)

// fakeDBusDesktop exports real D-Bus objects for org.freedesktop.Notifications
// and org.agentdockbell.FocusBridge on the isolated test session bus.
type fakeDBusDesktop struct {
	mu             sync.Mutex
	focusedPID     uint32
	notifyCalls    int
	lastAgent      string
	lastReplacesID uint32
	activatedPIDs  []uint32
}

func (f *fakeDBusDesktop) GetFocusedPID() (uint32, string, *dbus.Error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.focusedPID, "org.gnome.Terminal", nil
}

func (f *fakeDBusDesktop) ActivateWindow(pid uint32) (bool, *dbus.Error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.activatedPIDs = append(f.activatedPIDs, pid)
	f.focusedPID = pid
	return true, nil
}

func (f *fakeDBusDesktop) Notify(
	appName string,
	replacesID uint32,
	appIcon string,
	summary string,
	body string,
	actions []string,
	hints map[string]dbus.Variant,
	expireTimeout int32,
) (uint32, *dbus.Error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.notifyCalls++
	f.lastAgent = appName
	f.lastReplacesID = replacesID
	return uint32(100 + f.notifyCalls), nil
}

func setupIsolatedDBusServices(t *testing.T, initialFocusedPID uint32) (*dbus.Conn, *fakeDBusDesktop) {
	t.Helper()
	conn, err := dbus.ConnectSessionBus()
	if err != nil {
		t.Skipf("session D-Bus not available: %v", err)
	}

	desktop := &fakeDBusDesktop{focusedPID: initialFocusedPID}

	if err := conn.Export(desktop, FocusBridgePath, FocusBridgeIface); err != nil {
		t.Fatalf("export FocusBridge: %v", err)
	}
	if _, err := conn.RequestName(FocusBridgeBusName, dbus.NameFlagReplaceExisting); err != nil {
		t.Fatalf("request FocusBridge bus name: %v", err)
	}

	if err := conn.Export(desktop, NotificationsPath, NotificationsIface); err != nil {
		t.Fatalf("export Notifications: %v", err)
	}
	if _, err := conn.RequestName(NotificationsBusName, dbus.NameFlagReplaceExisting); err != nil {
		t.Fatalf("request Notifications bus name: %v", err)
	}

	t.Cleanup(func() {
		_ = conn.Close()
	})
	return conn, desktop
}

func TestDaemon_EndToEnd_FocusAndNotificationClick(t *testing.T) {
	// Run inside isolated dbus-run-session (enforced by quality-gate.sh Gate 5)
	termPID := os.Getpid()
	busConn, desktop := setupIsolatedDBusServices(t, 999999) // unfocused initially

	sockPath := filepath.Join(t.TempDir(), "test-bell.sock")
	repoRoot, _ := filepath.Abs(filepath.Join("..", ".."))
	soundFile := filepath.Join(repoRoot, "assets", "universfield-new-notification-02-323592.mp3")

	srv, err := NewServer(Config{
		SocketPath:    sockPath,
		SoundFile:     soundFile,
		PlayerBin:     "true",
		AudioInterval: 50 * time.Millisecond,
		MaxAudioWait:  5 * time.Second,
		TTYPollPeriod: 50 * time.Millisecond,
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

	// Wait until server is ready
	time.Sleep(30 * time.Millisecond)

	// 1. Trigger when terminal is NOT focused -> should send notification and start alarm loop
	resp, err := SendRequest(sockPath, Request{
		Action:       "trigger",
		AgentName:    "Antigravity",
		TerminalPID:  termPID,
		AncestorPIDs: []int{termPID},
		DesktopID:    "org.gnome.Terminal.desktop",
	}, 200*time.Millisecond)
	if err != nil {
		t.Fatalf("SendRequest trigger: %v", err)
	}
	if !resp.OK || resp.Status != "alarm-started" {
		t.Fatalf("expected status=alarm-started, got %+v", resp)
	}

	desktop.mu.Lock()
	notifyCount := desktop.notifyCalls
	desktop.mu.Unlock()
	if notifyCount != 1 {
		t.Fatalf("expected 1 D-Bus Notify call, got %d", notifyCount)
	}

	// Verify alarm is active via status
	st, err := SendRequest(sockPath, Request{Action: "status"}, 200*time.Millisecond)
	if err != nil || !st.ActiveAlarm || !st.FocusBridgeAvailable {
		t.Fatalf("expected ActiveAlarm=true and FocusBridgeAvailable=true, got %+v (err=%v)", st, err)
	}

	// 2. Emit ActionInvoked (user clicks notification) -> must call ActivateWindow(termPID) and stop alarm
	if err := busConn.Emit(NotificationsPath, NotificationsIface+".ActionInvoked", uint32(101), "default"); err != nil {
		t.Fatalf("Emit ActionInvoked: %v", err)
	}

	deadline := time.Now().Add(1 * time.Second)
	for time.Now().Before(deadline) {
		st, _ = SendRequest(sockPath, Request{Action: "status"}, 100*time.Millisecond)
		if !st.ActiveAlarm {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if st.ActiveAlarm {
		t.Fatalf("expected alarm to stop after notification click")
	}

	desktop.mu.Lock()
	activated := append([]uint32(nil), desktop.activatedPIDs...)
	desktop.mu.Unlock()
	if len(activated) != 1 || int(activated[0]) != termPID {
		t.Fatalf("expected ActivateWindow(%d) to be called on notification click, got %v", termPID, activated)
	}

	// 3. Now terminal IS focused (desktop.focusedPID == termPID): trigger must play single chime and suppress banner
	resp2, err := SendRequest(sockPath, Request{
		Action:       "trigger",
		AgentName:    "Antigravity",
		TerminalPID:  termPID,
		AncestorPIDs: []int{termPID},
	}, 200*time.Millisecond)
	if err != nil {
		t.Fatalf("SendRequest trigger focused: %v", err)
	}
	if resp2.Status != "single-chime-focused" {
		t.Fatalf("expected single-chime-focused when terminal is already focused, got %+v", resp2)
	}

	desktop.mu.Lock()
	notifyCountAfter := desktop.notifyCalls
	desktop.mu.Unlock()
	if notifyCountAfter != 1 {
		t.Fatalf("expected notification banner to be suppressed when focused, notifyCalls=%d", notifyCountAfter)
	}

	// 4. Test FocusChanged signal stopping an active alarm
	desktop.mu.Lock()
	desktop.focusedPID = 888888 // unfocused again
	desktop.mu.Unlock()

	resp3, err := SendRequest(sockPath, Request{
		Action:       "trigger",
		AgentName:    "Codex CLI",
		TerminalPID:  termPID,
		AncestorPIDs: []int{termPID},
	}, 200*time.Millisecond)
	if err != nil || resp3.Status != "alarm-started" {
		t.Fatalf("expected alarm-started, got %+v (err=%v)", resp3, err)
	}

	if err := busConn.Emit(FocusBridgePath, FocusBridgeIface+".FocusChanged", uint32(termPID), "org.gnome.Terminal"); err != nil {
		t.Fatalf("Emit FocusChanged: %v", err)
	}

	deadline = time.Now().Add(1 * time.Second)
	for time.Now().Before(deadline) {
		st, _ = SendRequest(sockPath, Request{Action: "status"}, 100*time.Millisecond)
		if !st.ActiveAlarm {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if st.ActiveAlarm {
		t.Fatalf("expected alarm to stop after FocusChanged signal")
	}

	// 5. GNOME Terminal Multi-Tab Safety: if a single tab is closed (its PTY disappears)
	// while gnome-terminal-server (termPID) stays alive, the alarm must stop automatically
	// and never hold open file descriptors to the PTY.
	fakeTTY := filepath.Join(t.TempDir(), "pts-simulated")
	if err := os.WriteFile(fakeTTY, []byte("pty"), 0o600); err != nil {
		t.Fatalf("create fakeTTY: %v", err)
	}
	resp4, err := SendRequest(sockPath, Request{
		Action:       "trigger",
		AgentName:    "Hermes",
		TerminalPID:  termPID,
		AncestorPIDs: []int{termPID},
		TTYPath:      fakeTTY,
	}, 200*time.Millisecond)
	if err != nil || resp4.Status != "alarm-started" {
		t.Fatalf("expected alarm-started for tab PTY test, got %+v (err=%v)", resp4, err)
	}

	// Simulate closing the GNOME Terminal tab by removing its slave PTY
	if err := os.Remove(fakeTTY); err != nil {
		t.Fatalf("remove fakeTTY: %v", err)
	}

	deadline = time.Now().Add(1 * time.Second)
	for time.Now().Before(deadline) {
		st, _ = SendRequest(sockPath, Request{Action: "status"}, 100*time.Millisecond)
		if !st.ActiveAlarm {
			break
		}
		time.Sleep(25 * time.Millisecond)
	}
	if st.ActiveAlarm {
		t.Fatalf("expected alarm to stop when GNOME Terminal tab PTY is closed")
	}

	cancel()
	<-done
}
