# Doctor Scheduling Agent

A production-ready, portfolio-grade **Agentic Workflow** that lets patients book clinic appointments through a natural-language chat interface. Built with LangGraph, FastAPI, SQLite, and Streamlit.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Patient Browser                          │
│                    Streamlit (app.py :8501)                     │
└──────────────────────────────┬──────────────────────────────────┘
                               │ HTTP POST /chat
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                    FastAPI (main.py :8000)                       │
│  • In-memory thread store (thread_id → message history)         │
│  • Lifespan hook: init_db() + seed_db() on startup              │
└──────────────────────────────┬──────────────────────────────────┘
                               │ graph.invoke(state)
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                   LangGraph State Machine (agent.py)            │
│                                                                 │
│   START                                                         │
│     │                                                           │
│     ▼                                                           │
│  ┌──────────┐   tool_calls?   ┌────────────┐                   │
│  │ llm_node │ ──── YES ──────▶│ tool_node  │                   │
│  │(Claude)  │◀────────────────│(ToolNode)  │                   │
│  └──────────┘                 └────────────┘                   │
│     │ NO tool_calls                                             │
│     ▼                                                           │
│    END                                                          │
└──────────────────────────────┬──────────────────────────────────┘
                               │ SessionLocal()
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                 Tools Layer (tools.py)                          │
│                                                                 │
│  get_doctors_by_specialty()   ──▶  SELECT FROM doctors          │
│  check_doctor_availability()  ──▶  SELECT FROM appointments     │
│  book_appointment_slot()      ──▶  INSERT INTO appointments     │
│                                    (+ IntegrityError guard)     │
└──────────────────────────────┬──────────────────────────────────┘
                               │ SQLAlchemy ORM
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│              SQLite (clinic.db) — WAL mode enabled              │
│                                                                 │
│  doctors          appointments                                  │
│  ─────────        ──────────────────────────────                │
│  id               id                                            │
│  name             doctor_id  (FK → doctors.id)                  │
│  specialty        patient_name                                  │
│  weekly_schedule  patient_phone                                 │
│  (JSON)           start_time  (UTC DateTime)                    │
│                   status      (CONFIRMED/PENDING/CANCELLED)     │
│                   ★ UNIQUE(doctor_id, start_time)               │
└─────────────────────────────────────────────────────────────────┘
```

---

## Agentic Workflow — How It Works

The graph follows a **ReAct-style loop**:

1. **User message** arrives via `/chat`.
2. **`llm_node`** — Claude reads the system prompt + full conversation history and decides whether to respond directly or invoke a tool.
3. **`should_continue` edge** — if the AI message contains `tool_calls`, route to `tool_node`; otherwise terminate.
4. **`tool_node`** — LangGraph's built-in `ToolNode` dispatches each tool call, appends `ToolMessage` results to state.
5. Loop back to **`llm_node`** so the LLM can synthesise results and decide whether another tool call is needed.

A typical booking conversation runs **3 LLM turns** with **3 tool invocations**:
```
Turn 1: LLM → get_doctors_by_specialty("Dermatologist")
Turn 2: LLM → check_doctor_availability(doctor_id=2, date="2026-06-19")
Turn 3: LLM → book_appointment_slot(doctor_id=2, patient_name=…)
Turn 4: LLM → Final confirmation text → END
```

---

## Concurrency & Double-Booking Prevention

Double-bookings are prevented at **two independent layers**:

### Layer 1 — Database Unique Constraint
```sql
UNIQUE(doctor_id, start_time)   -- in appointments table
```
Even if two requests race past application-level checks simultaneously, only one `INSERT` will succeed. The other raises `sqlalchemy.exc.IntegrityError`.

### Layer 2 — Agent Recovery Logic
When `book_appointment_slot` catches an `IntegrityError`, it:
1. Rolls back the failed transaction.
2. Immediately calls `check_doctor_availability` to fetch the freshest open slots.
3. Returns a structured JSON error with `alternative_slots`.
4. The LLM node reads this and **apologises + offers alternatives** rather than crashing.

### Layer 3 — SQLite WAL Mode
```python
cursor.execute("PRAGMA journal_mode=WAL")
```
Write-Ahead Logging allows concurrent readers during a write, reducing lock contention in the SQLite tier.

---

## Doctors (Seed Data)

| Doctor | Specialty | Working Days | Hours |
|--------|-----------|-------------|-------|
| Dr. Sarah Chen | Cardiologist | Mon–Fri | 09:00–17:00 UTC |
| Dr. Marcus Adams | Dermatologist | Tue & Thu | 10:00–16:00 UTC |
| Dr. Priya Patel | General Practitioner | Mon, Wed, Fri | 08:00–15:00 UTC |

---

## Setup & Running

### 1. Prerequisites
- Python 3.11+
- A Google AI Studio API key (free tier supports 60 RPM for Gemini 2.5 Flash Lite)

### 2. Install
```bash
git clone <repo>
cd doc-schedule-agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure
```bash
cp .env.example .env
# Edit .env and set GOOGLE_API_KEY=AIza...
```

### 4. Run the API
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```
The database is created and seeded automatically on first startup.

### 5. Run the Streamlit UI
```bash
# In a second terminal
streamlit run app.py
```
Open http://localhost:8501 in your browser.

### 6. Run Tests
```bash
pytest tests/ -v
```

---

## Project Structure

```
doc-schedule-agent/
├── config.py          # Pydantic-settings for env vars + LLM config
├── database.py        # Engine, session factory, seed data
├── models.py          # SQLAlchemy ORM models (Doctor, Appointment)
├── schemas.py         # Pydantic request/response schemas
├── tools.py           # LLM-callable tools (specialty search, availability, booking)
├── agent.py           # LangGraph state machine + system prompt
├── main.py            # FastAPI app + /chat endpoint
├── app.py             # Streamlit patient chat UI
├── requirements.txt
├── .env.example
└── tests/
    ├── conftest.py            # In-memory DB fixtures
    ├── test_tools.py          # Tool unit tests (17 cases)
    └── test_agent_routing.py  # Graph structure tests
```

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| LangGraph over bare LangChain | Explicit state machine gives deterministic routing; easy to extend with human-in-the-loop nodes |
| UTC-only datetimes | Eliminates timezone ambiguity at the storage layer; display conversion is a UI concern |
| Unique constraint at DB level | Application locks are not enough under concurrent load; DB constraints are the last line of defence |
| Tool results as plain JSON strings | LLMs parse structured text reliably; avoids schema coupling between tools and agent prompts |
| In-memory thread store | Sufficient for demo/portfolio; replace with `langgraph.checkpoint.sqlite.SqliteSaver` for production persistence |
| `langchain-google-genai` | Gemini 2.5 Flash Lite offers fast, cost-effective tool-use with strong schema adherence |
| `InMemoryRateLimiter` | Enforces the 60 RPM cap (1 req/s, bucket size 1) at the LangChain layer — no bursting above quota |
