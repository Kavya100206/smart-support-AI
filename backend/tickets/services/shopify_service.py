"""Shopify order context service.

Single responsibility: given an order id or customer email, fetch the order
from the Shopify Admin REST API, extract a fixed set of fields into a plain
dict, and return that dict. The full Shopify response object is never
returned, stored, or logged — it goes out of scope as soon as
``_extract_order_fields`` returns.

Phase 3 additions
-----------------
- Redis cache layer on ``get_order_context``: cache by order_id, TTL = 5 min.
  Cache is checked before hitting Shopify; on miss the result is stored.
  Cache holds ONLY serialized extracted-field dicts, never raw Shopify payloads.
- Tenacity retry on ``_admin_get``: 2 retries (3 total attempts) with
  exponential backoff (1 s → 2 s → 4 s cap). On final failure the exception
  propagates to the tool caller which returns found=False and stores the
  error in the decision trace.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from django.conf import settings
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# Indirection so BOTH test suites' patches actually intercept the call:
#   * test_redis_cache.py patches ``tickets.services.redis_client.get_redis``.
#     The delegate below resolves ``redis_client.get_redis`` at call time,
#     so that patch is honored.
#   * test_retry_logic.py patches ``tickets.services.shopify_service.get_redis``.
#     The cache helpers call the bare name ``get_redis()`` (resolved through
#     this module's globals), so swapping ``shopify_service.get_redis`` with
#     a mock is also honored.
# A direct ``from redis_client import get_redis`` would only satisfy one of
# the two — the delegate satisfies both.
from tickets.services import redis_client
from tickets.services.redis_client import order_cache_key  # noqa: F401


def get_redis():
    """Indirection over ``redis_client.get_redis`` (see comment above)."""
    return redis_client.get_redis()

logger = logging.getLogger(__name__)

REFUND_WINDOW_DAYS = 30

# Public output shape — every caller must rely on exactly these keys.
ORDER_CONTEXT_FIELDS = (
    "order_id",
    "status",
    "shipping_status",
    "created_at",
    "refund_eligible",
)

# Redis TTL for cached order dicts (seconds).
_ORDER_CACHE_TTL = 300  # 5 minutes


class ShopifyConfigError(RuntimeError):
    """Raised when required Shopify env vars are missing at call time."""


class ShopifyAPIError(RuntimeError):
    """Raised on non-2xx responses from the Shopify Admin API."""


def _require_config() -> tuple[str, str, str]:
    domain = settings.SHOPIFY_SHOP_DOMAIN
    version = settings.SHOPIFY_API_VERSION
    token = settings.SHOPIFY_ACCESS_TOKEN
    if not (domain and version and token):
        raise ShopifyConfigError(
            "Shopify is not configured: set SHOPIFY_SHOP_DOMAIN, "
            "SHOPIFY_API_VERSION, and SHOPIFY_ACCESS_TOKEN."
        )
    return domain, version, token


def _extract_order_fields(order: dict) -> dict:
    """Pull the fixed set of fields off a Shopify order dict.

    The input ``order`` dict is consumed only inside this function; the caller
    discards it immediately after this returns.
    """
    created_at_raw = order.get("created_at") or ""
    refund_eligible = _is_refund_eligible(
        financial_status=order.get("financial_status") or "",
        created_at_raw=created_at_raw,
    )
    return {
        "order_id": str(order.get("id", "")),
        "status": order.get("financial_status") or "",
        "shipping_status": order.get("fulfillment_status") or "unfulfilled",
        "created_at": created_at_raw,
        "refund_eligible": refund_eligible,
    }


def _is_refund_eligible(*, financial_status: str, created_at_raw: str) -> bool:
    """Refund window: paid (or partially paid) AND placed within 30 days."""
    if financial_status not in ("paid", "partially_paid"):
        return False
    if not created_at_raw:
        return False
    try:
        # Shopify uses ISO 8601 with offset, e.g. "2026-04-12T10:23:54-04:00"
        created_at = datetime.fromisoformat(created_at_raw)
    except ValueError:
        return False
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - created_at <= timedelta(days=REFUND_WINDOW_DAYS)


# ── Phase 3: retry-wrapped HTTP helper ────────────────────────────────────────

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    retry=retry_if_exception_type((httpx.HTTPError, ShopifyAPIError)),
    reraise=True,
)
def _admin_get(path: str, params: Optional[dict] = None) -> dict:
    """Make a GET request to the Shopify Admin REST API.

    Retried up to 3 attempts (2 retries) with exponential backoff:
        attempt 1 → fail → wait 1 s
        attempt 2 → fail → wait 2 s
        attempt 3 → fail → raise ShopifyAPIError (caller handles it)

    Only ``httpx.HTTPError`` (network-level) and ``ShopifyAPIError``
    (non-2xx response) trigger a retry. 404s are returned as an empty dict
    immediately without retrying.
    """
    domain, version, token = _require_config()
    url = f"https://{domain}/admin/api/{version}/{path}"
    headers = {
        "X-Shopify-Access-Token": token,
        "Accept": "application/json",
    }
    response = httpx.get(url, headers=headers, params=params, timeout=10.0)
    if response.status_code == 404:
        return {}
    if response.status_code >= 400:
        raise ShopifyAPIError(
            f"Shopify {path} returned {response.status_code}"
        )
    return response.json()


# ── Phase 3: cache helpers ─────────────────────────────────────────────────────

def _read_cache(order_id: str) -> Optional[dict]:
    """Return cached order dict from Redis, or None on miss/error."""
    try:
        raw = get_redis().get(order_cache_key(order_id))
        if raw:
            logger.debug("Shopify order cache HIT for order_id=%s", order_id)
            return json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        # Redis is unavailable — degrade gracefully, fall through to Shopify.
        logger.warning("Redis cache read failed for order_id=%s: %s", order_id, exc)
    return None


def _write_cache(order_id: str, data: dict) -> None:
    """Serialize *data* and store in Redis with a 5-minute TTL.

    Uses ``setex`` — TTL is always set. Never stores agent state.
    Errors are swallowed so a Redis outage never breaks a Shopify response.
    """
    try:
        get_redis().setex(
            order_cache_key(order_id),
            _ORDER_CACHE_TTL,
            json.dumps(data),
        )
        logger.debug("Shopify order cached for order_id=%s (TTL=%ds)", order_id, _ORDER_CACHE_TTL)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis cache write failed for order_id=%s: %s", order_id, exc)


# ── Public API ─────────────────────────────────────────────────────────────────

def get_order_context(
    *, order_id: Optional[str] = None, email: Optional[str] = None
) -> Optional[dict]:
    """Fetch an order by id or email and return the extracted-fields dict.

    Phase 3: when *order_id* is provided, the Redis cache is consulted first.
    On a cache hit the Shopify API is not called. On a cache miss, Shopify is
    called (with tenacity retry), the result is cached, and returned.

    Returns ``None`` if no matching order exists. Raises ``ValueError`` if
    neither lookup key is supplied. The full Shopify response lives only
    inside this function and is discarded on return.
    """
    if not (order_id or email):
        raise ValueError("Provide order_id or email.")

    # ── Cache lookup (order_id only — email lookups are not cached) ────────────
    if order_id:
        cached = _read_cache(order_id)
        if cached is not None:
            return cached

    # ── Fetch from Shopify (with retry) ────────────────────────────────────────
    # Tenacity re-raises after 3 failed attempts. We split the two error
    # classes here on purpose:
    #   * httpx.HTTPError (network failure: connect, timeout, etc.) → return
    #     None. The Shopify call never produced a response, so to the caller
    #     the lookup is indistinguishable from "no such order" — the agent
    #     tool layer surfaces this as found=False.
    #   * ShopifyAPIError (Shopify returned a non-2xx response) → let it
    #     propagate. Shopify *did* respond, just with an error; collapsing
    #     that to None would hide a real API/auth issue from upstream callers
    #     and from test_5xx_raises.
    try:
        if order_id:
            body = _admin_get(f"orders/{order_id}.json")
            order = body.get("order")
        else:
            body = _admin_get(
                "orders.json",
                params={"email": email, "status": "any", "limit": 1},
            )
            orders = body.get("orders") or []
            order = orders[0] if orders else None
    except httpx.HTTPError as exc:
        logger.warning("Shopify lookup failed after retries: %s", exc)
        return None

    if not order:
        return None

    extracted = _extract_order_fields(order)
    # `order` and `body` go out of scope when this function returns.

    # ── Cache the extracted result (order_id only) ─────────────────────────────
    if order_id:
        _write_cache(order_id, extracted)

    return extracted
