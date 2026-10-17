// Package hook provides deterministic, structured JSON hook payload parsing
// for AI CLI agents (Antigravity, Codex CLI, Hermes) without raw string heuristics.
package hook

import (
	"bytes"
	"encoding/json"
	"io"
	"strings"
)

// MaxStdinBytes caps hook stdin reading at 10 MB so large agent transcripts
// are never truncated at 64 KB.
const MaxStdinBytes = 10 * 1024 * 1024

// Decision represents the deterministic evaluation of an agent lifecycle hook.
type Decision struct {
	ShouldAlert bool
	AgentName   string
	Reason      string
}

// Payload models only the structured lifecycle metadata fields emitted by
// Antigravity CLI, OpenAI Codex CLI, and Hermes Agent.
type Payload struct {
	// Event identification
	HookEventNameAlt1 string `json:"hook_event_name"`
	HookEventNameAlt2 string `json:"hookEventName"`
	HookEventNameAlt3 string `json:"hook-event-name"`

	// Antigravity fields
	FullyIdle *bool `json:"fullyIdle"`

	// Codex / Claude / generic stop reason
	StopReasonAlt1 string `json:"stop_reason"`
	StopReasonAlt2 string `json:"stopReason"`
	StopReasonAlt3 string `json:"stop-reason"`
	ToolName       string `json:"tool_name"`

	// Hermes completion status
	Completed      *bool  `json:"completed"`
	Interrupted    *bool  `json:"interrupted"`
	Failed         *bool  `json:"failed"`
	TurnExitReason string `json:"turn_exit_reason"`

	// Subagent / background markers
	ParentSessionIDAlt1 string `json:"parent_session_id"`
	ParentSessionIDAlt2 string `json:"parentSessionId"`
	ParentID            string `json:"parent_id"`
	ChildRole           string `json:"child_role"`
	IsSubagent          *bool  `json:"is_subagent"`
	IsBackground        *bool  `json:"is_background"`
	TaskID              string `json:"task_id"`
	WriteOrigin         string `json:"write_origin"`
	WriteContext        string `json:"write_context"`

	// Originator metadata (only structured fields, never transcript text)
	Originator string `json:"originator"`
	Client     string `json:"client"`

	Extra *Payload `json:"extra"`
}

func (p *Payload) eventName() string {
	for _, v := range []string{p.HookEventNameAlt1, p.HookEventNameAlt2, p.HookEventNameAlt3} {
		if strings.TrimSpace(v) != "" {
			return strings.TrimSpace(v)
		}
	}
	return ""
}

func (p *Payload) stopReason() string {
	for _, v := range []string{p.StopReasonAlt1, p.StopReasonAlt2, p.StopReasonAlt3} {
		if strings.TrimSpace(v) != "" {
			return strings.TrimSpace(v)
		}
	}
	return ""
}

func (p *Payload) hasSubagentMarker() (bool, string) {
	if p.IsSubagent != nil && *p.IsSubagent {
		return true, "payload.is_subagent=true"
	}
	if p.IsBackground != nil && *p.IsBackground {
		return true, "payload.is_background=true"
	}
	if p.ParentSessionIDAlt1 != "" || p.ParentSessionIDAlt2 != "" || p.ParentID != "" {
		return true, "payload.parent_session_id"
	}
	if p.ChildRole != "" {
		return true, "payload.child_role=" + p.ChildRole
	}
	if p.ToolName != "" {
		return true, "payload.tool_name=" + p.ToolName
	}
	if p.WriteOrigin == "background_review" || p.WriteOrigin == "review" ||
		p.WriteContext == "background_review" || p.WriteContext == "review" {
		return true, "payload.background_review"
	}
	for _, prefix := range []string{"bg-", "subagent", "cron-", "heartbeat-", "task-sub-"} {
		if strings.HasPrefix(p.TaskID, prefix) {
			return true, "payload.task_id=" + p.TaskID
		}
	}
	if p.Extra != nil {
		return p.Extra.hasSubagentMarker()
	}
	return false, ""
}

