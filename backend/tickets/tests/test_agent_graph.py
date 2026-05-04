"""Tests for tickets/services/agent_graph.py.

Mocks the Groq API (_call_groq) so no real network calls are made.
Tests verify:
  1. Auto-escalation when the LLM returns confidence < 0.6 on the first step.
  2. Auto-escalation when MAX_ITERATIONS is reached before a DONE signal.
  3. Successful resolution when the LLM returns DONE with high confidence.
  4. The AgentResult TypedDict has the exact expected keys.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

# ── Helpers ────────────────────────────────────────────────────────────────────

EXPECTED_RESULT_KEYS = {
    "final_action",
    "resolution",
    "escalation_reason",
    "tools_trace",
    "iterations",
    "confidence_score",
}

# Valid tool responses for the mock TOOL_REGISTRY calls.
_ORDER_STATUS_RESULT = {
    "order_id": "5001",
    "status": "paid",
    "shipping_status": "fulfilled",
    "found": True,
}


def _groq_low_confidence_response() -> str:
    """LLM says it wants to call a tool but is very uncertain (confidence 0.3)."""
    return json.dumps({
        "tool": "get_order_status",
        "args": {"order_id": "5001"},
        "confidence": 0.3,
        "resolution": "",
    })


def _groq_tool_call_response(iteration: int) -> str:
    """LLM keeps calling get_order_status every iteration (simulates loop)."""
    return json.dumps({
        "tool": "get_order_status",
        "args": {"order_id": "5001"},
        "confidence": 0.7,
        "resolution": "",
    })


def _groq_done_response() -> str:
    """LLM says DONE with high confidence after one tool call."""
    return json.dumps({
        "tool": "DONE",
        "args": {},
        "confidence": 0.92,
        "resolution": "Your order #5001 is paid and has been fulfilled.",
    })


def _groq_faq_then_done(call_count: list) -> str:
    """First call uses FAQ tool; second call returns DONE."""
    call_count[0] += 1
    if call_count[0] == 1:
        return json.dumps({
            "tool": "get_faq_answer",
            "args": {"query": "how to request refund"},
            "confidence": 0.8,
            "resolution": "",
        })
    return json.dumps({
        "tool": "DONE",
        "args": {},
        "confidence": 0.9,
        "resolution": "Refunds are available within 30 days of purchase.",
    })


# ── Test: auto-escalation on low confidence ────────────────────────────────────

class TestAutoEscalateOnLowConfidence:
    def test_escalates_when_confidence_below_threshold(self):
        """Agent must escalate immediately when first LLM response has confidence < 0.6."""
        from tickets.services.agent_graph import run_agent

        with patch(
            "tickets.services.agent_graph._call_groq",
            return_value=_groq_low_confidence_response(),
        ):
            result = run_agent(query="Where is my order?", order_id="5001")

        assert set(result.keys()) == EXPECTED_RESULT_KEYS
        assert result["final_action"] == "escalated"
        assert "0.3" in result["escalation_reason"] or "threshold" in result["escalation_reason"]
        assert result["confidence_score"] == pytest.approx(0.3, abs=0.01)
        # No tools should have been called — the confidence gate fires before execute_tool.
        assert result["tools_trace"] == []
        assert result["iterations"] == 0

    def test_no_tool_called_on_low_confidence(self):
        """Confirms the tool registry is never touched when confidence gate fires."""
        from tickets.services.agent_graph import run_agent

        with (
            patch(
                "tickets.services.agent_graph._call_groq",
                return_value=_groq_low_confidence_response(),
            ),
            patch("tickets.services.shopify_service.get_order_context") as mock_shopify,
        ):
            run_agent(query="Where is my order?", order_id="5001")

        mock_shopify.assert_not_called()


# ── Test: max iterations escalation ───────────────────────────────────────────

class TestMaxIterationsEscalation:
    def test_escalates_after_max_iterations(self):
        """Agent must escalate after MAX_ITERATIONS (3) tool calls with no DONE."""
        from tickets.services.agent_graph import run_agent, MAX_ITERATIONS

        call_count = [0]

        def always_tool_call(messages):
            call_count[0] += 1
            return _groq_tool_call_response(call_count[0])

        with (
            patch(
                "tickets.services.agent_graph._call_groq",
                side_effect=always_tool_call,
            ),
            patch(
                "tickets.services.shopify_service.get_order_context",
                return_value={
                    "order_id": "5001",
                    "status": "paid",
                    "shipping_status": "fulfilled",
                    "created_at": "2026-04-01T10:00:00+00:00",
                    "refund_eligible": True,
                },
            ),
        ):
            result = run_agent(query="Where is my order?", order_id="5001")

        assert set(result.keys()) == EXPECTED_RESULT_KEYS
        assert result["final_action"] == "escalated"
        assert result["iterations"] == MAX_ITERATIONS
        assert "maximum iterations" in result["escalation_reason"].lower()
        # Exactly MAX_ITERATIONS tool calls should have been recorded.
        assert len(result["tools_trace"]) == MAX_ITERATIONS

    def test_iterations_field_equals_max(self):
        """iterations field in the result equals MAX_ITERATIONS exactly."""
        from tickets.services.agent_graph import run_agent, MAX_ITERATIONS

        with (
            patch(
                "tickets.services.agent_graph._call_groq",
                return_value=_groq_tool_call_response(1),
            ),
            patch(
                "tickets.services.shopify_service.get_order_context",
                return_value={
                    "order_id": "5001",
                    "status": "paid",
                    "shipping_status": "fulfilled",
                    "created_at": "2026-04-01T10:00:00+00:00",
                    "refund_eligible": True,
                },
            ),
        ):
            result = run_agent(query="Order status?", order_id="5001")

        assert result["iterations"] == MAX_ITERATIONS


# ── Test: successful resolution ────────────────────────────────────────────────

class TestSuccessfulResolution:
    def test_resolves_on_done_signal(self):
        """Agent resolves when LLM immediately returns DONE with high confidence."""
        from tickets.services.agent_graph import run_agent

        with patch(
            "tickets.services.agent_graph._call_groq",
            return_value=_groq_done_response(),
        ):
            result = run_agent(query="Where is my order?", order_id="5001")

        assert set(result.keys()) == EXPECTED_RESULT_KEYS
        assert result["final_action"] == "resolved"
        assert result["resolution"] != ""
        assert result["confidence_score"] == pytest.approx(0.92, abs=0.01)
        assert result["escalation_reason"] == ""
        assert result["tools_trace"] == []   # DONE with no tools called
        assert result["iterations"] == 0

    def test_resolves_after_tool_call_and_done(self):
        """Agent resolves after calling one tool and then returning DONE."""
        from tickets.services.agent_graph import run_agent

        call_count = [0]

        with (
            patch(
                "tickets.services.agent_graph._call_groq",
                side_effect=lambda msgs: _groq_faq_then_done(call_count),
            ),
            patch(
                "tickets.services.faq_service.get_faq_answer",
                return_value={
                    "answer": "Refunds are available within 30 days.",
                    "score": 0.88,
                },
            ),
        ):
            result = run_agent(query="How do I request a refund?", order_id="")

        assert result["final_action"] == "resolved"
        assert len(result["tools_trace"]) == 1
        assert result["tools_trace"][0]["tool"] == "get_faq_answer"
        assert result["iterations"] == 1
        assert result["confidence_score"] == pytest.approx(0.9, abs=0.01)

    def test_result_shape_is_exact(self):
        """AgentResult has exactly the documented keys — no more, no less."""
        from tickets.services.agent_graph import run_agent

        with patch(
            "tickets.services.agent_graph._call_groq",
            return_value=_groq_done_response(),
        ):
            result = run_agent(query="test", order_id="")

        assert set(result.keys()) == EXPECTED_RESULT_KEYS

    def test_escalation_reason_empty_on_resolution(self):
        from tickets.services.agent_graph import run_agent

        with patch(
            "tickets.services.agent_graph._call_groq",
            return_value=_groq_done_response(),
        ):
            result = run_agent(query="test", order_id="")

        assert result["escalation_reason"] == ""

    def test_resolution_empty_on_escalation(self):
        from tickets.services.agent_graph import run_agent

        with patch(
            "tickets.services.agent_graph._call_groq",
            return_value=_groq_low_confidence_response(),
        ):
            result = run_agent(query="test", order_id="")

        assert result["resolution"] == ""
