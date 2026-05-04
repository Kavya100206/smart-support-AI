from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0002_ticket_shopify_fields_and_order_status_event"),
    ]

    operations = [
        # ── FAQEntry ────────────────────────────────────────────────────────────
        migrations.CreateModel(
            name="FAQEntry",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("question", models.TextField(unique=True)),
                ("answer", models.TextField()),
                # 384-float JSON array from all-MiniLM-L6-v2; null until seeded.
                ("embedding", models.JSONField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "verbose_name": "FAQ Entry",
                "verbose_name_plural": "FAQ Entries",
                "ordering": ["id"],
            },
        ),
        # ── AgentDecisionTrace ──────────────────────────────────────────────────
        migrations.CreateModel(
            name="AgentDecisionTrace",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "ticket",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="decision_traces",
                        to="tickets.ticket",
                    ),
                ),
                # Ordered list of {"tool": str, "args": dict, "result": dict}
                ("tools_called", models.JSONField(default=list)),
                (
                    "final_action",
                    models.CharField(
                        choices=[
                            ("resolved", "Resolved"),
                            ("escalated", "Escalated"),
                        ],
                        max_length=10,
                    ),
                ),
                ("escalation_reason", models.TextField(blank=True)),
                ("resolution_text", models.TextField(blank=True)),
                # LLM-reported confidence (0.0–1.0); null on pre-LLM escalations.
                ("confidence_score", models.FloatField(blank=True, null=True)),
                ("iterations", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="agentdecisiontrace",
            index=models.Index(
                fields=["ticket", "-created_at"],
                name="tickets_agt_ticket_id_created_idx",
            ),
        ),
    ]
