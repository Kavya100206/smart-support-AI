from django.urls import path
from .views import TicketListCreateView, TicketDetailView, StatsView
from .classify import ClassifyView

urlpatterns = [
    path("tickets/", TicketListCreateView.as_view()),
    path("tickets/stats/", StatsView.as_view()),
    path("tickets/<int:pk>/", TicketDetailView.as_view()),
    path("tickets/classify/", ClassifyView.as_view()),
]
