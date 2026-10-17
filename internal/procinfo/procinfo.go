// Package procinfo inspects /proc in read-only mode to discover the caller's
// terminal emulator PID, ancestor process chain, and slave PTS (/dev/pts/N).
package procinfo

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"
)

// CallerContext holds the discovered terminal process and TTY for a hook invocation.
type CallerContext struct {
	TerminalPID  int    `json:"terminal_pid"`
	AncestorPIDs []int  `json:"ancestor_pids"`
	TTYPath      string `json:"tty_path"`
	DesktopID    string `json:"desktop_id"`
}

var knownTerminals = map[string]string{
	"gnome-terminal-server": "org.gnome.Terminal.desktop",
	"gnome-terminal-":       "org.gnome.Terminal.desktop",
	"gnome-terminal":        "org.gnome.Terminal.desktop",
	"ptyxis":                "org.gnome.Ptyxis.desktop",
	"alacritty":             "Alacritty.desktop",
	"kitty":                 "kitty.desktop",
	"tilix":                 "com.gexperts.Tilix.desktop",
	"konsole":               "org.kde.konsole.desktop",
	"foot":                  "foot.desktop",
	"wezterm":               "org.wezfurlong.wezterm.desktop",
	"wezterm-gui":           "org.wezfurlong.wezterm.desktop",
	"xfce4-terminal":        "xfce4-terminal.desktop",
	"terminator":            "terminator.desktop",
	"xterm":                 "xterm.desktop",
	"ghostty":               "com.mitchellh.ghostty.desktop",
	"warp":                  "dev.warp.Warp.desktop",
	"code":                  "code.desktop",
	"cursor":                "cursor.desktop",
	"windsurf":              "windsurf.desktop",
	"zed":                   "dev.zed.Zed.desktop",
}

// Discover inspects the current process's ancestor chain in /proc.
func Discover(startPID int) CallerContext {
	if startPID <= 0 {
		startPID = os.Getpid()
	}
	ctx := CallerContext{
		DesktopID: "org.gnome.Terminal.desktop",
	}

	curr := startPID
	visited := make(map[int]bool, 16)
	for curr > 1 && !visited[curr] && len(ctx.AncestorPIDs) < 32 {
		visited[curr] = true
		ctx.AncestorPIDs = append(ctx.AncestorPIDs, curr)

		if ctx.TTYPath == "" {
			if tty := readProcessTTY(curr); tty != "" {
				ctx.TTYPath = tty
			}
		}

		comm, exeName := readProcessNames(curr)
		if ctx.TerminalPID == 0 {
			if dID, ok := matchTerminal(comm, exeName); ok {
				ctx.TerminalPID = curr
				ctx.DesktopID = dID
			}
		}

		ppid := readPPID(curr)
		if ppid <= 1 || ppid == curr {
			break
		}
		curr = ppid
	}

	return ctx
}

func matchTerminal(comm, exeName string) (string, bool) {
	if dID, ok := knownTerminals[exeName]; ok {
		return dID, true
	}
	if dID, ok := knownTerminals[comm]; ok {
		return dID, true
	}
	return "", false
}

func readPPID(pid int) int {
	data, err := os.ReadFile(fmt.Sprintf("/proc/%d/stat", pid))
	if err != nil {
		return 0
	}
	s := string(data)
	idx := strings.LastIndexByte(s, ')')
	if idx == -1 || idx+2 >= len(s) {
		return 0
	}
	fields := strings.Fields(s[idx+2:])
	if len(fields) < 2 {
		return 0
	}
	ppid, err := strconv.Atoi(fields[1])
	if err != nil {
		return 0
	}
	return ppid
}

func readProcessNames(pid int) (comm string, exeBase string) {
	if b, err := os.ReadFile(fmt.Sprintf("/proc/%d/comm", pid)); err == nil {
		comm = strings.ToLower(strings.TrimSpace(string(b)))
	}
	if b, err := os.ReadFile(fmt.Sprintf("/proc/%d/cmdline", pid)); err == nil && len(b) > 0 {
		first := b
		if nul := strings.IndexByte(string(b), 0); nul >= 0 {
			first = b[:nul]
		}
		exeBase = strings.ToLower(filepath.Base(string(first)))
	}
	return comm, exeBase
}

func readProcessTTY(pid int) string {
	for _, fd := range []int{0, 1, 2} {
		target, err := os.Readlink(fmt.Sprintf("/proc/%d/fd/%d", pid, fd))
		if err != nil {
			continue
		}
		if strings.HasPrefix(target, "/dev/pts/") {
			return target
		}
	}
	return ""
}

// ReadAccessTime returns the read-only access timestamp (st_atime) of a path.
// It NEVER opens the file or modifies terminal state.
func ReadAccessTime(path string) (time.Time, bool) {
	if path == "" {
		return time.Time{}, false
	}
	info, err := os.Stat(path)
	if err != nil {
		return time.Time{}, false
	}
	st, ok := info.Sys().(*syscall.Stat_t)
	if !ok || st == nil {
		return info.ModTime(), true
	}
	return time.Unix(int64(st.Atim.Sec), int64(st.Atim.Nsec)), true
}

// IsProcessAlive checks whether pid is alive using signal 0.
func IsProcessAlive(pid int) bool {
	if pid <= 1 {
		return false
	}
	err := syscall.Kill(pid, 0)
	return err == nil || err == syscall.EPERM
}

// SharesProcessTree reports whether candidatePID matches targetPID or is an
// ancestor/descendant of targetPID.
func SharesProcessTree(candidatePID int, targetPID int, targetAncestors []int) bool {
	if candidatePID <= 1 || targetPID <= 1 {
		return false
	}
	if candidatePID == targetPID {
		return true
	}
	for _, a := range targetAncestors {
		if candidatePID == a {
			return true
		}
	}
	// Walk candidatePID's parents up to 12 hops to see if it is a child of targetPID
	curr := candidatePID
	for i := 0; i < 12 && curr > 1; i++ {
		if curr == targetPID {
			return true
		}
		curr = readPPID(curr)
	}
	return false
}
