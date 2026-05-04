"""Tests for the Shopify order context service.

External Shopify HTTP calls are mocked via ``unittest.mock.patch`` on
``httpx.get``. We assert the *exact* output shape of ``get_order_context`` —
the contract Phase 2 tools will rely on.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from tickets.services.shopify_service import (
    ORDER_CONTEXT_FIELDS,
    ShopifyAPIError,
    ShopifyConfigError,
    _extract_order_fields,
    _is_refund_eligible,
    get_order_context,
)


SHOPIFY_TEST_SETTINGS = {
    "SHOPIFY_SHOP_DOMAIN": "test-store.myshopify.com",
    "SHOPIFY_API_VERSION": "2024-10",
    "SHOPIFY_ACCESS_TOKEN": "shpat_test_token",
    "SHOPIFY_WEBHOOK_SECRET": "whsec_test",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mock_response(status_code: int = 200, json_body: dict | None = None) -> MagicMock:
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = json_body or {}
    return mock


class ExtractOrderFieldsTests(SimpleTestCase):
    def test_returns_exact_contract(self):
        order = {
            "id": 5001,
            "financial_status": "paid",
            "fulfillment_status": "fulfilled",
            "created_at": _now_iso(),
        }
        result = _extract_order_fields(order)
        self.assertEqual(set(result.keys()), set(ORDER_CONTEXT_FIELDS))
        self.assertEqual(result["order_id"], "5001")
        self.assertEqual(result["status"], "paid")
        self.assertEqual(result["shipping_status"], "fulfilled")
        self.assertTrue(result["refund_eligible"])

    def test_unfulfilled_default(self):
        order = {
            "id": 5002,
            "financial_status": "paid",
            "fulfillment_status": None,
            "created_at": _now_iso(),
        }
        self.assertEqual(_extract_order_fields(order)["shipping_status"], "unfulfilled")

    def test_missing_fields_safe(self):
        result = _extract_order_fields({"id": 5003})
        self.assertEqual(set(result.keys()), set(ORDER_CONTEXT_FIELDS))
        self.assertEqual(result["order_id"], "5003")
        self.assertEqual(result["status"], "")
        self.assertEqual(result["shipping_status"], "unfulfilled")
        self.assertEqual(result["created_at"], "")
        self.assertFalse(result["refund_eligible"])


class RefundEligibilityTests(SimpleTestCase):
    def test_paid_recent_eligible(self):
        self.assertTrue(
            _is_refund_eligible(financial_status="paid", created_at_raw=_now_iso())
        )

    def test_partially_paid_recent_eligible(self):
        self.assertTrue(
            _is_refund_eligible(
                financial_status="partially_paid", created_at_raw=_now_iso()
            )
        )

    def test_unpaid_rejected(self):
        self.assertFalse(
            _is_refund_eligible(financial_status="pending", created_at_raw=_now_iso())
        )

    def test_outside_window_rejected(self):
        old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
        self.assertFalse(
            _is_refund_eligible(financial_status="paid", created_at_raw=old)
        )

    def test_naive_datetime_treated_as_utc(self):
        naive = datetime.utcnow().isoformat()
        self.assertTrue(
            _is_refund_eligible(financial_status="paid", created_at_raw=naive)
        )

    def test_garbage_input_rejected(self):
        self.assertFalse(
            _is_refund_eligible(financial_status="paid", created_at_raw="not-a-date")
        )
        self.assertFalse(
            _is_refund_eligible(financial_status="paid", created_at_raw="")
        )


@override_settings(**SHOPIFY_TEST_SETTINGS)
class GetOrderContextTests(SimpleTestCase):
    # get_redis is imported lazily inside _read_cache/_write_cache from
    # tickets.services.redis_client — NOT at shopify_service module level.
    # Patching the source (redis_client.get_redis) intercepts all lazy imports.
    def setUp(self):
        from unittest.mock import MagicMock, patch
        redis_mock = MagicMock()
        redis_mock.get.return_value = None  # always a cache miss
        redis_mock.setex.return_value = True
        self._redis_patcher = patch(
            "tickets.services.redis_client.get_redis",
            return_value=redis_mock,
        )
        self._redis_patcher.start()

    def tearDown(self):
        self._redis_patcher.stop()

    @patch("tickets.services.shopify_service.httpx.get")
    def test_by_order_id(self, mock_get):
        mock_get.return_value = _mock_response(
            json_body={
                "order": {
                    "id": 7001,
                    "financial_status": "paid",
                    "fulfillment_status": "fulfilled",
                    "created_at": _now_iso(),
                }
            }
        )
        result = get_order_context(order_id="7001")
        self.assertIsNotNone(result)
        self.assertEqual(set(result.keys()), set(ORDER_CONTEXT_FIELDS))
        self.assertEqual(result["order_id"], "7001")

        call = mock_get.call_args
        url = call.args[0] if call.args else call.kwargs.get("url")
        headers = call.kwargs["headers"]
        self.assertIn("test-store.myshopify.com", url)
        self.assertIn("/orders/7001.json", url)
        self.assertEqual(headers["X-Shopify-Access-Token"], "shpat_test_token")

    @patch("tickets.services.shopify_service.httpx.get")
    def test_by_email_takes_first_match(self, mock_get):
        mock_get.return_value = _mock_response(
            json_body={
                "orders": [
                    {
                        "id": 8001,
                        "financial_status": "paid",
                        "fulfillment_status": None,
                        "created_at": _now_iso(),
                    }
                ]
            }
        )
        result = get_order_context(email="customer@example.com")
        self.assertIsNotNone(result)
        self.assertEqual(result["order_id"], "8001")
        self.assertEqual(result["shipping_status"], "unfulfilled")

        call = mock_get.call_args
        self.assertEqual(call.kwargs["params"]["email"], "customer@example.com")
        self.assertEqual(call.kwargs["params"]["limit"], 1)

    @patch("tickets.services.shopify_service.httpx.get")
    def test_404_returns_none(self, mock_get):
        mock_get.return_value = _mock_response(status_code=404)
        self.assertIsNone(get_order_context(order_id="404404"))

    @patch("tickets.services.shopify_service.httpx.get")
    def test_email_no_results_returns_none(self, mock_get):
        mock_get.return_value = _mock_response(json_body={"orders": []})
        self.assertIsNone(get_order_context(email="nobody@example.com"))

    @patch("tickets.services.shopify_service.httpx.get")
    def test_5xx_raises(self, mock_get):
        # Phase 3: _admin_get now has @retry(stop_after_attempt(3)).
        # The 500 mock is returned on all 3 attempts before ShopifyAPIError
        # is finally raised. We assert the exception is raised (not swallowed).
        mock_get.return_value = _mock_response(status_code=500)
        with self.assertRaises(ShopifyAPIError):
            get_order_context(order_id="9001")
        # Confirm tenacity retried: httpx.get was called 3 times.
        self.assertEqual(mock_get.call_count, 3)

    def test_missing_lookup_key_raises(self):
        with self.assertRaises(ValueError):
            get_order_context()


@override_settings(
    SHOPIFY_SHOP_DOMAIN="", SHOPIFY_ACCESS_TOKEN="", SHOPIFY_API_VERSION=""
)
class MissingConfigTests(SimpleTestCase):
    def test_missing_config_raises(self):
        with self.assertRaises(ShopifyConfigError):
            get_order_context(order_id="1")
