import time

from django.db.models import Count, Avg
from django.db.models.functions import TruncDate
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Ticket, AgentDecisionTrace
from .serializers import TicketSerializer

# ── Rate limit helper ──────────────────────────────────────────────────────────

_RATE_429 = Response(
    {"error": "Rate limit exceeded", "retry_after": 60},
    status=status.HTTP_429_TOO_MANY_REQUESTS,
)


def _is_limited(request) -> bool:
    """Return True when the IP has exceeded 20 POST requests per minute.

    django-ratelimit sets the ``limited`` flag on the underlying Django
    HttpRequest (``request._request`` inside a DRF view). We check that
    attribute defensively so this never raises.
    """
    return bool(getattr(request, "limited", False) or getattr(getattr(request, "_request", None), "limited", False))


# ─── Ticket list / create ──────────────────────────────────────────────────────

class TicketListCreateView(APIView):
    """GET  /api/tickets/    — list all tickets with optional filters.
    POST /api/tickets/    — create a new ticket.

    Phase 3: POST is rate-limited to 20 requests / minute per IP.
    """

    def get(self, request):
        queryset = Ticket.objects.all()

        category = request.query_params.get("category")
        priority = request.query_params.get("priority")
        ticket_status = request.query_params.get("status")
        search = request.query_params.get("search")

        if category:
            queryset = queryset.filter(category=category)
        if priority:
            queryset = queryset.filter(priority=priority)
        if ticket_status:
            queryset = queryset.filter(status=ticket_status)
        if search:
            queryset = queryset.filter(title__icontains=search) | queryset.filter(
                description__icontains=search
            )

        serializer = TicketSerializer(queryset, many=True)
        return Response(serializer.data)

    @method_decorator(ratelimit(key="ip", rate="20/m", method="POST", block=False))
    def post(self, request):
        if _is_limited(request):
            return _RATE_429

        serializer = TicketSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# ─── Ticket detail ─────────────────────────────────────────────────────────────

