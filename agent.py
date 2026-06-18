"""
LangGraph State Machine for the Doctor Scheduling Agent.

Graph topology:
    START → llm_node → (tool_node → llm_node)* → END

The router edge after llm_node:
  - If the LLM emitted tool_calls  → route to tool_node
  - Otherwise                       → route to END
"""
from __future__ import annotations

import logging
from typing import Annotated, Literal, Optional

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.rate_limiters import InMemoryRateLimiter
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from config import get_settings
from tools import ALL_TOOLS

logger = logging.getLogger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    """Shared mutable state threaded through every graph node."""
    messages: Annotated[list[BaseMessage], add_messages]
    # Lightweight metadata tracked across turns (not persisted to DB)
    selected_doctor_id: Optional[int]
    selected_doctor_name: Optional[str]
    last_tool_calls: list[str]


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are CareBot, a professional and empathetic AI receptionist for City Health Clinic.

Your role is to help patients find the right doctor and book appointments efficiently and accurately.

## Core Rules — NEVER violate these
1. **Never invent or guess appointment slots.** Always call `check_doctor_availability` before
   presenting times to a patient. If you have not called this tool, you do not know which slots
   are free.
2. **Never confirm a booking without calling `book_appointment_slot`.** Verbally agreeing to a
   time is not a booking. The tool call is mandatory.
3. If `book_appointment_slot` returns `"success": false` with `alternative_slots`, immediately
   relay the alternatives to the patient with an apology and offer to rebook.

## Conversation Flow
1. Greet the patient warmly and ask about their health concern.
2. Based on their description, infer the required specialty:
   - Skin rash / acne / eczema / hair loss → Dermatologist
   - Chest pain / heart palpitations / blood pressure → Cardiologist
   - Fever / cold / general checkup / anything else → General Practitioner
3. Call `get_doctors_by_specialty` to retrieve available doctors.
4. Present doctor options with their working days.
5. Ask the patient for their preferred date, then call `check_doctor_availability`.
6. Present the open slots clearly (e.g., "09:00, 09:30, 10:00").
7. Collect: patient full name, phone number, and preferred slot.
8. Call `book_appointment_slot` and confirm the booking details.

## Tone
- Warm, professional, concise.
- Never ask for information you already have.
- If a patient seems anxious about a symptom, acknowledge their concern briefly before proceeding.
- Always end a successful booking with a clear summary: doctor name, date, time, and a reminder
  to arrive 10 minutes early.

Today's date context: use ISO format (YYYY-MM-DD) when calling tools.
"""


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def build_llm() -> ChatGoogleGenerativeAI:
    # 60 RPM → 1 request per second; max_bucket_size=1 prevents bursting above the cap
    rate_limiter = InMemoryRateLimiter(
        requests_per_second=settings.llm_rate_limit_rpm / 60,
        check_every_n_seconds=0.1,
        max_bucket_size=1,
    )
    return ChatGoogleGenerativeAI(
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        max_output_tokens=settings.llm_max_tokens,
        google_api_key=settings.google_api_key,
        rate_limiter=rate_limiter,
    ).bind_tools(ALL_TOOLS)


def llm_node(state: AgentState) -> dict:
    """
    Primary reasoning node.
    Prepends the system prompt on the first turn, then calls the LLM.
    """
    messages = state["messages"]

    # Inject system prompt as the very first message if not already present
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + list(messages)

    llm = build_llm()
    response: AIMessage = llm.invoke(messages)

    # Track which tools were called in this turn
    tool_calls_made = [tc["name"] for tc in (response.tool_calls or [])]

    return {
        "messages": [response],
        "last_tool_calls": tool_calls_made,
        # Preserve doctor context across turns (updated downstream via tool results)
        "selected_doctor_id": state.get("selected_doctor_id"),
        "selected_doctor_name": state.get("selected_doctor_name"),
    }


def should_continue(state: AgentState) -> Literal["tools", "__end__"]:
    """Route: if the last AI message has tool calls, go to tool execution; else done."""
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return "__end__"


# ---------------------------------------------------------------------------
# Graph compilation
# ---------------------------------------------------------------------------

def build_graph():
    tool_node = ToolNode(ALL_TOOLS)

    graph = StateGraph(AgentState)
    graph.add_node("llm", llm_node)
    graph.add_node("tools", tool_node)

    graph.add_edge(START, "llm")
    graph.add_conditional_edges("llm", should_continue, {"tools": "tools", "__end__": END})
    graph.add_edge("tools", "llm")  # Always return to LLM after tool execution

    return graph.compile()


# Singleton compiled graph (lazy-initialised on first import)
_compiled_graph = None


def get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph
