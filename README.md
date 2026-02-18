# Smart Support AI — Support Ticket System

A full-stack support ticket system with AI-powered ticket classification. Users submit support tickets and an LLM automatically suggests a category and priority based on the description. Users can review and override the suggestions before submitting.

## Tech Stack

- **Backend**: Django 4.2, Django REST Framework, PostgreSQL
- **Frontend**: React 18, Vite
- **LLM**: Groq API (llama-3.3-70b-versatile)
- **Infrastructure**: Docker, Docker Compose

## Setup

### Prerequisites

- Docker Desktop (running)
- A Groq API key — free at https://console.groq.com

### Running the Application

1. Clone or unzip the project folder.

2. Create a `.env` file in the project root:

   ```
   GROQ_API_KEY=your_groq_api_key_here
   ```

3. Start all services:

   ```bash
   docker-compose up --build
   ```

4. Access the application:
   - Frontend: http://localhost:5173
   - Backend API: http://localhost:8000/api/tickets/
   - Django Admin: http://localhost:8000/admin/

Database migrations run automatically on backend startup. No manual setup steps are required beyond providing the API key.

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/tickets/` | Create a ticket (returns 201) |
| GET | `/api/tickets/` | List all tickets, newest first. Supports `?category=`, `?priority=`, `?status=`, `?search=` |
| PATCH | `/api/tickets/<id>/` | Update a ticket (status, category, priority) |
| GET | `/api/tickets/stats/` | Aggregated statistics |
| POST | `/api/tickets/classify/` | LLM classification — returns suggested category and priority |

## LLM Choice: Groq (llama-3.3-70b-versatile)

**Why Groq:** Groq provides extremely fast inference (typically under 1 second) with a generous free tier and no credit card required. The `llama-3.3-70b-versatile` model produces reliable, structured JSON output with minimal prompt engineering.

**Why not OpenAI or Gemini:** OpenAI requires a paid account for reliable API access. Gemini's free-tier model availability was inconsistent at the time of development — the `gemini-1.5-flash` model returned 404 errors via the v1beta API.

**Prompt design:** The prompt instructs the model to return only a raw JSON object with no explanation or markdown. It includes explicit rules for each category and priority level to reduce ambiguity. Temperature is set to 0.1 for consistent, deterministic output.

**Error handling:** If the API key is missing, the classify endpoint returns `{"suggested_category": null, "suggested_priority": null}` immediately. Any exception (network error, malformed response, invalid JSON) is caught and returns the same null response with HTTP 200, so ticket submission always works regardless of LLM availability.

## Design Decisions

### Backend

- **APIView over ViewSet**: Used `APIView` directly for explicit control over each HTTP method. Simpler to reason about than a ViewSet with router magic.

- **DB-level aggregation in stats**: The `/api/tickets/stats/` endpoint uses Django ORM `Count`, `Avg`, and `annotate` exclusively. No Python-level loops or in-memory aggregation. This keeps the stats endpoint efficient regardless of ticket volume.

- **URL ordering**: `tickets/stats/` is registered before `tickets/<int:pk>/` in the URL config. Since `<int:pk>` only matches integers, `stats` would not conflict regardless, but the explicit ordering makes intent clear.

- **Choices enforced at model level**: All `CharField` fields with choices (`category`, `priority`, `status`) are validated by the serializer on every API request, preventing invalid values from reaching the database.

### Frontend

- **LLM classify on blur**: The classify API is called when the user clicks away from the description textarea (`onBlur`), not on every keystroke. This avoids spamming the API while still providing suggestions before the user reaches the dropdowns.

- **refreshKey pattern for stats**: A numeric key incremented on ticket creation is passed to `StatsDashboard` as a prop. The dashboard's `useEffect` depends on this key, triggering a re-fetch automatically whenever a new ticket is submitted.

- **Centralized API layer**: All fetch calls are in `src/api.js`. Components never call `fetch` directly, making the base URL configurable and the components easier to test.

## Project Structure

```
smart-support-ai/
├── backend/
│   ├── core/               # Django project settings and URL config
│   ├── tickets/            # Main app: model, serializer, views, classify
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── components/     # TicketForm, TicketList, TicketCard, StatsDashboard
│   │   ├── api.js          # Centralized API service layer
│   │   └── App.jsx
│   ├── Dockerfile
│   └── package.json
├── docker-compose.yml
└── .env                    # Not committed — contains GROQ_API_KEY
```
