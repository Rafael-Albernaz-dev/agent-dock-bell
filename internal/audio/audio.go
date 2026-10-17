// Package audio manages completion sound resolution and context-canceled
// playback loops without orphan processes or broad process sweeps.
package audio

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

const BundledAssetFilename = "universfield-new-notification-02-323592.mp3"

// ResolveSoundFile returns the path to the notification chime following RULES.md §2.
func ResolveSoundFile(repoRoot string) string {
	if custom := strings.TrimSpace(os.Getenv("AGENT_ALARM_SOUND")); custom != "" {
		if fileExists(custom) {
			return custom
		}
	}

	candidates := []string{}
	if repoRoot != "" {
		candidates = append(candidates, filepath.Join(repoRoot, "assets", BundledAssetFilename))
	}
	if exe, err := os.Executable(); err == nil {
		exeDir := filepath.Dir(exe)
		candidates = append(candidates,
			filepath.Join(exeDir, "..", "assets", BundledAssetFilename),
			filepath.Join(exeDir, "assets", BundledAssetFilename),
		)
	}
	if home, err := os.UserHomeDir(); err == nil {
		candidates = append(candidates,
			filepath.Join(home, ".local", "share", "agent-dock-bell", BundledAssetFilename),
			filepath.Join(home, ".local", "share", "sounds", BundledAssetFilename),
			filepath.Join(home, "Documents", "agent-dock-bell", "assets", BundledAssetFilename),
		)
	}
	candidates = append(candidates,
		"/usr/share/sounds/Yaru/stereo/bell.oga",
		"/usr/share/sounds/freedesktop/stereo/complete.oga",
	)

	for _, c := range candidates {
		if fileExists(c) {
			return c
		}
	}
	return ""
}

func fileExists(p string) bool {
	info, err := os.Stat(p)
	return err == nil && !info.IsDir() && info.Size() > 0
}

// BuildCommand returns the command and arguments to play soundFile once.
func BuildCommand(playerBin, soundFile, volume string) (string, []string) {
	if playerBin == "" {
		playerBin = os.Getenv("AGENT_ALARM_PLAYER")
	}
	if playerBin == "" {
		playerBin = "pw-play"
	}
	args := []string{}
	vol := strings.TrimSpace(volume)
	if vol != "" && vol != "1.0" && vol != "1" && filepath.Base(playerBin) == "pw-play" {
		args = append(args, "--volume", vol)
	}
	args = append(args, soundFile)
	return playerBin, args
}

// PlayOnce spawns a single non-blocking chime with a hard 4s timeout.
func PlayOnce(playerBin, soundFile, volume string) {
	if soundFile == "" || !fileExists(soundFile) {
		return
	}
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
		defer cancel()
		bin, args := BuildCommand(playerBin, soundFile, volume)
		cmd := exec.CommandContext(ctx, bin, args...)
		_ = cmd.Run()
	}()
}

// LoopConfig configures a bounded audio loop.
type LoopConfig struct {
	PlayerBin   string
	SoundFile   string
	Volume      string
	Interval    time.Duration
	MaxDuration time.Duration
	OnTick      func(iteration int)
}

// Loop plays SoundFile repeatedly until ctx is canceled or MaxDuration elapses.
// When ctx is canceled, any running child player process is killed immediately.
func Loop(ctx context.Context, cfg LoopConfig) {
	if cfg.Interval <= 0 {
		cfg.Interval = 3 * time.Second
	}
	if cfg.MaxDuration <= 0 {
		cfg.MaxDuration = 120 * time.Second
	}

	loopCtx, cancel := context.WithTimeout(ctx, cfg.MaxDuration)
	defer cancel()

	var (
		mu        sync.Mutex
		iteration int
	)

	for {
		if loopCtx.Err() != nil {
			return
		}
		iteration++
		if cfg.OnTick != nil {
			cfg.OnTick(iteration)
		}

		if cfg.SoundFile != "" && fileExists(cfg.SoundFile) {
			playCtx, playCancel := context.WithTimeout(loopCtx, 3*time.Second)
			bin, args := BuildCommand(cfg.PlayerBin, cfg.SoundFile, cfg.Volume)
			cmd := exec.CommandContext(playCtx, bin, args...)
			mu.Lock()
			_ = cmd.Run()
			mu.Unlock()
			playCancel()
		}

		select {
		case <-loopCtx.Done():
			return
		case <-time.After(cfg.Interval):
		}
	}
}
