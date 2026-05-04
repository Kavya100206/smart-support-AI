"""Tests for the Shopify webhook listener.

Covers HMAC verification, topic dispatch, payload extraction, and the
extract-and-discard guarantee (we assert nothing in the persisted row
holds the full payload).
"""
from __future__ import annotations

import base64
import hashlib
import hmac as hmac_lib
import json

from django.test import Client, TestCase, override_settings

from tickets.models import OrderStatusEvent


WEBHOOK_SECRET = "whsec_test_signing_key"
WEBHOOK_URL = "/api/shopify/webhook/"


def _sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    digest = hmac_lib.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _post(client: Client, payload: dict, *, topic: str, signature: str | None = None):
    body = json.dumps(payload).encode("utf-8")
    sig = signature if signature is not None else _sign(body)
    return client.post(
        WEBHOOK_URL,
        data=body,
        content_type="application/json",
        HTTP_X_SHOPIFY_TOPIC=topic,
        HTTP_X_SHOPIFY_HMAC_SHA256=sig,
    )


@override_settings(SHOPIFY_WEBHOOK_SECRET=WEBHOOK_SECRET)
class ShopifyWebhookTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_orders_updated_persists_extracted_fields(self):
        payload = {
            "id": 12345,
            "financial_status": "paid",
            "fulfillment_status": "partial",
            "email": "noisy@example.com",  # extra field that must NOT be persisted
            "line_items": [{"sku": "ABC", "quantity": 2}],
        }
        response = _post(self.client, payload, topic="orders/updated")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(OrderStatusEvent.objects.count(), 1)
        event = OrderStatusEvent.objects.get()
        self.assertEqual(event.shopify_order_id, "12345")
        self.assertEqual(event.topic, "orders/updated")
        self.assertEqual(event.financial_status, "paid")
        self.assertEqual(event.fulfillment_status, "partial")

        # Extract-and-discard guarantee: schema cannot hold the raw payload.
        persisted_attrs = {f.name for f in OrderStatusEvent._meta.get_fields()}
        self.assertEqual(
            persisted_attrs,
            {
                "id",
                "shopify_order_id",
                "topic",
                "financial_status",
                "fulfillment_status",
                "received_at",
            },
        )

    def test_orders_fulfilled_topic_persists(self):
        payload = {
            "id": 22222,
            "financial_status": "paid",
            "fulfillment_status": "fulfilled",
        }
        response = _post(self.client, payload, topic="orders/fulfilled")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            OrderStatusEvent.objects.filter(topic="orders/fulfilled").count(), 1
        )

    def test_invalid_signature_rejected(self):
        payload = {"id": 1, "financial_status": "paid"}
        response = _post(self.client, payload, topic="orders/updated", signature="invalid")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(OrderStatusEvent.objects.count(), 0)

    def test_unsupported_topic_acknowledged_but_not_persisted(self):
        payload = {"id": 1, "financial_status": "paid"}
        response = _post(self.client, payload, topic="customers/created")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(OrderStatusEvent.objects.count(), 0)

    def test_missing_order_id_rejected(self):
        payload = {"financial_status": "paid"}
        response = _post(self.client, payload, topic="orders/updated")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(OrderStatusEvent.objects.count(), 0)

    def test_invalid_json_rejected(self):
        body = b"not-json{{{"
        sig = _sign(body)
        response = self.client.post(
            WEBHOOK_URL,
            data=body,
            content_type="application/json",
            HTTP_X_SHOPIFY_TOPIC="orders/updated",
            HTTP_X_SHOPIFY_HMAC_SHA256=sig,
        )
        self.assertEqual(response.status_code, 400)


@override_settings(SHOPIFY_WEBHOOK_SECRET="")
class EmptySecretRejectsAllTests(TestCase):
    """Defensive: empty secret must reject everything, not allow blank HMACs."""

    def test_empty_secret_rejects(self):
        body = json.dumps({"id": 1, "financial_status": "paid"}).encode("utf-8")
        response = self.client.post(
            WEBHOOK_URL,
            data=body,
            content_type="application/json",
            HTTP_X_SHOPIFY_TOPIC="orders/updated",
            HTTP_X_SHOPIFY_HMAC_SHA256="",
        )
        self.assertEqual(response.status_code, 401)
