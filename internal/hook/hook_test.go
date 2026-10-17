package hook

import (
	"encoding/json"
	"io"
	"strings"
	"testing"
	"time"
)

func TestEvaluate_RegressionAndContracts(t *testing.T) {
	t.Parallel()

	// Build a 150 KB payload (> 64 KB limit that broke v1)
	largeTranscript := strings.Repeat("assistant output line with synthetic and ChatGPT words. ", 3000)
	largePayloadBytes, err := json.Marshal(map[string]any{
		"hook_event_name":        "Stop",
		"fullyIdle":              true,
		"last_assistant_message": largeTranscript,
	})
	if err != nil {
		t.Fatalf("marshal large payload: %v", err)
	}
	if len(largePayloadBytes) <= 65536 {
		t.Fatalf("expected payload > 64KB, got %d", len(largePayloadBytes))
	}

	cases := []struct {
		name      string
		stdin     string
		args      []string
		env       map[string]string
		wantAlert bool
		wantAgent string
	}{
		{
			name:      "Antigravity plain Stop triggers alarm",
			stdin:     `{"hook_event_name":"Stop","fullyIdle":true}`,
			args:      []string{"Antigravity"},
			wantAlert: true,
			wantAgent: "Antigravity",
		},
		{
			name:      "REGRESSION: Stop mentioning ChatGPT in message MUST NOT be skipped",
			stdin:     `{"hook_event_name":"Stop","last_assistant_message":"Let us compare this with ChatGPT and Codex Desktop"}`,
			args:      []string{"Antigravity"},
			wantAlert: true,
			wantAgent: "Antigravity",
		},
		{
			name:      "REGRESSION: Stop mentioning synthetic or test-session in message MUST NOT be skipped",
			stdin:     `{"hook_event_name":"Stop","last_assistant_message":"Generated synthetic dataset for test-session benchmark"}`,
			args:      []string{"Codex CLI"},
			wantAlert: true,
			wantAgent: "Codex CLI",
		},
		{
			name:      "REGRESSION: Payload > 64KB (150KB) MUST NOT be truncated or skipped",
			stdin:     string(largePayloadBytes),
			args:      []string{"Antigravity"},
			wantAlert: true,
			wantAgent: "Antigravity",
		},
		{
			name:      "REGRESSION: Hermes turn_exit_reason=max_tokens MUST trigger alarm",
			stdin:     `{"hook_event_name":"on_session_end","completed":true,"turn_exit_reason":"max_tokens"}`,
			args:      []string{"Hermes"},
			wantAlert: true,
			wantAgent: "Hermes",
		},
		{
			name:      "REGRESSION: Task ID starting with review-123 in user prompt or normal task triggers alarm",
			stdin:     `{"hook_event_name":"Stop","task_id":"review-123"}`,
			args:      []string{"Codex CLI"},
			wantAlert: true,
			wantAgent: "Codex CLI",
		},
		{
			name:      "REGRESSION: ChatGPT Desktop env vars in parent session do not silence CLI terminals",
			stdin:     `{"hook_event_name":"Stop"}`,
			args:      []string{"Codex CLI"},
			env:       map[string]string{"CODEX_APP_TOOLS_PIPE_PATH": "/tmp/pipe", "CHATGPT_DESKTOP": "1"},
			wantAlert: true,
			wantAgent: "Codex CLI",
		},
		{
			name:      "Antigravity fullyIdle=false is skipped",
			stdin:     `{"hook_event_name":"Stop","fullyIdle":false}`,
			args:      []string{"Antigravity"},
			wantAlert: false,
			wantAgent: "Antigravity",
		},
		{
			name:      "Codex intermediate tool_use is skipped",
			stdin:     `{"hook_event_name":"Stop","stop_reason":"tool_use"}`,
			args:      []string{"Codex CLI"},
			wantAlert: false,
			wantAgent: "Codex CLI",
		},
		{
			name:      "Subagent payload is skipped",
			stdin:     `{"hook_event_name":"Stop","is_subagent":true}`,
			args:      []string{"Antigravity"},
			wantAlert: false,
			wantAgent: "Antigravity",
		},
		{
			name:      "Hermes delegated child context env is skipped",
			stdin:     `{"hook_event_name":"on_session_end","completed":true}`,
			args:      []string{"Hermes"},
			env:       map[string]string{"HERMES_DELEGATED_CHILD_CONTEXT": "1"},
			wantAlert: false,
			wantAgent: "Hermes",
		},
		{
			name:      "Hermes interrupted session is skipped",
			stdin:     `{"hook_event_name":"on_session_end","completed":true,"interrupted":true}`,
			args:      []string{"Hermes"},
			wantAlert: false,
			wantAgent: "Hermes",
		},
		{
			name:      "Non-completion hook event is skipped",
			stdin:     `{"hook_event_name":"PreToolUse"}`,
			args:      []string{"Antigravity"},
			wantAlert: false,
			wantAgent: "Antigravity",
		},
		{
			name:      "Dry-run flag skips alert cleanly",
			stdin:     `{"hook_event_name":"Stop"}`,
			args:      []string{"--dry-run", "Antigravity"},
			wantAlert: false,
			wantAgent: "Antigravity",
		},
	}

	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			got := Evaluate([]byte(tc.stdin), tc.args, tc.env)
			if got.ShouldAlert != tc.wantAlert {
				t.Fatalf("Evaluate() ShouldAlert = %v (reason=%q), want %v", got.ShouldAlert, got.Reason, tc.wantAlert)
			}
			if got.AgentName != tc.wantAgent {
				t.Fatalf("Evaluate() AgentName = %q, want %q", got.AgentName, tc.wantAgent)
			}
		})
	}
}

func TestReadBounded_UnclosedPipeReturnsImmediately(t *testing.T) {
	t.Parallel()
	pr, pw := io.Pipe()
	defer pr.Close()
	defer pw.Close()

	go func() {
		_, _ = pw.Write([]byte(`{"hook_event_name":"Stop","fullyIdle":true}`))
		// Intentionally do NOT close pw to simulate an agent/terminal keeping stdin pipe open
	}()

	done := make(chan []byte, 1)
	go func() {
		b, _ := ReadBounded(pr)
		done <- b
	}()

	select {
	case data := <-done:
		dec := Evaluate(data, []string{"Antigravity"}, nil)
		if !dec.ShouldAlert {
			t.Fatalf("expected ShouldAlert=true from unclosed pipe, got false (%s)", dec.Reason)
		}
	case <-time.After(100 * time.Millisecond):
		t.Fatalf("ReadBounded blocked waiting for EOF on unclosed pipe")
	}
}
