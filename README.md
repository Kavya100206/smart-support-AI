# Smart Support AI — Autonomous E-commerce Support Agent

A full-stack support ticket system with AI-powered classification and an autonomous LangGraph resolution agent. Tickets are classified by an LLM on submit; open tickets are resolved end-to-end by an agent that searches an FAQ knowledge base and either resolves or escalates with a structured decision trace. The system is built around a Shopify integration layer (httpx-based, no SDK) that is fully implemented and test-verified — live credentials are optional for local development.

## Live Demo
[https://smart-support-frontend.onrender.com](https://smart-support-ai-2.onrender.com/)

## Tech Stack

- **Backend:** Django 4.2, DRF, PostgreSQL
- **Agent:** LangGraph 0.2, NumPy (FAQ cosine similarity)
- **Reliability:** Redis, tenacity, django-ratelimit
- **LLM:** Groq API (llama-3.3-70b-versatile)
- **Frontend:** React 18, Vite
- **Infrastructure:** Docker, Docker Compose

## Features

- AI-powered ticket classification with human-override support
- LangGraph autonomous resolution agent with 4 tools: `get_order_status`, `check_refund_eligibility`, `get_faq_answer`, `escalate_to_human`
- Hard confidence gate — auto-escalates when confidence < 0.6
- Hard 3-iteration cap — auto-escalates unconditionally beyond that
- Shopify Admin API integration via httpx — extract-and-discard pattern, HMAC-verified webhook listener for `orders/updated` and `orders/fulfilled`
- Redis caching for Shopify order lookups (5-min TTL), degrades silently on Redis outage
- Tenacity retry with exponential backoff on Shopify and Groq calls (2 retries, then escalate)
- Per-IP rate limiting on POST endpoints — 20 req/min, returns 429 with `retry_after`
- Per-ticket audit trace stored in PostgreSQL — tools called, confidence, latency, final action
- Agent metrics endpoint — resolution rate, escalation rate, avg latency

## Architecture

```text
POST /api/tickets/<id>/resolve/
│
▼
AgentResolveView
│
▼
build_graph() ── fresh LangGraph graph per request
│
├─▶ call_llm ── Groq returns {tool, args, confidence}
│        │
│        ├── confidence < 0.6 → auto-escalate
│        └── iterations ≥ 3  → auto-escalate
│
├─▶ execute_tool
│        ├── get_order_status(order_id)
│        │     └── Redis cache → Shopify Admin API
│        ├── check_refund_eligibility(order_id)
│        │     └── same Shopify context service
│        ├── get_faq_answer(query)
│        │     └── NumPy cosine similarity over module-level embeddings
│        └── escalate_to_human(reason) → END
│
└─▶ AgentDecisionTrace saved to PostgreSQL
    └── tools_called, final_action, confidence_score,
        iterations, resolution_text, escalation_reason
```

Agent state is instantiated per request and discarded after. FAQ embeddings are loaded once at startup into a module-level variable and reused across all requests.

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/tickets/` | Create ticket — rate limited 20/min/IP |
| GET | `/api/tickets/` | List with `?category=`, `?priority=`, `?status=`, `?search=` filters |
| PATCH | `/api/tickets/<id>/` | Update ticket status, category, priority |
| POST | `/api/tickets/<id>/resolve/` | Run agent — returns `trace_id`, `confidence`, `tools_called`, `latency` |
| GET | `/api/tickets/<id>/trace/` | Full audit trail — all agent runs for this ticket |
| GET | `/api/tickets/metrics/` | Resolution rate, escalation rate, avg latency |
| GET | `/api/tickets/stats/` | Ticket volume and breakdown |
| POST | `/api/tickets/classify/` | LLM classification — rate limited |
| POST | `/api/shopify/webhook/` | HMAC-verified Shopify order event listener |

## Setup

### Prerequisites

- Docker Desktop
- Groq API key — free at https://console.groq.com

### Running

1. Create `.env` in the project root:

```env
# Required
GROQ_API_KEY=your_groq_api_key_here
DATABASE_URL=postgresql://user:password@hostname/dbname

# Redis — set automatically in Docker, only needed for local non-Docker runs
REDIS_URL=redis://redis:6379/0
```

2. Start all services:

```bash
docker-compose up --build
```

3. Access:
   - Frontend: http://localhost:5173
   - API: http://localhost:8000/api/tickets/
   - Admin: http://localhost:8000/admin/

Migrations run automatically on startup.

### Running Tests

```bash
docker compose exec backend python -m pytest tickets/tests/ -v
```

All tests mock Shopify, Groq, and Redis — no real credentials needed.

## Key Design Decisions

**Per-request agent graph** — LangGraph graph instantiated inside the view function, discarded after response. No global agent, no cross-request state leakage.

**Module-level FAQ embeddings** — loaded once in `AppConfig.ready()`, reused across all requests. Dataset is small (~10-15 pairs) so NumPy cosine similarity is sufficient — no FAISS index needed.

**Extract-and-discard for Shopify** — only `order_id`, `status`, `shipping_status`, `created_at`, `refund_eligible` leave the service function. The raw Shopify response goes out of scope immediately and is never stored or logged.

**Two-layer error semantics** — network errors during Shopify lookup collapse to `found=False` (lookup didn't happen). Shopify 5xx errors propagate as `ShopifyAPIError` so real API failures are never silently swallowed.

**DB-level aggregation** — `/api/tickets/metrics/` uses Django ORM `Count`/`Avg`/`annotate` exclusively. No Python-level loops regardless of ticket volume.

**URL ordering** — `/api/tickets/metrics/` registered before `/api/tickets/<int:pk>/` so Django never attempts to parse the string `metrics` as a primary key.
