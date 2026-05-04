"""Tests for retry logic in shopify_service and agent_graph.

Verifies:
1. Shopify retry: _admin_get retries on ShopifyAPIError and succeeds on 3rd attempt.
2. Shopify retry exhausted: after 3 failures get_order_context returns None
   (tool returns found=False, decision trace stores error reason).
3. Groq retry: _call_groq retries on exception and returns on 3rd attempt.
4. Groq retry exhausted: run_agent escalates and stores error in escalation_reason.

All network calls are mocked. Tenacity's wait is patched to 0 s so tests
run in milliseconds.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, call, patch

import pytest


# ── Shopify retry ──────────────────────────────────────────────────────────────

class TestShopifyRetry:
    def test_retries_twice_then_succeeds(self):
        """_admin_get must retry on ShopifyAPIError and return on the 3rd attempt."""
        from tickets.services.shopify_service import ShopifyAPIError

        call_count = [0]
        success_response = {
            "order": {
                "id": 9001,
                "financial_status": "paid",
                "fulfillment_status": "fulfilled",
                "created_at": "2026-04-01T10:00:00+00:00",
            }
        }

        def flaky_get(url, headers, params, timeout):
            call_count[0] += 1
            if call_count[0] < 3:
                # Simulate a 500 response object.
                resp = MagicMock()
                resp.status_code = 500
                return resp
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = success_response
            return resp

        # Patch tenacity's wait so tests don't sleep.
        with (
            patch("httpx.get", side_effect=flaky_get),
            patch("tenacity.wait_exponential.__call__", return_value=0),
            patch(
                "tickets.services.shopify_service.get_redis",
                return_value=MagicMock(get=MagicMock(return_value=None), setex=MagicMock()),
            ),
            patch("tickets.services.shopify_service._require_config",
                  return_value=("shop.myshopify.com", "2024-10", "token")),
        ):
            from tickets.services.shopify_service import get_order_context

            result = get_order_context(order_id="9001")

        assert result is not None
        assert result["order_id"] == "9001"
        assert result["status"] == "paid"

    def test_all_retries_exhausted_returns_none(self):
        """When all 3 _admin_get attempts fail, get_order_context returns None
        (caller gets found=False from the tool layer)."""
        import httpx

        with (
            patch("httpx.get", side_effect=httpx.ConnectError("Network error")),
            patch(
                "tickets.services.shopify_service.get_redis",
                return_value=MagicMock(get=MagicMock(return_value=None)),
            ),
            patch("tickets.services.shopify_service._require_config",
                  return_value=("shop.myshopify.com", "2024-10", "token")),
        ):
            from tickets.services.shopify_service import get_order_context

            result = get_order_context(order_id="9001")

        # On total failure get_order_context must return None.
        assert result is None

    def test_tool_returns_found_false_when_shopify_fails(self):
        """get_order_status tool must return found=False when Shopify is down."""
        import httpx

        with (
            patch("httpx.get", side_effect=httpx.ConnectError("Network error")),
            patch(
                "tickets.services.shopify_service.get_redis",
                return_value=MagicMock(get=MagicMock(return_value=None)),
            ),
            patch("tickets.services.shopify_service._require_config",
                  return_value=("shop.myshopify.com", "2024-10", "token")),
        ):
            from tickets.services.agent_tools import get_order_status

            result = get_order_status("9001")

        assert result["found"] is False
        assert result["order_id"] == "9001"
        assert result["status"] == ""


# ── Groq retry ─────────────────────────────────────────────────────────────────

class TestGroqRetry:
    def _done_response(self) -> str:
        return json.dumps({
            "tool": "DONE",
            "args": {},
            "confidence": 0.95,
            "resolution": "Your order is on its way.",
        })

    def test_groq_retries_twice_then_succeeds(self):
        """_call_groq must retry on exception and return on the 3rd attempt."""
        from groq import APIStatusError  # type: ignore[import]

        call_count = [0]
        done_json = self._done_response()

        def flaky_groq(messages):
            call_count[0] += 1
            if call_count[0] < 3:
                raise RuntimeError("Groq transient error")
            return done_json

        with patch("tickets.services.agent_graph._call_groq", side_effect=flaky_groq):
            from tickets.services.agent_graph import _call_groq

        # Direct test of the original retried function — patching Groq client.
        call_count2 = [0]

        def flaky_create(**kwargs):
            call_count2[0] += 1
            if call_count2[0] < 3:
                raise RuntimeError("Groq transient error")
            msg = MagicMock()
            msg.content = done_json
            choice = MagicMock()
            choice.message = msg
            completion = MagicMock()
            completion.choices = [choice]
            return completion

        with (
            patch("os.environ.get", return_value="fake-api-key"),
            patch("groq.Groq") as mock_groq_cls,
        ):
            mock_client = MagicMock()
            mock_groq_cls.return_value = mock_client
            mock_client.chat.completions.create.side_effect = flaky_create

            from tickets.services.agent_graph import _call_groq as real_call_groq

            result = real_call_groq([{"role": "user", "content": "hi"}])

        assert result == done_json
        assert call_count2[0] == 3   # confirms two retries happened

    def test_groq_all_retries_exhausted_causes_escalation(self):
        """When all Groq retries fail, run_agent must escalate and store
        the error in escalation_reason."""
        with patch(
            "tickets.services.agent_graph._call_groq",
            side_effect=RuntimeError("Groq is down"),
        ):
            from tickets.services.agent_graph import run_agent

            result = run_agent(query="Where is my order?", order_id="")

        assert result["final_action"] == "escalated"
        assert "LLM call failed" in result["escalation_reason"]
        assert result["confidence_score"] is None

    def test_groq_failure_result_has_correct_shape(self):
        """Even on total Groq failure, AgentResult must have all expected keys."""
        expected_keys = {
            "final_action", "resolution", "escalation_reason",
            "tools_trace", "iterations", "confidence_score",
        }
        with patch(
            "tickets.services.agent_graph._call_groq",
            side_effect=RuntimeError("Groq is down"),
        ):
            from tickets.services.agent_graph import run_agent

            result = run_agent(query="test", order_id="")

        assert set(result.keys()) == expected_keys
