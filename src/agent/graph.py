"""LangGraph graph definition.

Wires the agent nodes into a stateful, iterative graph:

  receive_request → plan_files → fetch_files → reason ──→ create_output
                        ↑                          |
                        └── (needs_more_context) ───┘

The "needs_more_context" loop allows the agent to request additional files
if the initial plan was insufficient (up to max_iterations).
"""

from __future__ import annotations

import logging
from functools import partial
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph import END, StateGraph

from src.agent.nodes import (
    create_output,
    fetch_files,
    plan_files,
    reason,
    receive_request,
)
from src.agent.state import AgentState
from src.clients.devops import AzureDevOpsClient
from src.config import Settings

logger = logging.getLogger(__name__)


def _should_loop_or_finish(state: AgentState) -> str:
    """Conditional edge: loop back to plan_files or proceed to output."""
    if state.needs_more_context and state.iteration < state.max_iterations:
        logger.info("Agent needs more context (iteration %d/%d) — looping", state.iteration, state.max_iterations)
        return "plan_files"
    return "create_output"


def build_graph(
    settings: Settings,
    devops: AzureDevOpsClient,
    llm: BaseChatModel,
) -> Any:
    """Construct and compile the LangGraph agent graph.

    Args:
        settings: Application settings.
        devops: Azure DevOps REST API client.
        llm: LangChain-compatible chat model.

    Returns:
        A compiled LangGraph that accepts AgentState.
    """
    # Bind dependencies into node functions using partial application
    _receive_request = partial(receive_request, devops=devops)
    _plan_files = partial(plan_files, llm=llm)
    _fetch_files = partial(fetch_files, devops=devops)
    _reason = partial(reason, llm=llm, max_context_tokens=settings.max_tokens_context)
    _create_output = partial(create_output, devops=devops)

    # Build the graph
    graph = StateGraph(AgentState)

    graph.add_node("receive_request", _receive_request)
    graph.add_node("plan_files", _plan_files)
    graph.add_node("fetch_files", _fetch_files)
    graph.add_node("reason", _reason)
    graph.add_node("create_output", _create_output)

    # Edges
    graph.set_entry_point("receive_request")

    graph.add_edge("receive_request", "plan_files")
    graph.add_edge("plan_files", "fetch_files")
    graph.add_edge("fetch_files", "reason")

    # Conditional: loop or finish
    graph.add_conditional_edges(
        "reason",
        _should_loop_or_finish,
        {
            "plan_files": "plan_files",
            "create_output": "create_output",
        },
    )

    graph.add_edge("create_output", END)

    compiled = graph.compile()
    logger.info("Agent graph compiled successfully")
    return compiled