func (p *Payload) completionProblem() (bool, string) {
	if p.FullyIdle != nil && !*p.FullyIdle {
		return true, "fullyIdle=false"
	}
	if p.Completed != nil && !*p.Completed {
		return true, "completed=false"
	}
	if p.Interrupted != nil && *p.Interrupted {
		return true, "interrupted=true"
	}
	if p.Failed != nil && *p.Failed {
		return true, "failed=true"
	}
	if sr := strings.ToLower(p.stopReason()); sr == "tool_use" || sr == "intermediate" {
		return true, "stop_reason=" + sr
	}
	if reason := strings.ToLower(strings.TrimSpace(p.TurnExitReason)); reason != "" {
		for _, badPrefix := range []string{
			"guardrail", "halt", "interrupt", "error", "timeout",
			"session_persistence", "local_processing", "repeated_outer",
		} {
			if strings.HasPrefix(reason, badPrefix) {
				return true, "turn_exit_reason=" + reason
			}
		}
	}
	if p.Extra != nil {
		return p.Extra.completionProblem()
	}
	return false, ""
}

// ReadBounded reads up to MaxStdinBytes from r.
// Once a complete valid JSON object is assembled in the buffer, it returns
// immediately without waiting for pipe EOF (preventing hangs when callers keep pipes open).
func ReadBounded(r io.Reader) ([]byte, error) {
	if r == nil {
		return nil, nil
	}
	var buf bytes.Buffer
	chunk := make([]byte, 8192)
	for buf.Len() < MaxStdinBytes {
		n, err := r.Read(chunk)
		if n > 0 {
			buf.Write(chunk[:n])
			trimmed := bytes.TrimSpace(buf.Bytes())
			if len(trimmed) > 0 && trimmed[0] == '{' && trimmed[len(trimmed)-1] == '}' && json.Valid(trimmed) {
				return buf.Bytes(), nil
			}
		}
		if err != nil {
			if err == io.EOF {
				return buf.Bytes(), nil
			}
			return buf.Bytes(), err
		}
	}
	return buf.Bytes(), nil
}

// Evaluate determines whether a hook invocation should trigger an alarm.
// It inspects ONLY structured JSON fields and explicit subagent environment flags.
func Evaluate(rawStdin []byte, args []string, env map[string]string) Decision {
	agentName := resolveAgentName(args)

	for _, arg := range args {
		if arg == "--force" {
			return Decision{ShouldAlert: true, AgentName: agentName, Reason: "forced"}
		}
		if arg == "--dry-run" || arg == "--test" {
			return Decision{ShouldAlert: false, AgentName: agentName, Reason: "dry-run"}
		}
	}

	// Explicit child subagent environment variables only.
	if env["HERMES_DELEGATED_CHILD_CONTEXT"] != "" {
		return Decision{ShouldAlert: false, AgentName: agentName, Reason: "env:HERMES_DELEGATED_CHILD_CONTEXT"}
	}
	if env["ANTIGRAVITY_SUBAGENT"] != "" || env["SUBAGENT_ID"] != "" {
		return Decision{ShouldAlert: false, AgentName: agentName, Reason: "env:ANTIGRAVITY_SUBAGENT"}
	}

	trimmed := bytes.TrimSpace(rawStdin)
	if len(trimmed) == 0 {
		return Decision{ShouldAlert: true, AgentName: agentName, Reason: "empty-stdin-trigger"}
	}

	var p Payload
	if err := json.Unmarshal(trimmed, &p); err != nil {
		return Decision{ShouldAlert: false, AgentName: agentName, Reason: "invalid-json"}
	}

	// Only skip if the structured originator field explicitly reports ChatGPT Desktop UI.
	orig := strings.ToLower(strings.TrimSpace(p.Originator))
	if orig == "" && p.Extra != nil {
		orig = strings.ToLower(strings.TrimSpace(p.Extra.Originator))
	}
	if orig == "chatgpt desktop" || orig == "codex desktop" {
		return Decision{ShouldAlert: false, AgentName: agentName, Reason: "originator=" + orig}
	}

	if ev := p.eventName(); ev != "" {
		if ev != "Stop" && ev != "stop" && ev != "on_session_end" {
			return Decision{ShouldAlert: false, AgentName: agentName, Reason: "event=" + ev}
		}
	}

	if isSub, why := p.hasSubagentMarker(); isSub {
		return Decision{ShouldAlert: false, AgentName: agentName, Reason: why}
	}

	if bad, why := p.completionProblem(); bad {
		return Decision{ShouldAlert: false, AgentName: agentName, Reason: why}
	}

	return Decision{ShouldAlert: true, AgentName: agentName, Reason: "turn-completed"}
}

func resolveAgentName(args []string) string {
	for _, a := range args {
		if !strings.HasPrefix(a, "-") && strings.TrimSpace(a) != "" {
			return strings.TrimSpace(a)
		}
	}
	return "AI Agent"
}
