from django.urls import path

from .views import (
    TicketListCreateView,
    TicketDetailView,
    StatsView,
    AgentResolveView,
    MetricsView,        # Phase 3
    TicketTraceView,    # Phase 3
)
from .classify import ClassifyView
from .webhooks import ShopifyWebhookView

urlpatterns = [
    path("tickets/", TicketListCreateView.as_view()),
    path("tickets/stats/", StatsView.as_view()),
    # Phase 3: /metrics/ must come before <int:pk>/ so Django does not try to
    # parse the literal string "metrics" as a primary key integer.
    path("tickets/metrics/", MetricsView.as_view()),              # Phase 3
    path("tickets/<int:pk>/", TicketDetailView.as_view()),
    path("tickets/<int:pk>/resolve/", AgentResolveView.as_view()),
    path("tickets/<int:pk>/trace/", TicketTraceView.as_view()),   # Phase 3
    path("tickets/classify/", ClassifyView.as_view()),
    path("shopify/webhook/", ShopifyWebhookView.as_view()),
]
