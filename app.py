"""
Streamlit patient-facing chat UI.

Run with:  streamlit run app.py
BACKEND_URL is read from (in priority order):
  1. st.secrets["BACKEND_URL"]  (Streamlit Cloud secrets)
  2. BACKEND_URL environment variable
  3. Default: http://localhost:8000
"""
from __future__ import annotations

import uuid
import httpx
import streamlit as st

from config import get_settings

settings = get_settings()

# Streamlit Cloud secrets override the env-var default
_BACKEND_URL: str = st.secrets.get("BACKEND_URL", settings.backend_url)

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="City Health Clinic — Book an Appointment",
    page_icon="🏥",
    layout="centered",
)

st.title("🏥 City Health Clinic")
st.caption("Chat with CareBot to find a doctor and book your appointment.")

# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------
if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())

if "messages" not in st.session_state:
    st.session_state.messages = [
        {
            "role": "assistant",
            "content": (
                "Hello! I'm CareBot, your clinic assistant. 😊\n\n"
                "What health concern brings you in today? "
                "I'll help you find the right specialist and book an appointment."
            ),
        }
    ]

if "user_id" not in st.session_state:
    st.session_state.user_id = str(uuid.uuid4())

# ---------------------------------------------------------------------------
# Render existing messages
# ---------------------------------------------------------------------------
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# ---------------------------------------------------------------------------
# Sidebar — session controls
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Session")
    st.code(f"Thread: {st.session_state.thread_id[:8]}…", language=None)

    if st.button("🔄 Start New Conversation"):
        # Clear server-side history
        try:
            httpx.delete(
                f"{_BACKEND_URL}/chat/{st.session_state.thread_id}",
                timeout=5,
            )
        except Exception:
            pass
        st.session_state.thread_id = str(uuid.uuid4())
        st.session_state.messages = [
            {
                "role": "assistant",
                "content": (
                    "Hello again! I'm CareBot. What brings you in today?"
                ),
            }
        ]
        st.rerun()

    st.divider()
    st.markdown("**Clinic Hours**")
    st.markdown("- Dr. Chen (Cardiology): Mon–Fri")
    st.markdown("- Dr. Adams (Dermatology): Tue & Thu")
    st.markdown("- Dr. Patel (General): Mon, Wed, Fri")

# ---------------------------------------------------------------------------
# Chat input
# ---------------------------------------------------------------------------
if prompt := st.chat_input("Type your message…"):
    # Render user message immediately
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Call the FastAPI backend
    with st.chat_message("assistant"):
        with st.spinner("CareBot is thinking…"):
            try:
                resp = httpx.post(
                    f"{_BACKEND_URL}/chat",
                    json={
                        "user_id": st.session_state.user_id,
                        "thread_id": st.session_state.thread_id,
                        "message": prompt,
                    },
                    timeout=60,
                )
                resp.raise_for_status()
                data = resp.json()
                reply = data["response"]

                # Show tool activity as a subtle expander
                if data.get("tool_calls_made"):
                    with st.expander("🔧 Tools used", expanded=False):
                        st.write(", ".join(data["tool_calls_made"]))

            except httpx.ConnectError:
                reply = (
                    "⚠️ I can't reach the scheduling server right now. "
                    "Please make sure the API is running (`uvicorn main:app --reload`) "
                    "and try again."
                )
            except httpx.HTTPStatusError as exc:
                reply = f"⚠️ Server error ({exc.response.status_code}). Please try again."
            except Exception as exc:
                reply = f"⚠️ Unexpected error: {exc}"

        st.markdown(reply)

    st.session_state.messages.append({"role": "assistant", "content": reply})
