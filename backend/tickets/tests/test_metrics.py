"""Tests for Phase 3 metrics and audit trace endpoints.

Covers:
1. GET /api/tickets/metrics/ — correct aggregate counts and rates.
2. GET /api/tickets/<pk>/trace/ — correct trace list shape and ordering.
3. Edge cases: no agent runs yet (rates = 0.0), unknown ticket 404.

Uses Django's test client so views + URL routing are exercised end-to-end.
No real Redis or Groq calls are made.
"""
from __future__ import annotations

import pytest
from django.test import TestCase, Client
from django.urls import reverse

from tickets.models import Ticket, AgentDecisionTrace


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_ticket(**kwargs) -> Ticket:
    defaults = {
        "title": "Test ticket",
        "description": "Something went wrong.",
        "category": "general",
        "priority": "low",
        "status": "open",
    }
    defaults.update(kwargs)
    return Ticket.objects.create(**defaults)


def _make_trace(ticket: Ticket, final_action: str = "resolved", **kwargs) -> AgentDecisionTrace:
    defaults = {
        "tools_called": [],
        "final_action": final_action,
        "escalation_reason": "",
        "resolution_text": "All good.",
        "confidence_score": 0.9,
        "iterations": 1,
    }
    defaults.update(kwargs)
    return AgentDecisionTrace.objects.create(ticket=ticket, **defaults)


# ── Metrics endpoint ───────────────────────────────────────────────────────────

class TestMetricsView(TestCase):
    def setUp(self):
        self.client = Client()

    def test_returns_zeros_when_no_agent_runs(self):
        """When no AgentDecisionTrace rows exist all metrics must be 0/0.0."""
        response = self.client.get("/api/tickets/metrics/")

        assert response.status_code == 200
        data = response.json()

        assert data["total_agent_runs"] == 0
        assert data["total_resolved"] == 0
        assert data["total_escalated"] == 0
        assert data["resolution_rate"] == 0.0
        assert data["escalation_rate"] == 0.0
        assert data["avg_latency_ms"] == 0.0

    def test_correct_counts_with_mixed_traces(self):
        """Resolution rate and escalation rate computed correctly."""
        t1 = _make_ticket(agent_latency_ms=200)
        t2 = _make_ticket(agent_latency_ms=400)
        t3 = _make_ticket()   # no latency (agent never ran directly but trace exists)

        _make_trace(t1, final_action="resolved")
        _make_trace(t2, final_action="resolved")
        _make_trace(t3, final_action="escalated")

        response = self.client.get("/api/tickets/metrics/")
        assert response.status_code == 200
        data = response.json()

        assert data["total_agent_runs"] == 3
        assert data["total_resolved"] == 2
        assert data["total_escalated"] == 1
        assert abs(data["resolution_rate"] - round(2 / 3, 4)) < 0.001
        assert abs(data["escalation_rate"] - round(1 / 3, 4)) < 0.001

    def test_avg_latency_computed_from_ticket_fields(self):
        """avg_latency_ms is the mean of Ticket.agent_latency_ms (non-null only)."""
        t1 = _make_ticket(agent_latency_ms=100)
        t2 = _make_ticket(agent_latency_ms=300)
        _make_trace(t1)
        _make_trace(t2)

        response = self.client.get("/api/tickets/metrics/")
        data = response.json()

        assert abs(data["avg_latency_ms"] - 200.0) < 1.0

    def test_response_has_exact_keys(self):
        """The metrics response must have exactly the documented keys."""
        expected_keys = {
            "total_agent_runs",
            "total_resolved",
            "total_escalated",
            "resolution_rate",
            "escalation_rate",
            "avg_latency_ms",
        }
        response = self.client.get("/api/tickets/metrics/")
        assert set(response.json().keys()) == expected_keys

    def test_only_get_allowed(self):
        """POST to /metrics/ must return 405 Method Not Allowed."""
        response = self.client.post("/api/tickets/metrics/", {}, content_type="application/json")
        assert response.status_code == 405


# ── Trace endpoint ─────────────────────────────────────────────────────────────

class TestTicketTraceView(TestCase):
    def setUp(self):
        self.client = Client()

    def test_returns_404_for_unknown_ticket(self):
        response = self.client.get("/api/tickets/99999/trace/")
        assert response.status_code == 404

    def test_returns_empty_trace_list_for_ticket_without_runs(self):
        ticket = _make_ticket()
        response = self.client.get(f"/api/tickets/{ticket.pk}/trace/")

        assert response.status_code == 200
        data = response.json()
        assert data["ticket_id"] == ticket.pk
        assert data["traces"] == []

    def test_returns_all_traces_for_ticket(self):
        ticket = _make_ticket()
        t1 = _make_trace(ticket, final_action="resolved", confidence_score=0.9)
        t2 = _make_trace(ticket, final_action="escalated", escalation_reason="Too complex")

        response = self.client.get(f"/api/tickets/{ticket.pk}/trace/")
        assert response.status_code == 200
        data = response.json()

        assert data["ticket_id"] == ticket.pk
        assert len(data["traces"]) == 2

    def test_trace_item_has_exact_keys(self):
        """Each trace item must contain exactly the documented keys."""
        expected_keys = {
            "trace_id",
            "final_action",
            "confidence_score",
            "iterations",
            "tools_called",
            "resolution_text",
            "escalation_reason",
            "created_at",
        }
        ticket = _make_ticket()
        _make_trace(ticket)

        response = self.client.get(f"/api/tickets/{ticket.pk}/trace/")
        trace_item = response.json()["traces"][0]

        assert set(trace_item.keys()) == expected_keys

    def test_traces_ordered_most_recent_first(self):
        """Traces must be returned in descending created_at order."""
        import time as _time

        ticket = _make_ticket()
        t1 = _make_trace(ticket, final_action="resolved")
        _time.sleep(0.01)   # ensure different timestamps
        t2 = _make_trace(ticket, final_action="escalated")

        response = self.client.get(f"/api/tickets/{ticket.pk}/trace/")
        traces = response.json()["traces"]

        # Most-recently created trace should come first.
        assert traces[0]["trace_id"] == t2.pk
        assert traces[1]["trace_id"] == t1.pk

    def test_trace_values_match_db_row(self):
        """Trace field values must exactly match what was written to the DB."""
        ticket = _make_ticket()
        trace = _make_trace(
            ticket,
            final_action="escalated",
            escalation_reason="Too many retries",
            confidence_score=0.55,
            iterations=3,
            tools_called=[{"tool": "get_order_status", "args": {}, "result": {}}],
        )

        response = self.client.get(f"/api/tickets/{ticket.pk}/trace/")
        item = response.json()["traces"][0]

        assert item["trace_id"] == trace.pk
        assert item["final_action"] == "escalated"
        assert item["escalation_reason"] == "Too many retries"
        assert abs(item["confidence_score"] - 0.55) < 0.001
        assert item["iterations"] == 3
        assert len(item["tools_called"]) == 1
        assert item["tools_called"][0]["tool"] == "get_order_status"

    def test_response_top_level_has_exact_keys(self):
        """Top-level response must contain exactly ticket_id and traces."""
        ticket = _make_ticket()
        response = self.client.get(f"/api/tickets/{ticket.pk}/trace/")
        assert set(response.json().keys()) == {"ticket_id", "traces"}
