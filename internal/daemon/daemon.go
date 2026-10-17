// Package daemon implements the persistent user-session alarm server,
// Unix socket IPC, D-Bus notification lifecycle, and FocusBridge integration.
package daemon

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/Rafael-Albernaz-dev/agent-dock-bell/internal/audio"
	"github.com/Rafael-Albernaz-dev/agent-dock-bell/internal/procinfo"
	"github.com/godbus/dbus/v5"
)

const (
	FocusBridgeBusName = "org.agentdockbell.FocusBridge"
	FocusBridgePath    = dbus.ObjectPath("/org/agentdockbell/FocusBridge")
	FocusBridgeIface   = "org.agentdockbell.FocusBridge"

	IdleMonitorBusName = "org.gnome.Mutter.IdleMonitor"
	IdleMonitorPath    = dbus.ObjectPath("/org/gnome/Mutter/IdleMonitor/Core")
	IdleMonitorIface   = "org.gnome.Mutter.IdleMonitor"

	NotificationsBusName = "org.freedesktop.Notifications"
	NotificationsPath    = dbus.ObjectPath("/org/freedesktop/Notifications")
	NotificationsIface   = "org.freedesktop.Notifications"

	// ActiveUserThresholdMs defines the idle threshold (2000 ms) below which the user
	// is considered actively using the screen right now (plays 1 chime + notification, no annoying loop).
	ActiveUserThresholdMs = 2000
)

// Request is sent by the CLI hook client to the daemon over the Unix socket.
type Request struct {
	Action       string `json:"action"` // "trigger", "stop", "status"
	AgentName    string `json:"agent_name,omitempty"`
	TerminalPID  int    `json:"terminal_pid,omitempty"`
	AncestorPIDs []int  `json:"ancestor_pids,omitempty"`
	TTYPath      string `json:"tty_path,omitempty"`
	DesktopID    string `json:"desktop_id,omitempty"`
	Force        bool   `json:"force,omitempty"`
}

// Response is returned by the daemon to the CLI client.
type Response struct {
	OK                   bool   `json:"ok"`
	Status               string `json:"status"`
	FocusBridgeAvailable bool   `json:"focus_bridge_available,omitempty"`
	IdleMonitorAvailable bool   `json:"idle_monitor_available,omitempty"`
	ActiveAlarm          bool   `json:"active_alarm,omitempty"`
	SoundFile            string `json:"sound_file,omitempty"`
	Error                string `json:"error,omitempty"`
}

// Config holds daemon runtime settings.
type Config struct {
	SocketPath    string
	SoundFile     string
	PlayerBin     string
	Volume        string
	AudioInterval time.Duration
	MaxAudioWait  time.Duration
	TTYPollPeriod time.Duration
	Logger        *slog.Logger
}

// DefaultSocketPath returns the session Unix domain socket path.
func DefaultSocketPath() string {
	if p := strings.TrimSpace(os.Getenv("AGENT_DOCK_BELL_SOCKET")); p != "" {
		return p
	}
	if xdg := strings.TrimSpace(os.Getenv("XDG_RUNTIME_DIR")); xdg != "" {
		return filepath.Join(xdg, "agent-dock-bell.sock")
	}
	return fmt.Sprintf("/tmp/agent-dock-bell-%d.sock", os.Getuid())
}

type activeAlarm struct {
	cancel       context.CancelFunc
	agentName    string
	terminalPID  int
	ancestorPIDs []int
	ttyPath      string
	initialAtime time.Time
	hasAtime     bool
	startedAt    time.Time
	notifyID     uint32
	idleWatchID  uint32
	done         chan struct{}
}

// Server manages active alarms, D-Bus signals, and Unix socket requests.
type Server struct {
	cfg      Config
	logger   *slog.Logger
	bus      *dbus.Conn
	listener net.Listener

	mu       sync.Mutex
	current  *activeAlarm
	lastNIDs map[string]uint32
}

