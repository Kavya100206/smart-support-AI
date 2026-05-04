# Smart Support AI — Support Ticket System

A full-stack support ticket system with AI-powered classification **and** an autonomous resolution agent. New tickets are categorised by an LLM; eligible tickets are then handed to a LangGraph agent that pulls Shopify order context, searches an FAQ knowledge base, and either resolves the ticket or escalates with a structured reason.

## Overview

Smart Support AI reduces manual triage and resolution effort in two layers:

1. **Classification** — On submit, an LLM suggests a category and priority. The user can override.
2. **Autonomous resolution** — A LangGraph agent runs against open tickets, calling tools (`get_order_status`, `check_refund_eligibility`, `get_faq_answer`, `escalate_to_human`) until it produces a confident resolution or escalates. Every run is auditable via a stored decision trace.

The system is built around three hard rules:

- **No agent state between requests.** The graph is instantiated per request and discarded.
- **FAQ embeddings load once.** They live in a module-level variable, never reloaded per call.
- **Redis only stores serialized order dicts and rate-limit counters,** always with TTL — never agent state.

## Live Demo
[https://smart-support-frontend.onrender.com]([https://smart-support-frontend.onrender.com]))

## Features

- Create, filter, and search support tickets
- AI-powered category and priority suggestions (user-overridable)
- Autonomous resolution agent (LangGraph) with hard 3-iteration cap
- Shopify Admin API integration: order lookup, refund eligibility, webhook listener for `orders/updated` and `orders/fulfilled`
- FAQ similarity search (sentence-transformers, in-memory)
- Confidence-gated resolution (auto-escalate when confidence < 0.6)
- Retry with exponential backoff on Shopify and Groq calls (2 retries, then escalate)
- Redis caching for Shopify order lookups (5-minute TTL)
- Per-IP rate limiting on `POST /api/tickets/` and `POST /api/tickets/classify/` (20 req/min)
- Resolution metrics endpoint (`/api/tickets/metrics/`)
- Per-ticket audit trace endpoint (`/api/tickets/<id>/trace/`)
- Aggregated statistics dashboard
- Fully Dockerized full-stack setup

## Tech Stack

- **Backend:** Django 4.2, Django REST Framework, PostgreSQL
- **Agent:** LangGraph 0.2, sentence-transformers (FAQ similarity)
- **Reliability:** Redis 5, tenacity (retries), django-ratelimit
- **LLM:** Groq API (`llama-3.3-70b-versatile`)
- **HTTP client:** httpx (Shopify Admin REST, no SDK)
- **Frontend:** React 18, Vite
- **Infrastructure:** Docker, Docker Compose

## Setup

### Prerequisites

- Docker Desktop (running)
- A Groq API key — free at https://console.groq.com
- *(Optional, for live Shopify integration)* a Shopify dev store with an Admin API access token

### Running the application

1. Clone the project.

2. Create a `.env` file in the project root:

   ```env
   # Required
   GROQ_API_KEY=your_groq_api_key_here

   # Optional — required only when calling the agent on real orders
   SHOPIFY_SHOP_DOMAIN=your-shop.myshopify.com
   SHOPIFY_API_VERSION=2024-10
   SHOPIFY_ACCESS_TOKEN=shpat_xxxxxxxxxxxxxxxx
   SHOPIFY_WEBHOOK_SECRET=your_webhook_shared_secret

   # Optional — defaults to redis://redis:6379/0 in Docker
   REDIS_URL=redis://redis:6379/0
   ```

3. Start all services:

   ```bash
   docker-compose up --build
   ```

4. Access the application:
   - Frontend: http://localhost:5173
   - Backend API: http://localhost:8000/api/tickets/
   - Django Admin: http://localhost:8000/admin/

Database migrations run automatically on backend startup.

### Running the tests

```bash
docker compose exec backend pytest
```

The suite mocks Shopify, Groq, and Redis — no real network calls are made.

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST   | `/api/tickets/` | Create a ticket. Rate-limited to 20/min/IP; returns 429 with `retry_after` when exceeded. |
| GET    | `/api/tickets/` | List tickets, newest first. Supports `?category=`, `?priority=`, `?status=`, `?search=`. |
| PATCH  | `/api/tickets/<id>/` | Update a ticket (status, category, priority). |
| POST   | `/api/tickets/<id>/resolve/` | Run the autonomous resolution agent against the ticket. Returns the final action, confidence, tools called, latency, and a `trace_id`. |
| GET    | `/api/tickets/<id>/trace/` | Audit trail: all agent decision traces for the ticket, newest first. |
| GET    | `/api/tickets/stats/` | Aggregated ticket statistics (volume, breakdown). |
| GET    | `/api/tickets/metrics/` | Agent metrics: total runs, resolution rate, escalation rate, average latency. |
| POST   | `/api/tickets/classify/` | LLM classification — returns suggested category and priority. Rate-limited. |
| POST   | `/api/shopify/webhook/` | Webhook listener for `orders/updated` and `orders/fulfilled`. HMAC-verified. |

## Architecture

### Classification (Phase 0)

`POST /api/tickets/classify/` calls Groq with a tightly-scoped prompt (temperature 0.1, JSON-only output) and returns suggested `category` and `priority`. Failures degrade silently — the endpoint returns `{"suggested_category": null, ...}` so ticket submission never blocks on the LLM.

### Shopify integration (Phase 1)

A thin httpx-based client (`tickets/services/shopify_service.py`) calls the Shopify Admin REST API and extracts only a fixed set of fields (`order_id`, `status`, `shipping_status`, `created_at`, `refund_eligible`). The full Shopify response is never stored or logged — it goes out of scope as soon as `_extract_order_fields` returns.

A webhook listener at `/api/shopify/webhook/` HMAC-verifies the payload (using `SHOPIFY_WEBHOOK_SECRET`) and persists a slim `OrderStatusEvent` row containing only the topic, financial status, and fulfillment status — never the raw payload.

### Autonomous resolution agent (Phase 2)

`POST /api/tickets/<id>/resolve/` instantiates a fresh LangGraph graph and runs it against the ticket's description and Shopify order id. The agent has four tools:

- `get_order_status(order_id)` — wraps the Shopify order context service.
- `check_refund_eligibility(order_id)` — reuses the same service; refund window is 30 days for paid/partially-paid orders.
- `get_faq_answer(query)` — cosine similarity against ~10–15 FAQ pairs whose embeddings are loaded **once** at startup into a module-level variable.
- `escalate_to_human(reason)` — terminal action with a structured reason string.

A hard 3-iteration cap is enforced — beyond that the agent auto-escalates unconditionally. The graph is discarded as soon as the response returns; nothing about the run lives in process memory afterward.

Every run writes a row to `AgentDecisionTrace` capturing the tools called (in order), the final action, the resolution text or escalation reason, the confidence score, and iteration count. This is queryable per-ticket via `/api/tickets/<id>/trace/`.

### Confidence + reliability layer (Phase 3)

- **Confidence gate.** The LLM returns a `confidence` field on every step. If a proposed resolution comes in below `0.6`, the agent auto-escalates without attempting it.
- **Retries.** Both Shopify (`_admin_get`) and Groq (`_call_groq`) calls are wrapped in tenacity with `stop_after_attempt(3)` and exponential backoff (1 s → 2 s → 4 s cap). Retries fire on `httpx.HTTPError` and `ShopifyAPIError` for Shopify; on any exception for Groq. On final failure the agent escalates with a structured error reason in the decision trace.
- **Network vs. API failure semantics.** Network errors during Shopify lookup (connect timeout, DNS, etc.) collapse to "order not found" → tools surface `found=False`. A 5xx from Shopify, however, is *not* swallowed — `ShopifyAPIError` propagates so a real API/auth issue is never hidden behind a silent miss.
- **Order cache.** Shopify order lookups are cached in Redis by `order_id` with a 5-minute TTL (`SETEX`). On cache hit, the Shopify API is not called. On a Redis outage the cache layer degrades silently and falls through to Shopify.
- **Rate limiting.** `POST /api/tickets/` and `POST /api/tickets/classify/` are limited to 20 requests per minute per IP via django-ratelimit (Redis-backed). When exceeded, the response is `HTTP 429` with `{"error": "Rate limit exceeded", "retry_after": 60}`.
- **Metrics.** `auto_resolved` (bool) and `agent_latency_ms` (int) are persisted on each `Ticket` row to avoid joining `AgentDecisionTrace` on every metrics call. `/api/tickets/metrics/` aggregates these into `resolution_rate`, `escalation_rate`, and `avg_latency_ms`.

## LLM choice: Groq (llama-3.3-70b-versatile)

**Why Groq:** sub-second inference, generous free tier, no credit card required. `llama-3.3-70b-versatile` produces reliable JSON output with minimal prompt engineering.

**Why not OpenAI / Gemini:** OpenAI requires a paid account for reliable API access. Gemini's free-tier model availability was inconsistent during development (404s on `gemini-1.5-flash` via v1beta).

**Prompt design:** Raw JSON only, no markdown or explanation. Explicit rules per category/priority level reduce ambiguity. Temperature 0.1 for determinism.

**Error handling:** Missing API key or any exception (network, malformed response, invalid JSON) returns `{"suggested_category": null, "suggested_priority": null}` with HTTP 200 — ticket submission is never blocked by the LLM.

## Design decisions

### Backend

- **APIView over ViewSet** — explicit control over each HTTP method; simpler than router magic.
- **DB-level aggregation in stats and metrics** — `Count`, `Avg`, `annotate` only; no Python-level loops.
- **Per-request agent graph** — instantiated inside the view, discarded after. No global agent, no cross-request state leakage.
- **Module-level FAQ embeddings** — loaded once at startup, reused across requests. The dataset is small (~15 pairs) so no FAISS index is needed; pure NumPy cosine similarity is enough.
- **Extract-and-discard for Shopify responses** — only the fields we need leave the function; the raw payload goes out of scope immediately.
- **Two-layer error semantics in `get_order_context`** — `httpx.HTTPError` becomes `None` (lookup didn't happen), `ShopifyAPIError` propagates (Shopify said something went wrong, surface it).
- **Module-level `get_redis` indirection in `shopify_service`** — `shopify_service.get_redis` is a thin delegate over `redis_client.get_redis`. This lets tests patch either module's `get_redis` and have the patch actually intercept the cache call, instead of fighting frozen `from … import` bindings.
- **URL ordering for `metrics`** — `/api/tickets/metrics/` is registered before `/api/tickets/<int:pk>/` so Django doesn't try to parse the literal string `metrics` as a primary key.
- **Choices enforced at model and serializer level** — invalid values can't reach the DB.

### Frontend

- **LLM classify on blur** — the classify API fires when the user leaves the description textarea, not on every keystroke. Suggestions land before the user reaches the dropdowns without spamming the LLM.
- **`refreshKey` pattern for stats** — a numeric key incremented on ticket creation is passed to `StatsDashboard`; its `useEffect` depends on the key and re-fetches automatically.
- **Centralized API layer** — all fetch calls live in `src/api.js`. Components never call `fetch` directly, so the base URL is configurable and the components stay testable.

## Project structure

```
smart-support-ai/
├── backend/
│   ├── core/                          # Django project settings and URL config
│   ├── tickets/
│   │   ├── models.py                  # Ticket, OrderStatusEvent, FAQEntry, AgentDecisionTrace
│   │   ├── serializers.py
│   │   ├── views.py                   # List/create, detail, stats, resolve, metrics, trace
│   │   ├── classify.py                # Groq classification endpoint
│   │   ├── webhooks.py                # Shopify webhook listener (HMAC-verified)
│   │   ├── urls.py
│   │   ├── services/
│   │   │   ├── shopify_service.py     # Order context + Redis cache + tenacity retry
│   │   │   ├── redis_client.py        # Single Redis client, TTL-only writes
│   │   │   ├── faq_service.py         # FAQ embeddings loaded once at startup
│   │   │   ├── agent_tools.py         # 4 LangGraph tools
│   │   │   └── agent_graph.py         # LangGraph graph + Groq call (per-request)
│   │   ├── tests/                     # Mocks for Shopify, Groq, Redis
│   │   └── migrations/
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── components/                # TicketForm, TicketList, TicketCard, StatsDashboard
│   │   ├── api.js                     # Centralized API service layer
│   │   └── App.jsx
│   ├── Dockerfile
│   └── package.json
├── docker-compose.yml
└── .env                               # Not committed — secrets only
```
