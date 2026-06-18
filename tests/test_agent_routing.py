"""
Lightweight tests for agent graph structure and state routing.
No LLM calls — verifies that the graph compiles and the conditional edge
function routes correctly based on message shape.
"""
import pytest
from unittest.mock import MagicMock
from langchain_core.messages import AIMessage, HumanMessage

from agent import should_continue, build_graph, AgentState


def _make_state(last_message) -> AgentState:
    return {
        "messages": [last_message],
        "selected_doctor_id": None,
        "selected_doctor_name": None,
        "last_tool_calls": [],
    }


class TestShouldContinue:
    def test_routes_to_tools_when_tool_calls_present(self):
        ai_msg = AIMessage(
            content="",
            tool_calls=[{"name": "check_doctor_availability", "args": {}, "id": "tc1", "type": "tool_call"}],
        )
        assert should_continue(_make_state(ai_msg)) == "tools"

    def test_routes_to_end_when_no_tool_calls(self):
        ai_msg = AIMessage(content="Your appointment is confirmed!")
        assert should_continue(_make_state(ai_msg)) == "__end__"

    def test_routes_to_end_for_human_message(self):
        # Edge case: if somehow a HumanMessage is last, don't call tools
        human_msg = HumanMessage(content="Hello")
        assert should_continue(_make_state(human_msg)) == "__end__"


class TestGraphCompilation:
    def test_graph_compiles_without_error(self):
        graph = build_graph()
        assert graph is not None

    def test_graph_has_expected_nodes(self):
        graph = build_graph()
        node_names = set(graph.nodes.keys())
        assert "llm" in node_names
        assert "tools" in node_names
