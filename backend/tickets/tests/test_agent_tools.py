"""Tests for tickets/services/agent_tools.py.

All external dependencies (Shopify API, FAQ service) are mocked.
Each test asserts the output shape is EXACTLY correct — no extra keys,
no missing keys, correct types.

Mock path note
--------------
agent_tools.py imports its dependencies LAZILY inside each function:
    from tickets.services.shopify_service import get_order_context
    from tickets.services.faq_service import get_faq_answer as _faq

So the correct patch targets are the SOURCE modules, NOT agent_tools:
    tickets.services.shopify_service.get_order_context   ✓
    tickets.services.faq_service.get_faq_answer          ✓
    tickets.services.agent_tools.get_order_context       ✗  (doesn't exist at module level)
    tickets.services.agent_tools._faq                    ✗  (doesn't exist at module level)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ── Helpers ────────────────────────────────────────────────────────────────────

EXPECTED_ORDER_STATUS_KEYS = {"order_id", "status", "shipping_status", "found"}
EXPECTED_REFUND_KEYS = {"order_id", "refund_eligible", "found"}
EXPECTED_FAQ_KEYS = {"answer", "score"}
EXPECTED_ESCALATE_KEYS = {"escalated", "reason"}


def _shopify_context_found():
    return {
        "order_id": "5001",
        "status": "paid",
        "shipping_status": "fulfilled",
        "created_at": "2026-04-01T10:00:00+00:00",
        "refund_eligible": True,
    }


# ── get_order_status ───────────────────────────────────────────────────────────

class TestGetOrderStatus:
    def test_output_shape_on_found_order(self):
        """Shape is exact when Shopify returns a context dict."""
        from tickets.services.agent_tools import get_order_status

        with patch(
            "tickets.services.shopify_service.get_order_context",
            return_value=_shopify_context_found(),
        ):
            result = get_order_status("5001")

        assert set(result.keys()) == EXPECTED_ORDER_STATUS_KEYS
        assert result["order_id"] == "5001"
        assert result["status"] == "paid"
        assert result["shipping_status"] == "fulfilled"
        assert result["found"] is True

    def test_output_shape_on_not_found(self):
        """Shape is exact when Shopify returns None (order doesn't exist)."""
        from tickets.services.agent_tools import get_order_status

        with patch(
            "tickets.services.shopify_service.get_order_context",
            return_value=None,
        ):
            result = get_order_status("9999")

        assert set(result.keys()) == EXPECTED_ORDER_STATUS_KEYS
        assert result["found"] is False
        assert result["status"] == ""
        assert result["shipping_status"] == ""

    def test_output_shape_on_shopify_exception(self):
        """Shape is exact when the Shopify call raises an exception."""
        from tickets.services.agent_tools import get_order_status

        with patch(
            "tickets.services.shopify_service.get_order_context",
            side_effect=RuntimeError("Shopify down"),
        ):
            result = get_order_status("5001")

        assert set(result.keys()) == EXPECTED_ORDER_STATUS_KEYS
        assert result["found"] is False

    def test_empty_order_id_returns_not_found(self):
        """Empty order_id short-circuits without calling Shopify."""
        from tickets.services.agent_tools import get_order_status

        with patch("tickets.services.shopify_service.get_order_context") as mock_shopify:
            result = get_order_status("")

        mock_shopify.assert_not_called()
        assert set(result.keys()) == EXPECTED_ORDER_STATUS_KEYS
        assert result["found"] is False


# ── check_refund_eligibility ───────────────────────────────────────────────────

class TestCheckRefundEligibility:
    def test_output_shape_eligible(self):
        """Shape is exact and refund_eligible=True when Shopify says so."""
        from tickets.services.agent_tools import check_refund_eligibility

        with patch(
            "tickets.services.shopify_service.get_order_context",
            return_value=_shopify_context_found(),
        ):
            result = check_refund_eligibility("5001")

        assert set(result.keys()) == EXPECTED_REFUND_KEYS
        assert result["order_id"] == "5001"
        assert result["refund_eligible"] is True
        assert result["found"] is True

    def test_output_shape_not_eligible(self):
        """refund_eligible=False when the order is outside the refund window."""
        from tickets.services.agent_tools import check_refund_eligibility

        ctx = {**_shopify_context_found(), "refund_eligible": False}
        with patch(
            "tickets.services.shopify_service.get_order_context",
            return_value=ctx,
        ):
            result = check_refund_eligibility("5001")

        assert set(result.keys()) == EXPECTED_REFUND_KEYS
        assert result["refund_eligible"] is False
        assert result["found"] is True

    def test_output_shape_on_not_found(self):
        from tickets.services.agent_tools import check_refund_eligibility

        with patch(
            "tickets.services.shopify_service.get_order_context",
            return_value=None,
        ):
            result = check_refund_eligibility("9999")

        assert set(result.keys()) == EXPECTED_REFUND_KEYS
        assert result["found"] is False
        assert result["refund_eligible"] is False

    def test_empty_order_id_short_circuits(self):
        from tickets.services.agent_tools import check_refund_eligibility

        with patch("tickets.services.shopify_service.get_order_context") as mock_shopify:
            result = check_refund_eligibility("")

        mock_shopify.assert_not_called()
        assert set(result.keys()) == EXPECTED_REFUND_KEYS
        assert result["found"] is False


# ── get_faq_answer ─────────────────────────────────────────────────────────────

class TestGetFaqAnswer:
    def test_output_shape_match_found(self):
        """Shape is exact when a FAQ match is above threshold."""
        from tickets.services.agent_tools import get_faq_answer

        # Patch the source function in faq_service (where it's lazily imported from).
        with patch(
            "tickets.services.faq_service.get_faq_answer",
            return_value={"answer": "You can track via email.", "score": 0.85},
        ):
            result = get_faq_answer("Where is my order?")

        assert set(result.keys()) == EXPECTED_FAQ_KEYS
        assert isinstance(result["answer"], str)
        assert isinstance(result["score"], float)
        assert 0.0 <= result["score"] <= 1.0

    def test_output_shape_no_match(self):
        """Shape is exact and answer=None when below threshold."""
        from tickets.services.agent_tools import get_faq_answer

        with patch(
            "tickets.services.faq_service.get_faq_answer",
            return_value={"answer": None, "score": 0.3},
        ):
            result = get_faq_answer("gibberish query xyz")

        assert set(result.keys()) == EXPECTED_FAQ_KEYS
        assert result["answer"] is None
        assert result["score"] == 0.3

    def test_empty_query_returns_none(self):
        """Empty query short-circuits without calling the service."""
        from tickets.services.agent_tools import get_faq_answer

        with patch("tickets.services.faq_service.get_faq_answer") as mock_faq:
            result = get_faq_answer("")

        mock_faq.assert_not_called()
        assert set(result.keys()) == EXPECTED_FAQ_KEYS
        assert result["answer"] is None
        assert result["score"] == 0.0


# ── escalate_to_human ──────────────────────────────────────────────────────────

class TestEscalateToHuman:
    def test_output_shape(self):
        """Shape is exact: escalated=True and reason echoed back."""
        from tickets.services.agent_tools import escalate_to_human

        result = escalate_to_human("Cannot find matching FAQ or order.")

        assert set(result.keys()) == EXPECTED_ESCALATE_KEYS
        assert result["escalated"] is True
        assert result["reason"] == "Cannot find matching FAQ or order."

    def test_empty_reason_has_default(self):
        """Empty reason is replaced with a default string."""
        from tickets.services.agent_tools import escalate_to_human

        result = escalate_to_human("")

        assert set(result.keys()) == EXPECTED_ESCALATE_KEYS
        assert result["escalated"] is True
        assert len(result["reason"]) > 0  # never blank

    def test_escalated_is_always_true(self):
        from tickets.services.agent_tools import escalate_to_human

        for reason in ["", "some reason", "   "]:
            result = escalate_to_human(reason)
            assert result["escalated"] is True
