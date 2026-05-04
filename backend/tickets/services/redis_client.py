"""Shared Redis client for the smart-support-ai backend.

Responsibilities
----------------
- Read REDIS_URL from the environment (never hardcoded).
- Expose a single ``get_redis()`` function that returns a connected
  ``redis.Redis`` instance, created lazily on first call and reused
  across all subsequent calls in the same process.

Ground rules (enforced here and documented for all callers)
-----------------------------------------------------------
- Redis holds ONLY serialized order dicts and rate-limit counters.
- Every key written to Redis MUST be written with an explicit TTL (use
  ``setex`` / ``set(..., ex=...)`` — never bare ``set``).
- Agent state is NEVER stored in Redis.
- The client is not the Django cache backend (that is configured
  separately via django-redis in settings.CACHES). This module is a
  direct ``redis-py`` client used by the Shopify order cache layer.

Usage
-----
    from tickets.services.redis_client import get_redis

    r = get_redis()
    r.setex("order:5001", 300, json.dumps(order_dict))
    raw = r.get("order:5001")
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import redis

logger = logging.getLogger(__name__)

# ── Module-level singleton ─────────────────────────────────────────────────────
# Lazily initialised on first call to get_redis().
# Never stored per-request — one client for the process lifetime.
_redis_client: Optional[redis.Redis] = None


def get_redis() -> redis.Redis:
    """Return the shared Redis client, creating it on first call.

    Reads ``REDIS_URL`` from the environment.  Falls back to
    ``redis://localhost:6379/0`` so local development without Docker still
    works — but in production / Docker the env var must be set explicitly.

    Raises:
        redis.exceptions.ConnectionError: if the Redis server is unreachable
            and the first command is attempted.  The connection itself is
            lazy; this function will not raise on a misconfigured URL —
            the error surfaces on the first actual Redis command.
    """
    global _redis_client

    if _redis_client is None:
        url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        logger.info("Initialising Redis client: %s", _redact_url(url))
        _redis_client = redis.Redis.from_url(
            url,
            decode_responses=True,   # always return str, never bytes
            socket_connect_timeout=2,
            socket_timeout=2,
        )

    return _redis_client


# ── Key helpers ────────────────────────────────────────────────────────────────

def order_cache_key(order_id: str) -> str:
    """Canonical Redis key for a Shopify order lookup cache entry.

    Format: ``order:<order_id>``

    Keeping the key format in one place prevents typos between the writer
    (shopify_service) and any future readers.
    """
    return f"order:{order_id}"


# ── Internal helpers ───────────────────────────────────────────────────────────

def _redact_url(url: str) -> str:
    """Return the URL with any password component replaced by ***."""
    try:
        from urllib.parse import urlparse, urlunparse  # noqa: PLC0415

        parsed = urlparse(url)
        if parsed.password:
            netloc = parsed.netloc.replace(parsed.password, "***")
            return urlunparse(parsed._replace(netloc=netloc))
    except Exception:  # noqa: BLE001
        pass
    return url
