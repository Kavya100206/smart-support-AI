"""Agent tool functions for the autonomous resolution agent.

Each function is a plain Python callable — no LangGraph coupling.
The agent graph in agent_graph.py calls these by name.

Output shapes are FIXED. Callers must not rely on any key not documented
here. Adding keys is a breaking change.

Tool output shapes:
    get_order_status       → {"order_id": str, "status": str,
                              "shipping_status": str, "found": bool}
    check_refund_eligibility → {"order_id": str, "refund_eligible": bool,
                                "found": bool}
    get_faq_answer         → {"answer": str | None, "score": float}
    escalate_to_human      → {"escalated": True, "reason": str}
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def get_order_status(order_id: str) -> dict:
    """Fetch order status fields for *order_id* from Shopify.

    Calls get_order_context from shopify_service; extracts only the fields
    needed for this tool and discards the rest. Returns a fixed-shape dict.

    Args:
        order_id: Shopify order ID string (e.g. "5001234567890").

    Returns:
        {
            "order_id":        str,   # echoed back for agent traceability
            "status":          str,   # financial_status from Shopify
            "shipping_status": str,   # fulfillment_status or "unfulfilled"
            "found":           bool,  # False when no order matches the ID
        }
    """
    if not order_id or not order_id.strip():
        return {
            "order_id": order_id,
            "status": "",
            "shipping_status": "",
            "found": False,
        }

    try:
        from tickets.services.shopify_service import get_order_context  # noqa

        context = get_order_context(order_id=order_id.strip())
    except Exception as exc:
        logger.warning("get_order_status: Shopify call failed for %s: %s", order_id, exc)
        return {
            "order_id": order_id,
            "status": "",
            "shipping_status": "",
            "found": False,
        }

    if context is None:
        return {
            "order_id": order_id,
            "status": "",
            "shipping_status": "",
            "found": False,
        }

    return {
        "order_id": context["order_id"],
        "status": context["status"],
        "shipping_status": context["shipping_status"],
        "found": True,
    }


def check_refund_eligibility(order_id: str) -> dict:
    """Check whether *order_id* is eligible for a refund.

    Calls get_order_context from shopify_service; extracts only the
    refund_eligible field. The full context dict is discarded after extraction.

    Args:
        order_id: Shopify order ID string.

    Returns:
        {
            "order_id":        str,
            "refund_eligible": bool,
            "found":           bool,
        }
    """
    if not order_id or not order_id.strip():
        return {"order_id": order_id, "refund_eligible": False, "found": False}

    try:
        from tickets.services.shopify_service import get_order_context  # noqa

        context = get_order_context(order_id=order_id.strip())
    except Exception as exc:
        logger.warning(
            "check_refund_eligibility: Shopify call failed for %s: %s", order_id, exc
        )
        return {"order_id": order_id, "refund_eligible": False, "found": False}

    if context is None:
        return {"order_id": order_id, "refund_eligible": False, "found": False}

    return {
        "order_id": context["order_id"],
        "refund_eligible": context["refund_eligible"],
        "found": True,
    }


def get_faq_answer(query: str) -> dict:
    """Search the FAQ knowledge base for an answer to *query*.

    Delegates to faq_service.get_faq_answer which performs cosine-similarity
    search over the module-level embedding cache loaded at startup.

    Args:
        query: The customer's question or a reformulated sub-query.

    Returns:
        {
            "answer": str | None,  # best-matching answer, None if below threshold
            "score":  float,       # cosine similarity (0.0–1.0)
        }
    """
    if not query or not query.strip():
        return {"answer": None, "score": 0.0}

    try:
        from tickets.services.faq_service import get_faq_answer as _faq  # noqa

        return _faq(query.strip())
    except Exception as exc:
        logger.warning("get_faq_answer: similarity search failed: %s", exc)
        return {"answer": None, "score": 0.0}


def escalate_to_human(reason: str) -> dict:
    """Signal that the ticket must be escalated to a human agent.

    This is a terminal tool — after calling it the agent stops. The *reason*
    is stored in AgentDecisionTrace.escalation_reason.

    Args:
        reason: A concise, structured explanation of why escalation is needed.

    Returns:
        {
            "escalated": True,
            "reason":    str,
        }
    """
    return {
        "escalated": True,
        "reason": str(reason).strip() if reason else "No reason provided.",
    }


# ── Tool registry ──────────────────────────────────────────────────────────────
# Used by agent_graph.py to dispatch tool calls by name.
TOOL_REGISTRY: dict[str, callable] = {
    "get_order_status": get_order_status,
    "check_refund_eligibility": check_refund_eligibility,
    "get_faq_answer": get_faq_answer,
    "escalate_to_human": escalate_to_human,
}
