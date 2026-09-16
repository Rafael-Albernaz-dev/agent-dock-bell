import pytest
from agent_dock_bell.cli import should_skip_event

def test_should_skip_test_flags():
    assert should_skip_event("", None, ["--test"]) is True
    assert should_skip_event("", None, ["--dry-run"]) is True
    assert should_skip_event("", None, ["Antigravity CLI"]) is False

def test_should_skip_synthetic_stdin():
    assert should_skip_event('{"extra": "test-session"}', None, []) is True
    assert should_skip_event('{"event": "synthetic payload"}', None, []) is True

def test_should_skip_antigravity_not_fully_idle():
    # When background tasks or subagents are running, fullyIdle is False
    payload_running = {"fullyIdle": False, "executionNum": 1}
    assert should_skip_event("", payload_running, []) is True

    # When prompt is truly finished, fullyIdle is True
    payload_idle = {"fullyIdle": True, "executionNum": 1}
    assert should_skip_event("", payload_idle, []) is False

def test_should_skip_hermes_intermediate_events():
    # Intermediate LLM call should be ignored
    payload_intermediate = {"hook_event_name": "post_llm_call"}
    assert should_skip_event("", payload_intermediate, []) is True

    # Final session end should be processed
    payload_session_end = {
        "hook_event_name": "on_session_end",
        "extra": {"completed": True, "interrupted": False},
    }
    assert should_skip_event("", payload_session_end, []) is False

    # Interrupted or uncompleted session should be ignored
    payload_interrupted = {
        "hook_event_name": "on_session_end",
        "extra": {"completed": False, "interrupted": True},
    }
    assert should_skip_event("", payload_interrupted, []) is True

def test_should_skip_codex_tool_use():
    payload_tool = {"stop_reason": "tool_use"}
    assert should_skip_event("", payload_tool, []) is True

    payload_done = {"stop_reason": "end_turn"}
    assert should_skip_event("", payload_done, []) is False
