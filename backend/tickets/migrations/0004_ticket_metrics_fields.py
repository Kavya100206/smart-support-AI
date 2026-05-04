from django.db import migrations, models


class Migration(migrations.Migration):
    """Add auto_resolved and agent_latency_ms to the Ticket model.

    These fields store per-ticket agent metrics directly on the ticket row,
    avoiding a join to AgentDecisionTrace when computing aggregate rates.

    auto_resolved:
        True  — agent resolved the ticket.
        False — agent escalated.
        None  — agent has never run on this ticket.

    agent_latency_ms:
        Wall-clock milliseconds of the agent run (all LLM + Shopify calls
        combined). Null when agent has never run.
    """

    dependencies = [
        ("tickets", "0003_faqentry_agentdecisiontrace"),
    ]

    operations = [
        migrations.AddField(
            model_name="ticket",
            name="auto_resolved",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="ticket",
            name="agent_latency_ms",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
