"""Tests for the Redis caching layer in tickets/services/shopify_service.py.

Verifies:
1. Cache HIT: Redis returns a value → Shopify HTTP call is never made.
2. Cache MISS: Redis returns None → Shopify is called, result is cached
   via setex with TTL = 300 s.
3. Redis error (GET): degrades gracefully, falls through to Shopify.
4. Redis error (SET): Shopify result is still returned even if caching fails.

All Shopify HTTP calls are mocked so no real network traffic is made.
All Redis calls are mocked so no real Redis connection is needed.

Mock path note
--------------
get_redis is imported LAZILY inside _read_cache/_write_cache:
    from tickets.services.redis_client import get_redis, order_cache_key

So the correct patch target is ``tickets.services.redis_client.get_redis``,
NOT ``tickets.services.shopify_service.get_redis`` (which doesn't exist at
module level and causes AttributeError).
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


# ── Fixtures ───────────────────────────────────────────────────────────────────

FAKE_ORDER_CONTEXT = {
    "order_id": "9001",
    "status": "paid",
    "shipping_status": "fulfilled",
    "created_at": "2026-04-01T10:00:00+00:00",
    "refund_eligible": True,
}

FAKE_SHOPIFY_ORDER_RESPONSE = {
    "order": {
        "id": 9001,
        "financial_status": "paid",
        "fulfillment_status": "fulfilled",
        "created_at": "2026-04-01T10:00:00+00:00",
    }
}


def _make_redis_mock(get_return=None):
    """Return a MagicMock that mimics a redis.Redis instance."""
    mock = MagicMock()
    mock.get.return_value = get_return
    mock.setex.return_value = True
    return mock


# ── Test: cache HIT ────────────────────────────────────────────────────────────

class TestRedisCacheHit:
    def test_returns_cached_dict_without_calling_shopify(self):
        """On a cache hit get_order_context must return the cached dict and
        never touch the Shopify Admin API."""
        cached_json = json.dumps(FAKE_ORDER_CONTEXT)
        redis_mock = _make_redis_mock(get_return=cached_json)

        with (
            patch(
                "tickets.services.redis_client.get_redis",
                return_value=redis_mock,
            ),
            patch("tickets.services.shopify_service._admin_get") as mock_admin,
        ):
            from tickets.services.shopify_service import get_order_context

            result = get_order_context(order_id="9001")

        # Shopify must NOT have been called.
        mock_admin.assert_not_called()

        # The returned dict must match the cached data exactly.
        assert result == FAKE_ORDER_CONTEXT

    def test_cache_hit_returns_exact_keys(self):
        """Result shape must contain exactly the ORDER_CONTEXT_FIELDS keys."""
        from tickets.services.shopify_service import ORDER_CONTEXT_FIELDS

        cached_json = json.dumps(FAKE_ORDER_CONTEXT)
        redis_mock = _make_redis_mock(get_return=cached_json)

        with patch("tickets.services.redis_client.get_redis", return_value=redis_mock):
            from tickets.services.shopify_service import get_order_context

            result = get_order_context(order_id="9001")

        assert set(result.keys()) == set(ORDER_CONTEXT_FIELDS)


# ── Test: cache MISS ───────────────────────────────────────────────────────────

class TestRedisCacheMiss:
    def test_calls_shopify_on_miss_and_caches_result(self):
        """On a cache miss: Shopify is called, result is stored via setex(TTL=300)."""
        redis_mock = _make_redis_mock(get_return=None)  # None = cache miss

        with (
            patch("tickets.services.redis_client.get_redis", return_value=redis_mock),
            patch(
                "tickets.services.shopify_service._admin_get",
                return_value=FAKE_SHOPIFY_ORDER_RESPONSE,
            ),
        ):
            from tickets.services.shopify_service import get_order_context

            result = get_order_context(order_id="9001")

        # Shopify must have been called; result must contain expected fields.
        assert result["order_id"] == "9001"
        assert result["status"] == "paid"

        # setex must have been called with TTL = 300.
        redis_mock.setex.assert_called_once()
        call_args = redis_mock.setex.call_args
        key_arg, ttl_arg, value_arg = call_args.args
        assert ttl_arg == 300
        assert "order:9001" in key_arg

        # The serialized value must be valid JSON containing the result.
        stored = json.loads(value_arg)
        assert stored["order_id"] == "9001"

    def test_result_shape_on_cache_miss(self):
        """Result on miss must have the same shape as ORDER_CONTEXT_FIELDS."""
        from tickets.services.shopify_service import ORDER_CONTEXT_FIELDS

        redis_mock = _make_redis_mock(get_return=None)

        with (
            patch("tickets.services.redis_client.get_redis", return_value=redis_mock),
            patch(
                "tickets.services.shopify_service._admin_get",
                return_value=FAKE_SHOPIFY_ORDER_RESPONSE,
            ),
        ):
            from tickets.services.shopify_service import get_order_context

            result = get_order_context(order_id="9001")

        assert set(result.keys()) == set(ORDER_CONTEXT_FIELDS)


# ── Test: Redis errors degrade gracefully ──────────────────────────────────────

class TestRedisDegradation:
    def test_redis_get_error_falls_through_to_shopify(self):
        """If Redis.get raises, get_order_context must still call Shopify."""
        import redis

        redis_mock = MagicMock()
        redis_mock.get.side_effect = redis.exceptions.ConnectionError("Redis down")

        with (
            patch("tickets.services.redis_client.get_redis", return_value=redis_mock),
            patch(
                "tickets.services.shopify_service._admin_get",
                return_value=FAKE_SHOPIFY_ORDER_RESPONSE,
            ) as mock_admin,
        ):
            from tickets.services.shopify_service import get_order_context

            result = get_order_context(order_id="9001")

        mock_admin.assert_called_once()
        assert result is not None
        assert result["order_id"] == "9001"

    def test_redis_setex_error_does_not_prevent_return(self):
        """If Redis.setex raises, the Shopify result is still returned."""
        import redis

        redis_mock = _make_redis_mock(get_return=None)
        redis_mock.setex.side_effect = redis.exceptions.ConnectionError("Redis down")

        with (
            patch("tickets.services.redis_client.get_redis", return_value=redis_mock),
            patch(
                "tickets.services.shopify_service._admin_get",
                return_value=FAKE_SHOPIFY_ORDER_RESPONSE,
            ),
        ):
            from tickets.services.shopify_service import get_order_context

            result = get_order_context(order_id="9001")

        # Despite setex failing, the result is still returned correctly.
        assert result is not None
        assert result["status"] == "paid"