// NewServer creates and initializes a daemon Server.
func NewServer(cfg Config) (*Server, error) {
	if cfg.SocketPath == "" {
		cfg.SocketPath = DefaultSocketPath()
	}
	if cfg.SoundFile == "" {
		cfg.SoundFile = audio.ResolveSoundFile("")
	}
	if cfg.Volume == "" {
		cfg.Volume = strings.TrimSpace(os.Getenv("AGENT_ALARM_VOLUME"))
		if cfg.Volume == "" {
			cfg.Volume = "1.0"
		}
	}
	if cfg.AudioInterval <= 0 {
		cfg.AudioInterval = 3 * time.Second
	}
	if cfg.MaxAudioWait <= 0 {
		cfg.MaxAudioWait = 120 * time.Second
	}
	if cfg.TTYPollPeriod <= 0 {
		cfg.TTYPollPeriod = 250 * time.Millisecond
	}
	if cfg.Logger == nil {
		cfg.Logger = slog.New(slog.NewJSONHandler(os.Stderr, nil))
	}

	_ = os.Remove(cfg.SocketPath)
	if err := os.MkdirAll(filepath.Dir(cfg.SocketPath), 0o700); err != nil {
		return nil, fmt.Errorf("mkdir socket dir: %w", err)
	}
	ln, err := net.Listen("unix", cfg.SocketPath)
	if err != nil {
		return nil, fmt.Errorf("listen unix %s: %w", cfg.SocketPath, err)
	}
	_ = os.Chmod(cfg.SocketPath, 0o600)

	bus, _ := dbus.ConnectSessionBus()

	return &Server{
		cfg:      cfg,
		logger:   cfg.Logger,
		bus:      bus,
		listener: ln,
		lastNIDs: make(map[string]uint32),
	}, nil
}

// Run serves Unix socket clients and D-Bus signals until ctx is canceled.
func (s *Server) Run(ctx context.Context) error {
	defer func() {
		s.stopCurrentAlarm("daemon-shutdown")
		_ = s.listener.Close()
		_ = os.Remove(s.cfg.SocketPath)
		if s.bus != nil {
			_ = s.bus.Close()
		}
	}()

	if s.bus != nil {
		go s.watchDBusSignals(ctx)
	}

	go func() {
		<-ctx.Done()
		_ = s.listener.Close()
	}()

	s.logger.Info("daemon_started", "socket", s.cfg.SocketPath, "sound", s.cfg.SoundFile)

	for {
		conn, err := s.listener.Accept()
		if err != nil {
			if ctx.Err() != nil || errors.Is(err, net.ErrClosed) {
				return nil
			}
			continue
		}
		go s.handleClient(ctx, conn)
	}
}

func (s *Server) handleClient(ctx context.Context, conn net.Conn) {
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(2 * time.Second))

	reader := bufio.NewReader(io.LimitReader(conn, 65536))
	line, err := reader.ReadBytes('\n')
	if err != nil && len(line) == 0 {
		return
	}

	var req Request
	if err := json.Unmarshal(line, &req); err != nil {
		s.writeResponse(conn, Response{OK: false, Error: "invalid-json"})
		return
	}

	resp := s.dispatch(ctx, req)
	s.writeResponse(conn, resp)
}

func (s *Server) writeResponse(conn net.Conn, resp Response) {
	b, err := json.Marshal(resp)
	if err != nil {
		return
	}
	_, _ = conn.Write(append(b, '\n'))
}

func (s *Server) dispatch(ctx context.Context, req Request) Response {
	switch req.Action {
	case "stop":
		s.stopCurrentAlarm("cli-stop")
		return Response{OK: true, Status: "stopped"}

	case "status":
		s.mu.Lock()
		active := s.current != nil
		s.mu.Unlock()
		_, fbOK := s.getFocusedWindowPID()
		_, imOK := s.getUserIdleTimeMs()
		return Response{
			OK:                   true,
			Status:               "running",
			FocusBridgeAvailable: fbOK,
			IdleMonitorAvailable: imOK,
			ActiveAlarm:          active,
			SoundFile:            s.cfg.SoundFile,
		}

	case "trigger":
		return s.handleTrigger(ctx, req)

	default:
		return Response{OK: false, Error: "unknown-action"}
	}
}

