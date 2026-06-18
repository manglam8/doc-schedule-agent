"""
FastAPI entrypoint — wraps the LangGraph agent behind a /chat endpoint.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage

from agent import get_graph
from database import init_db, seed_db
from schemas import ChatRequest, ChatResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# In-memory thread store: thread_id → list[BaseMessage]
# For production replace with Redis / Postgres-backed checkpointer
_thread_store: dict[str, list[BaseMessage]] = {}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("Initialising database…")
    init_db()
    seed_db()
    logger.info("Database ready.")
    yield


app = FastAPI(
    title="Doctor Scheduling Agent API",
    description="Agentic workflow for booking clinic appointments via LangGraph.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Accepts a user message and returns the agent's response.

    Thread history is maintained in-process keyed by `thread_id`.
    Multiple users can have isolated conversations simultaneously.
    """
    thread_id = request.thread_id
    graph = get_graph()

    # Restore or initialise conversation history for this thread
    history: list[BaseMessage] = _thread_store.get(thread_id, [])
    history.append(HumanMessage(content=request.message))

    initial_state = {
        "messages": history,
        "selected_doctor_id": None,
        "selected_doctor_name": None,
        "last_tool_calls": [],
    }

    try:
        result = graph.invoke(initial_state)
    except Exception as exc:
        logger.exception("Agent invocation failed for thread %s", thread_id)
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}") from exc

    # Persist updated history (drop the injected SystemMessage at index 0)
    updated_messages: list[BaseMessage] = result["messages"]
    # Strip system message so it doesn't pile up across turns
    _thread_store[thread_id] = [m for m in updated_messages if m.type != "system"]

    # Extract the final AI text response
    ai_messages = [m for m in updated_messages if isinstance(m, AIMessage)]
    final_response = ai_messages[-1].content if ai_messages else "I'm sorry, I couldn't process that."

    # Handle structured content blocks (Anthropic returns list[dict] for tool-use responses)
    if isinstance(final_response, list):
        text_blocks = [b.get("text", "") for b in final_response if isinstance(b, dict) and b.get("type") == "text"]
        final_response = "\n".join(text_blocks).strip() or "Done."

    return ChatResponse(
        thread_id=thread_id,
        response=final_response,
        tool_calls_made=result.get("last_tool_calls", []),
    )


@app.delete("/chat/{thread_id}")
async def clear_thread(thread_id: str):
    """Clear conversation history for a thread (e.g., start a new session)."""
    _thread_store.pop(thread_id, None)
    return {"cleared": thread_id}
