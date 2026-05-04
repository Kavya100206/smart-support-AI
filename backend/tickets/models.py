from django.db import models


class Ticket(models.Model):
    CATEGORY_CHOICES = [
        ("billing", "Billing"),
        ("technical", "Technical"),
        ("account", "Account"),
        ("general", "General"),
    ]

    PRIORITY_CHOICES = [
        ("low", "Low"),
        ("medium", "Medium"),
        ("high", "High"),
        ("critical", "Critical"),
    ]

    STATUS_CHOICES = [
        ("open", "Open"),
        ("in_progress", "In Progress"),
        ("resolved", "Resolved"),
        ("closed", "Closed"),
    ]

    RESOLVED_BY_CHOICES = [
        ("agent", "Agent"),
        ("human", "Human"),
    ]

    title = models.CharField(max_length=200)
    description = models.TextField()
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="open")
    created_at = models.DateTimeField(auto_now_add=True)

    shopify_order_id = models.CharField(max_length=64, blank=True, db_index=True)
    resolved_by = models.CharField(
        max_length=10, choices=RESOLVED_BY_CHOICES, blank=True
    )

    # ── Phase 3: metrics fields ────────────────────────────────────────────────
    # True  = agent resolved the ticket autonomously.
    # False = agent escalated to a human.
    # None  = the agent has never been run on this ticket.
    auto_resolved = models.BooleanField(null=True, blank=True)
    # Wall-clock time in milliseconds that the agent run took (inclusive of
    # all Groq + Shopify calls within the LangGraph loop).
    agent_latency_ms = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.priority.upper()}] {self.title}"


class OrderStatusEvent(models.Model):
    """Persisted snapshot of a Shopify order/updated or order/fulfilled webhook.

    We never store the full Shopify payload — only the extracted status fields
    needed to power downstream agent decisions and audit trails.
    """

    TOPIC_CHOICES = [
        ("orders/updated", "Order Updated"),
        ("orders/fulfilled", "Order Fulfilled"),
    ]

    shopify_order_id = models.CharField(max_length=64, db_index=True)
    topic = models.CharField(max_length=32, choices=TOPIC_CHOICES)
    financial_status = models.CharField(max_length=32, blank=True)
    fulfillment_status = models.CharField(max_length=32, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at"]
        indexes = [
            models.Index(fields=["shopify_order_id", "-received_at"]),
        ]

    def __str__(self):
        return f"{self.topic} {self.shopify_order_id} @ {self.received_at:%Y-%m-%d %H:%M}"


# ─── Phase 2: FAQ Knowledge Base ──────────────────────────────────────────────

class FAQEntry(models.Model):
    """A single FAQ question-answer pair with its pre-computed embedding.

    Embeddings are stored as a JSON list of floats (all-MiniLM-L6-v2 produces
    384-dimensional vectors). The dataset is intentionally small (10–15 rows)
    so a full cosine-similarity scan in Python is fast enough without a vector
    index.

    The embedding field is populated by the seed_faqs management command and
    re-used at runtime via the module-level cache in faq_service.py. It is
    never re-computed per request.
    """

    question = models.TextField(unique=True)
    answer = models.TextField()
    # 384 floats from all-MiniLM-L6-v2, stored as a JSON array.
    # null=True so rows can be inserted before embedding is computed.
    embedding = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "FAQ Entry"
        verbose_name_plural = "FAQ Entries"

    def __str__(self):
        return self.question[:80]


# ─── Phase 2: Agent Decision Trace ────────────────────────────────────────────

class AgentDecisionTrace(models.Model):
    """Audit record for a single LangGraph agent run on a ticket.

    One row is written per agent invocation (i.e. per call to
    /api/tickets/<pk>/resolve/). If the same ticket is resolved multiple times
    (e.g. customer follows up), multiple rows will exist for that ticket.

    tools_called — ordered list of tool invocations, each entry is:
        {
            "tool":   "<tool_name>",
            "args":   { ... },    # args passed to the tool
            "result": { ... }     # raw dict returned by the tool
        }

    final_action — either "resolved" (agent produced a resolution) or
        "escalated" (agent hit max iterations, low confidence, or an error).

    confidence_score — the confidence float (0.0–1.0) returned by the LLM on
        its final decision step. null if the agent escalated before any LLM
        confidence was produced (e.g. on a Groq API failure).
    """

    FINAL_ACTION_CHOICES = [
        ("resolved", "Resolved"),
        ("escalated", "Escalated"),
    ]

    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        related_name="decision_traces",
    )
    # Ordered list of {"tool": str, "args": dict, "result": dict}
    tools_called = models.JSONField(default=list)
    final_action = models.CharField(
        max_length=10, choices=FINAL_ACTION_CHOICES
    )
    escalation_reason = models.TextField(blank=True)
    resolution_text = models.TextField(blank=True)
    # LLM-reported confidence on the final decision step (0.0–1.0).
    # null when the agent escalated before an LLM response was produced.
    confidence_score = models.FloatField(null=True, blank=True)
    iterations = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["ticket", "-created_at"]),
        ]

    def __str__(self):
        return (
            f"Trace #{self.pk} — ticket {self.ticket_id} "
            f"[{self.final_action}] @ {self.created_at:%Y-%m-%d %H:%M}"
        )