func (s *Server) handleTrigger(ctx context.Context, req Request) Response {
	agent := strings.TrimSpace(req.AgentName)
	if agent == "" {
		agent = "AI Agent"
	}

	// 1. Check if the caller's terminal is already focused via FocusBridge
	if !req.Force && req.TerminalPID > 1 {
		if focusedPID, ok := s.getFocusedWindowPID(); ok && focusedPID > 1 {
			if procinfo.SharesProcessTree(int(focusedPID), req.TerminalPID, req.AncestorPIDs) {
				s.stopCurrentAlarm("already-focused")
				audio.PlayOnce(s.cfg.PlayerBin, s.cfg.SoundFile, s.cfg.Volume)
				s.logger.Info("alarm_single_chime_focused", "agent", agent, "terminal_pid", req.TerminalPID)
				return Response{OK: true, Status: "single-chime-focused"}
			}
		}
	}

	// 2. Check if the user is actively interacting with the screen right now (mouse/keyboard idle < 2s).
	// When the user is already watching and moving the mouse/typing in another window, show the
	// notification banner and play ONE chime without repeating an annoying audio loop.
	if !req.Force {
		if idleMs, ok := s.getUserIdleTimeMs(); ok && idleMs < ActiveUserThresholdMs {
			s.stopCurrentAlarm("user-already-active")
			s.mu.Lock()
			replacesID := s.lastNIDs[agent]
			s.mu.Unlock()
			nid := s.sendNotification(agent, req.DesktopID, replacesID)
			if nid > 0 {
				s.mu.Lock()
				s.lastNIDs[agent] = nid
				s.mu.Unlock()
			}
			audio.PlayOnce(s.cfg.PlayerBin, s.cfg.SoundFile, s.cfg.Volume)
			s.logger.Info("alarm_single_chime_user_active", "agent", agent, "idle_ms", idleMs)
			return Response{OK: true, Status: "single-chime-user-active"}
		}
	}

	// 3. User is away / idle: replace any existing alarm, send notification, register active watch, and start loop
	s.stopCurrentAlarm("replaced-by-new-trigger")

	s.mu.Lock()
	replacesID := s.lastNIDs[agent]
	s.mu.Unlock()

	nid := s.sendNotification(agent, req.DesktopID, replacesID)
	initialAtime, hasAtime := procinfo.ReadAccessTime(req.TTYPath)
	watchID := s.addUserActiveWatch()

	alarmCtx, cancel := context.WithCancel(ctx)
	alarm := &activeAlarm{
		cancel:       cancel,
		agentName:    agent,
		terminalPID:  req.TerminalPID,
		ancestorPIDs: req.AncestorPIDs,
		ttyPath:      req.TTYPath,
		initialAtime: initialAtime,
		hasAtime:     hasAtime,
		startedAt:    time.Now(),
		notifyID:     nid,
		idleWatchID:  watchID,
		done:         make(chan struct{}),
	}

	s.mu.Lock()
	if nid > 0 {
		s.lastNIDs[agent] = nid
	}
	s.current = alarm
	s.mu.Unlock()

	s.logger.Info("alarm_started",
		"agent", agent,
		"terminal_pid", req.TerminalPID,
		"tty", req.TTYPath,
		"notification_id", nid,
		"idle_watch_id", watchID,
	)

	go s.runAlarmLoop(alarmCtx, alarm)

	return Response{OK: true, Status: "alarm-started"}
}

func (s *Server) runAlarmLoop(ctx context.Context, alarm *activeAlarm) {
	defer close(alarm.done)

	// Start lightweight watcher for TTY input, terminal closure, or user return
	go s.watchTTYAndProcess(ctx, alarm)

	audio.Loop(ctx, audio.LoopConfig{
		PlayerBin:   s.cfg.PlayerBin,
		SoundFile:   s.cfg.SoundFile,
		Volume:      s.cfg.Volume,
		Interval:    s.cfg.AudioInterval,
		MaxDuration: s.cfg.MaxAudioWait,
	})

	s.mu.Lock()
	if s.current == alarm {
		s.current = nil
	}
	s.mu.Unlock()
}

func (s *Server) watchTTYAndProcess(ctx context.Context, alarm *activeAlarm) {
	ticker := time.NewTicker(s.cfg.TTYPollPeriod)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			if alarm.ttyPath != "" {
				cur, ok := procinfo.ReadAccessTime(alarm.ttyPath)
				if !ok {
					// In multi-tab servers like gnome-terminal-server, closing a tab/window
					// destroys /dev/pts/N while the server PID stays alive.
					s.logger.Info("alarm_stopped_tty_closed", "tty", alarm.ttyPath)
					alarm.cancel()
					return
				}
				if alarm.hasAtime && cur.After(alarm.initialAtime) {
					s.logger.Info("alarm_stopped_tty_input", "tty", alarm.ttyPath)
					alarm.cancel()
					return
				}
			}
			if alarm.terminalPID > 1 && !procinfo.IsProcessAlive(alarm.terminalPID) {
				s.logger.Info("alarm_stopped_terminal_closed", "terminal_pid", alarm.terminalPID)
				alarm.cancel()
				return
			}
			// After the first 800ms of alarm playback, if Mutter IdleMonitor reports the user
			// just moved the mouse, clicked, or pressed a key (idle < 400ms), stop the alarm immediately.
			if time.Since(alarm.startedAt) >= 800*time.Millisecond {
				if idleMs, ok := s.getUserIdleTimeMs(); ok && idleMs < 400 {
					s.logger.Info("alarm_stopped_user_active_idle_reset", "idle_ms", idleMs)
					alarm.cancel()
					return
				}
			}
		}
	}
}

