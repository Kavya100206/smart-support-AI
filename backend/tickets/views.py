from django.db.models import Count, Avg, F
from django.db.models.functions import TruncDate
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Ticket
from .serializers import TicketSerializer


class TicketListCreateView(APIView):
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
            queryset = queryset.filter(title__icontains=search) | queryset.filter(description__icontains=search)

        serializer = TicketSerializer(queryset, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = TicketSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class TicketDetailView(APIView):
    def get_object(self, pk):
        try:
            return Ticket.objects.get(pk=pk)
        except Ticket.DoesNotExist:
            return None

    def patch(self, request, pk):
        ticket = self.get_object(pk)
        if ticket is None:
            return Response({"error": "Ticket not found"}, status=status.HTTP_404_NOT_FOUND)
        serializer = TicketSerializer(ticket, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class StatsView(APIView):
    def get(self, request):
        total = Ticket.objects.count()
        open_count = Ticket.objects.filter(status="open").count()

        daily_counts = (
            Ticket.objects.annotate(date=TruncDate("created_at"))
            .values("date")
            .annotate(count=Count("id"))
        )
        avg_per_day = (
            daily_counts.aggregate(avg=Avg("count"))["avg"] or 0
        )

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

        return Response({
            "total_tickets": total,
            "open_tickets": open_count,
            "avg_tickets_per_day": round(avg_per_day, 1),
            "priority_breakdown": priority_breakdown,
            "category_breakdown": category_breakdown,
        })
