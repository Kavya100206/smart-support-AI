"""Shopify webhook listener.

Verifies the HMAC-SHA256 signature, extracts only the four fields we care
about from the payload, persists an ``OrderStatusEvent`` row, and discards
the full webhook payload. The raw JSON body never reaches the database.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import OrderStatusEvent


SUPPORTED_TOPICS = frozenset({"orders/updated", "orders/fulfilled"})


def _verify_hmac(raw_body: bytes, header_hmac: str, secret: str) -> bool:
    """Constant-time HMAC-SHA256 verification per Shopify's spec."""
    if not (secret and header_hmac and raw_body):
        return False
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, header_hmac)


@method_decorator(csrf_exempt, name="dispatch")
class ShopifyWebhookView(APIView):
    """POST /api/shopify/webhook/ — receives orders/updated and orders/fulfilled."""

    authentication_classes: list = []
    permission_classes: list = []

    def post(self, request):
        secret = settings.SHOPIFY_WEBHOOK_SECRET
        topic = request.headers.get("X-Shopify-Topic", "")
        sent_hmac = request.headers.get("X-Shopify-Hmac-Sha256", "")
        raw_body = request.body

        if not _verify_hmac(raw_body, sent_hmac, secret):
            return Response(
                {"error": "invalid signature"}, status=status.HTTP_401_UNAUTHORIZED
            )

        # Acknowledge unsupported topics with 200 so Shopify doesn't retry; we
        # just don't persist anything for them.
        if topic not in SUPPORTED_TOPICS:
            return Response({"ignored": topic}, status=status.HTTP_200_OK)

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return Response(
                {"error": "invalid json"}, status=status.HTTP_400_BAD_REQUEST
            )

        order_id = payload.get("id")
        if not order_id:
            return Response(
                {"error": "missing order id"}, status=status.HTTP_400_BAD_REQUEST
            )

        # Extract only the fields we need; the full payload is dropped here.
        OrderStatusEvent.objects.create(
            shopify_order_id=str(order_id),
            topic=topic,
            financial_status=payload.get("financial_status") or "",
            fulfillment_status=payload.get("fulfillment_status") or "",
        )
        # `payload` and `raw_body` go out of scope when this method returns.
        return Response({"ok": True}, status=status.HTTP_200_OK)