func (s *Server) stopCurrentAlarm(reason string) {
	s.mu.Lock()
	alarm := s.current
	s.current = nil
	s.mu.Unlock()

	if alarm != nil {
		s.logger.Info("alarm_stopping", "agent", alarm.agentName, "reason", reason)
		alarm.cancel()
		if alarm.idleWatchID > 0 {
			s.removeUserActiveWatch(alarm.idleWatchID)
		}
		select {
		case <-alarm.done:
		case <-time.After(500 * time.Millisecond):
		}
	}
}

func (s *Server) getFocusedWindowPID() (uint32, bool) {
	if s.bus == nil {
		return 0, false
	}
	ctx, cancel := context.WithTimeout(context.Background(), 80*time.Millisecond)
	defer cancel()

	obj := s.bus.Object(FocusBridgeBusName, FocusBridgePath)
	var pid uint32
	var wmClass string
	err := obj.CallWithContext(ctx, FocusBridgeIface+".GetFocusedPID", 0).Store(&pid, &wmClass)
	if err != nil {
		return 0, false
	}
	return pid, true
}

func (s *Server) getUserIdleTimeMs() (uint64, bool) {
	if s.bus == nil {
		return 0, false
	}
	ctx, cancel := context.WithTimeout(context.Background(), 80*time.Millisecond)
	defer cancel()

	obj := s.bus.Object(IdleMonitorBusName, IdleMonitorPath)
	var idleMs uint64
	err := obj.CallWithContext(ctx, IdleMonitorIface+".GetIdletime", 0).Store(&idleMs)
	if err != nil {
		return 0, false
	}
	return idleMs, true
}

func (s *Server) addUserActiveWatch() uint32 {
	if s.bus == nil {
		return 0
	}
	ctx, cancel := context.WithTimeout(context.Background(), 80*time.Millisecond)
	defer cancel()

	obj := s.bus.Object(IdleMonitorBusName, IdleMonitorPath)
	var watchID uint32
	if err := obj.CallWithContext(ctx, IdleMonitorIface+".AddUserActiveWatch", 0).Store(&watchID); err != nil {
		return 0
	}
	return watchID
}

func (s *Server) removeUserActiveWatch(watchID uint32) {
	if s.bus == nil || watchID == 0 {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 80*time.Millisecond)
	defer cancel()

	obj := s.bus.Object(IdleMonitorBusName, IdleMonitorPath)
	_ = obj.CallWithContext(ctx, IdleMonitorIface+".RemoveWatch", 0, watchID).Err
}

func (s *Server) activateWindow(pid int) bool {
	if s.bus == nil || pid <= 1 {
		return false
	}
	ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
	defer cancel()

	obj := s.bus.Object(FocusBridgeBusName, FocusBridgePath)
	var ok bool
	err := obj.CallWithContext(ctx, FocusBridgeIface+".ActivateWindow", 0, uint32(pid)).Store(&ok)
	return err == nil && ok
}

func (s *Server) sendNotification(agentName, desktopID string, replacesID uint32) uint32 {
	if s.bus == nil {
		return 0
	}
	ctx, cancel := context.WithTimeout(context.Background(), 1*time.Second)
	defer cancel()

	icon := strings.TrimSuffix(desktopID, ".desktop")
	if icon == "" {
		icon = "utilities-terminal"
	}

	hints := map[string]dbus.Variant{
		"suppress-sound":                  dbus.MakeVariant(true),
		"urgency":                         dbus.MakeVariant(byte(1)),
		"x-canonical-private-synchronous": dbus.MakeVariant(agentName),
	}

	obj := s.bus.Object(NotificationsBusName, NotificationsPath)
	var nid uint32
	err := obj.CallWithContext(
		ctx,
		NotificationsIface+".Notify",
		0,
		agentName,
		replacesID,
		icon,
		"Task Completed!",
		fmt.Sprintf("%s has finished executing.", agentName),
		[]string{"default", "Open Terminal"},
		hints,
		int32(5000),
	).Store(&nid)
	if err != nil {
		s.logger.Warn("dbus_notify_failed", "err", err)
		return 0
	}
	return nid
}

