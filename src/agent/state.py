"""LangGraph agent state definition.

The state flows through nodes:
  receive_request → plan_files → fetch_files → reason → create_output
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any

from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field
from langchain_core.messages import BaseMessage


class AgentAction(str, Enum):
    """Possible output actions the agent can take."""

    INVESTIGATION_REPORT = "investigation_report"
    FEATURE_SKELETON = "feature_skeleton"
    BUG_ANALYSIS = "bug_analysis"
    PR_WITH_CHANGES = "pr_with_changes"


class PlannedFile(BaseModel):
    """A file the agent plans to retrieve, with a reason."""

    path: str = Field(description="Repo-relative path, e.g. /src/core/auth.py")
    reason: str = Field(description="Why this file is relevant to the request")


class AgentState(BaseModel):
    """Full state passed between LangGraph nodes.

    Uses Pydantic v2 for validation. The `messages` field uses
    LangGraph's built-in message accumulator.
    """

    # ── Input ────────────────────────────────────────────────────
    work_item_id: int | None = None
    request_text: str = ""
    request_type: str = ""  # "feature_request", "bug", "investigation", etc.

    # ── Work item context (populated by receive_request) ─────────
    work_item_title: str = ""
    work_item_description: str = ""
    work_item_tags: list[str] = Field(default_factory=list)
    work_item_comments: list[str] = Field(default_factory=list)

    # ── Parent work item (Epic/Feature) for scoping ─────────────
    parent_work_item_id: int | None = None
    parent_work_item_type: str = ""  # e.g. "Feature", "Epic"
    parent_work_item_title: str = ""
    parent_work_item_description: str = ""

    # ── Repository context ───────────────────────────────────────
    repo_tree_summary: str = ""  # condensed view of repo structure

    # ── Plan (populated by plan_files) ───────────────────────────
    planned_files: list[PlannedFile] = Field(default_factory=list)
    plan_reasoning: str = ""

    # ── Fetched content (populated by fetch_files) ───────────────
    fetched_files: list[tuple[str, str]] = Field(default_factory=list)
    fetch_errors: list[str] = Field(default_factory=list)

    # ── Reasoning result (populated by reason) ───────────────────
    analysis: str = ""
    recommended_action: AgentAction = AgentAction.INVESTIGATION_REPORT
    suggested_file_changes: dict[str, str] = Field(default_factory=dict)

    # ── Output (populated by create_output) ──────────────────────
    output_branch: str = ""
    output_pr_url: str = ""
    output_summary: str = ""

    # ── LangGraph message history ────────────────────────────────
    messages: Annotated[list[BaseMessage], add_messages] = Field(default_factory=list)

    # ── Control flow ─────────────────────────────────────────────
    error: str = ""
    needs_more_context: bool = False
    iteration: int = 0
    max_iterations: int = 3