class TicketDetailView(APIView):
    def get_object(self, pk):
        try:
            return Ticket.objects.get(pk=pk)
        except Ticket.DoesNotExist:
            return None

    def patch(self, request, pk):
        ticket = self.get_object(pk)
        if ticket is None:
            return Response(
                {"error": "Ticket not found"}, status=status.HTTP_404_NOT_FOUND
            )
        serializer = TicketSerializer(ticket, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# ─── Stats ─────────────────────────────────────────────────────────────────────

class StatsView(APIView):
    def get(self, request):
        total = Ticket.objects.count()
        open_count = Ticket.objects.filter(status="open").count()

        daily_counts = (
            Ticket.objects.annotate(date=TruncDate("created_at"))
            .values("date")
            .annotate(count=Count("id"))
        )
        avg_per_day = daily_counts.aggregate(avg=Avg("count"))["avg"] or 0

        priority_breakdown = dict(
            Ticket.objects.values_list("priority")
            .annotate(count=Count("id"))
            .values_list("priority", "count")
        )

        category_breakdown = dict(
            Ticket.objects.values_list("category")
            .annotate(count=Count("id"))
            .values_list("category", "count")
        )

        return Response(
            {
                "total_tickets": total,
                "open_tickets": open_count,
                "avg_tickets_per_day": round(avg_per_day, 1),
                "priority_breakdown": priority_breakdown,
                "category_breakdown": category_breakdown,
            }
        )


# ─── Phase 2: Autonomous Resolution Agent ─────────────────────────────────────

class AgentResolveView(APIView):
    """POST /api/tickets/<pk>/resolve/

    Runs the LangGraph autonomous resolution agent against the ticket.
    A fresh agent graph is instantiated per request and discarded after.

    Phase 3 additions:
    - Writes ``auto_resolved`` and ``agent_latency_ms`` to the Ticket row so
      the /metrics/ endpoint can aggregate without joining AgentDecisionTrace.

    Request body: (empty — all context comes from the ticket record)

    Response 200:
        {
            "ticket_id":         int,
            "final_action":      "resolved" | "escalated",
            "resolution":        str,
            "escalation_reason": str,
            "confidence_score":  float | null,
            "iterations":        int,
            "tools_called":      list[dict],
            "ticket_status":     str,
            "trace_id":          int,
            "agent_latency_ms":  int,
        }

    Response 404: ticket not found.
    Response 400: ticket status is not open or in_progress.
    """

    def post(self, request, pk: int):
        # ── 1. Fetch and validate the ticket ──────────────────────────────────
        try:
            ticket = Ticket.objects.get(pk=pk)
        except Ticket.DoesNotExist:
            return Response(
                {"error": f"Ticket {pk} not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if ticket.status not in ("open", "in_progress"):
            return Response(
                {
                    "error": (
                        f"Ticket {pk} has status '{ticket.status}' and cannot "
                        "be submitted for agent resolution. Only open or "
                        "in_progress tickets are accepted."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── 2. Run the agent (fresh graph, discarded after) ───────────────────
        from tickets.services.agent_graph import run_agent  # noqa: PLC0415

        start_ms = int(time.monotonic() * 1000)

        result = run_agent(
            query=ticket.description,
            order_id=ticket.shopify_order_id or "",
        )

        agent_latency_ms = int(time.monotonic() * 1000) - start_ms

        # ── 3. Update ticket status + Phase 3 metrics fields ──────────────────
        if result["final_action"] == "resolved":
            ticket.status = "resolved"
            ticket.resolved_by = "agent"
            ticket.auto_resolved = True
        else:
            # Escalated — mark as in_progress for human pick-up.
            ticket.status = "in_progress"
            ticket.resolved_by = ""
            ticket.auto_resolved = False

        ticket.agent_latency_ms = agent_latency_ms
        ticket.save(update_fields=["status", "resolved_by", "auto_resolved", "agent_latency_ms"])

        # ── 4. Persist the decision trace ─────────────────────────────────────
        trace = AgentDecisionTrace.objects.create(
            ticket=ticket,
            tools_called=result["tools_trace"],
            final_action=result["final_action"],
            escalation_reason=result["escalation_reason"],
            resolution_text=result["resolution"],
            confidence_score=result["confidence_score"],
            iterations=result["iterations"],
        )

        # ── 5. Return structured response ──────────────────────────────────────
        return Response(
            {
                "ticket_id": ticket.pk,
                "final_action": result["final_action"],
                "resolution": result["resolution"],
                "escalation_reason": result["escalation_reason"],
                "confidence_score": result["confidence_score"],
                "iterations": result["iterations"],
                "tools_called": result["tools_trace"],
                "ticket_status": ticket.status,
                "trace_id": trace.pk,
                "agent_latency_ms": agent_latency_ms,
            },
            status=status.HTTP_200_OK,
        )


# ─── Phase 3: Metrics endpoint ────────────────────────────────────────────────

class MetricsView(APIView):
    """GET /api/tickets/metrics/

    Returns aggregate resolution metrics computed from AgentDecisionTrace rows.

    Response 200:
        {
            "total_agent_runs":  int,
            "total_resolved":    int,
            "total_escalated":   int,
            "resolution_rate":   float,   // resolved / total_agent_runs
            "escalation_rate":   float,   // escalated / total_agent_runs
            "avg_latency_ms":    float,   // average agent_latency_ms across tickets
        }

    Rates are 0.0 when no agent runs exist yet.
    """

    def get(self, request):
        total_runs = AgentDecisionTrace.objects.count()
        total_resolved = AgentDecisionTrace.objects.filter(
            final_action="resolved"
        ).count()
        total_escalated = AgentDecisionTrace.objects.filter(
            final_action="escalated"
        ).count()

        # avg_latency_ms is stored on Ticket, not AgentDecisionTrace, to avoid
        # the join. We average only tickets that have had an agent run.
        avg_latency = (
            Ticket.objects.filter(agent_latency_ms__isnull=False).aggregate(
                avg=Avg("agent_latency_ms")
            )["avg"]
            or 0.0
        )

        resolution_rate = round(total_resolved / total_runs, 4) if total_runs > 0 else 0.0
        escalation_rate = round(total_escalated / total_runs, 4) if total_runs > 0 else 0.0

        return Response(
            {
                "total_agent_runs": total_runs,
                "total_resolved": total_resolved,
                "total_escalated": total_escalated,
                "resolution_rate": resolution_rate,
                "escalation_rate": escalation_rate,
                "avg_latency_ms": round(avg_latency, 1),
            }
        )


# ─── Phase 3: Per-ticket audit trace endpoint ─────────────────────────────────

class TicketTraceView(APIView):
    """GET /api/tickets/<pk>/trace/

    Returns all AgentDecisionTrace rows for ticket *pk*, ordered most-recent
    first. Multiple traces can exist if the same ticket was re-submitted for
    resolution after a customer follow-up.

    Response 200:
        {
            "ticket_id": int,
            "traces": [
                {
                    "trace_id":          int,
                    "final_action":      "resolved" | "escalated",
                    "confidence_score":  float | null,
                    "iterations":        int,
                    "tools_called":      list[dict],
                    "resolution_text":   str,
                    "escalation_reason": str,
                    "created_at":        str  // ISO 8601 UTC
                }
            ]
        }

    Response 404: ticket not found.
    """

    def get(self, request, pk: int):
        try:
            ticket = Ticket.objects.get(pk=pk)
        except Ticket.DoesNotExist:
            return Response(
                {"error": f"Ticket {pk} not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        traces = AgentDecisionTrace.objects.filter(ticket=ticket).order_by("-created_at")

        return Response(
            {
                "ticket_id": ticket.pk,
                "traces": [
                    {
                        "trace_id": t.pk,
                        "final_action": t.final_action,
                        "confidence_score": t.confidence_score,
                        "iterations": t.iterations,
                        "tools_called": t.tools_called,
                        "resolution_text": t.resolution_text,
                        "escalation_reason": t.escalation_reason,
                        "created_at": t.created_at.isoformat(),
                    }
                    for t in traces
                ],
            }
        )