func (s *Server) watchDBusSignals(ctx context.Context) {
	_ = s.bus.AddMatchSignal(
		dbus.WithMatchInterface(FocusBridgeIface),
		dbus.WithMatchMember("FocusChanged"),
	)
	_ = s.bus.AddMatchSignal(
		dbus.WithMatchInterface(IdleMonitorIface),
		dbus.WithMatchMember("WatchFired"),
	)
	_ = s.bus.AddMatchSignal(
		dbus.WithMatchInterface(NotificationsIface),
		dbus.WithMatchMember("ActionInvoked"),
	)
	_ = s.bus.AddMatchSignal(
		dbus.WithMatchInterface(NotificationsIface),
		dbus.WithMatchMember("NotificationClosed"),
	)

	sigCh := make(chan *dbus.Signal, 32)
	s.bus.Signal(sigCh)

	for {
		select {
		case <-ctx.Done():
			return
		case sig, ok := <-sigCh:
			if !ok || sig == nil {
				return
			}
			s.handleDBusSignal(sig)
		}
	}
}

func (s *Server) handleDBusSignal(sig *dbus.Signal) {
	switch sig.Name {
	case FocusBridgeIface + ".FocusChanged":
		if len(sig.Body) < 1 {
			return
		}
		focusedPID, ok := toUint32(sig.Body[0])
		if !ok || focusedPID <= 1 {
			return
		}
		s.mu.Lock()
		alarm := s.current
		s.mu.Unlock()
		if alarm != nil && procinfo.SharesProcessTree(int(focusedPID), alarm.terminalPID, alarm.ancestorPIDs) {
			s.stopCurrentAlarm("window-focused")
		}

	case IdleMonitorIface + ".WatchFired":
		if len(sig.Body) < 1 {
			return
		}
		watchID, ok := toUint32(sig.Body[0])
		if !ok {
			return
		}
		s.mu.Lock()
		alarm := s.current
		s.mu.Unlock()
		if alarm != nil && (alarm.idleWatchID == 0 || alarm.idleWatchID == watchID) {
			s.stopCurrentAlarm("user-activity-watch-fired")
		}

	case NotificationsIface + ".ActionInvoked":
		if len(sig.Body) < 2 {
			return
		}
		nid, ok1 := toUint32(sig.Body[0])
		action, ok2 := sig.Body[1].(string)
		if !ok1 || !ok2 {
			return
		}
		s.mu.Lock()
		alarm := s.current
		s.mu.Unlock()
		if alarm != nil && (alarm.notifyID == 0 || alarm.notifyID == nid) && action == "default" {
			if alarm.terminalPID > 1 {
				s.activateWindow(alarm.terminalPID)
			}
			s.stopCurrentAlarm("notification-clicked")
		}

	case NotificationsIface + ".NotificationClosed":
		if len(sig.Body) < 2 {
			return
		}
		nid, ok1 := toUint32(sig.Body[0])
		reason, ok2 := toUint32(sig.Body[1])
		if !ok1 || !ok2 {
			return
		}
		// reason == 2 means dismissed by user click/swipe
		if reason == 2 {
			s.mu.Lock()
			alarm := s.current
			s.mu.Unlock()
			if alarm != nil && alarm.notifyID == nid {
				s.stopCurrentAlarm("notification-dismissed")
			}
		}
	}
}

func toUint32(v any) (uint32, bool) {
	switch x := v.(type) {
	case uint32:
		return x, true
	case int32:
		return uint32(x), true
	case int:
		return uint32(x), true
	default:
		return 0, false
	}
}

// SendRequest sends a Request to the daemon Unix socket with a strict timeout.
func SendRequest(socketPath string, req Request, timeout time.Duration) (Response, error) {
	if socketPath == "" {
		socketPath = DefaultSocketPath()
	}
	if timeout <= 0 {
		timeout = 35 * time.Millisecond
	}
	conn, err := net.DialTimeout("unix", socketPath, timeout)
	if err != nil {
		return Response{}, err
	}
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(timeout))

	b, err := json.Marshal(req)
	if err != nil {
		return Response{}, err
	}
	if _, err := conn.Write(append(b, '\n')); err != nil {
		return Response{}, err
	}

	line, err := bufio.NewReader(conn).ReadBytes('\n')
	if err != nil {
		return Response{}, err
	}
	var resp Response
	if err := json.Unmarshal(line, &resp); err != nil {
		return Response{}, err
	}
	return resp, nil
}
