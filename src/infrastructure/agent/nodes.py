"""LangGraph node functions.

Each node receives the full AgentState and returns a partial dict of
fields to update.  The graph wires them together.

Flow:
  receive_request → plan_files → fetch_files → reason → create_output
                        ↑                          |
                        └── (needs_more_context) ───┘
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from src.infrastructure.agent.prompts import (
    PLAN_FILES_SYSTEM,
    PLAN_FILES_USER,
    REASON_SYSTEM,
    REASON_USER,
)
from src.core.agent.state import AgentAction, AgentState, PlannedFile
from src.infrastructure.clients.devops import AzureDevOpsClient
from src.core.utils.tokens import build_context_block

logger = logging.getLogger(__name__)


def _format_parent_block(state: AgentState) -> str:
    """Format parent work item context for prompts, or empty string if no parent."""
    if not state.parent_work_item_id:
        return ""
    parts = [
        "",
        f"Parent work item (#{state.parent_work_item_id}, {state.parent_work_item_type or 'Parent'}): {state.parent_work_item_title}",
    ]
    if state.parent_work_item_description:
        parts.append("Parent description:")
        parts.append(state.parent_work_item_description)
    parts.append("")
    return "\n".join(parts)


def _extract_json(text: str) -> dict[str, Any]:
    """Best-effort JSON extraction from LLM output (handles markdown fences)."""
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try extracting from markdown code block
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    # Last resort: find first { ... } block
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Could not extract JSON from LLM response:\n{text[:500]}")


# ── Node: receive_request ────────────────────────────────────────


async def receive_request(
    state: AgentState,
    devops: AzureDevOpsClient,
) -> dict[str, Any]:
    """Fetch work item details and repo tree to populate initial context."""
    updates: dict[str, Any] = {}

    # Fetch work item if an ID was provided
    if state.work_item_id:
        try:
            wi = await devops.get_work_item(state.work_item_id)
            fields = wi.get("fields", {})
            updates["work_item_title"] = fields.get("System.Title", "")
            updates["work_item_description"] = fields.get("System.Description", "")
            updates["work_item_tags"] = [
                t.strip() for t in fields.get("System.Tags", "").split(";") if t.strip()
            ]

            comments = await devops.get_work_item_comments(state.work_item_id)
            updates["work_item_comments"] = [c.get("text", "") for c in comments[:10]]

            # Fetch parent work item (e.g. Feature/Epic) for scoping and clearer feature definition
            parent = await devops.get_parent_work_item(state.work_item_id)
            if parent:
                pfields = parent.get("fields", {})
                updates["parent_work_item_id"] = parent.get("id")
                updates["parent_work_item_type"] = pfields.get("System.WorkItemType", "")
                updates["parent_work_item_title"] = pfields.get("System.Title", "")
                updates["parent_work_item_description"] = pfields.get("System.Description", "")

            logger.info("Loaded work item #%d: %s", state.work_item_id, updates["work_item_title"])
        except Exception as exc:
            logger.error("Failed to fetch work item #%s: %s", state.work_item_id, exc)
            updates["error"] = f"Could not fetch work item: {exc}"
            return updates

    # Fetch repo tree (top-level + one level deep for key folders)
    try:
        tree = await devops.get_repo_tree("/", depth=2)
        tree_lines = []
        for folder in sorted(tree.folders):
            tree_lines.append(f"  [dir]  {folder}")
        for f in sorted(tree.files):
            tree_lines.append(f"  [file] {f}")
        updates["repo_tree_summary"] = "\n".join(tree_lines)
        logger.info("Repo tree: %d entries", len(tree_lines))
    except Exception as exc:
        logger.error("Failed to fetch repo tree: %s", exc)
        updates["error"] = f"Could not fetch repo tree: {exc}"

    return updates


# ── Node: plan_files ─────────────────────────────────────────────


async def plan_files(
    state: AgentState,
    llm: Any,
) -> dict[str, Any]:
    """Use the LLM to decide which files to retrieve."""
    system_msg = PLAN_FILES_SYSTEM.format(repo_tree=state.repo_tree_summary)
    user_msg = PLAN_FILES_USER.format(
        work_item_id=state.work_item_id or "N/A",
        title=state.work_item_title or state.request_text[:100],
        description=state.work_item_description,
        tags=", ".join(state.work_item_tags) if state.work_item_tags else "none",
        comments="\n".join(state.work_item_comments[:5]) if state.work_item_comments else "none",
        parent_block=_format_parent_block(state),
        request_text=state.request_text,
    )

    messages = [SystemMessage(content=system_msg), HumanMessage(content=user_msg)]
    response = await llm.ainvoke(messages)
    raw = response.content

    try:
        data = _extract_json(raw)
        planned = [
            PlannedFile(path=f["path"], reason=f.get("reason", ""))
            for f in data.get("files", [])
        ]
        reasoning = data.get("reasoning", "")
    except (ValueError, KeyError) as exc:
        logger.warning("Failed to parse plan_files response: %s", exc)
        planned = []
        reasoning = f"Parse error — raw response: {raw[:300]}"

    logger.info("Planned %d files to fetch", len(planned))
    return {
        "planned_files": planned,
        "plan_reasoning": reasoning,
    }


# ── Node: fetch_files ───────────────────────────────────────────


async def fetch_files(
    state: AgentState,
    devops: AzureDevOpsClient,
) -> dict[str, Any]:
    """Fetch the planned files from Azure DevOps Git."""
    paths = [f.path for f in state.planned_files]
    if not paths:
        return {"error": "No files planned for retrieval"}

    items = await devops.get_files_batch(paths)
    fetched = [(item.path, item.content) for item in items]
    errors = [p for p in paths if p not in {item.path for item in items}]

    logger.info("Fetched %d files, %d errors", len(fetched), len(errors))
    return {
        "fetched_files": fetched,
        "fetch_errors": [f"Could not fetch: {p}" for p in errors],
    }


# ── Node: reason ─────────────────────────────────────────────────


async def reason(
    state: AgentState,
    llm: Any,
    max_context_tokens: int,
) -> dict[str, Any]:
    """Analyze fetched files against the work item and produce a report."""
    context_block = build_context_block(state.fetched_files, max_context_tokens)

    system_msg = REASON_SYSTEM
    user_msg = REASON_USER.format(
        work_item_id=state.work_item_id or "N/A",
        title=state.work_item_title or state.request_text[:100],
        description=state.work_item_description or state.request_text,
        parent_block=_format_parent_block(state),
        request_type=state.request_type or "investigation",
        context_block=context_block,
    )

    messages = [SystemMessage(content=system_msg), HumanMessage(content=user_msg)]
    response = await llm.ainvoke(messages)
    raw = response.content

    try:
        data = _extract_json(raw)
        analysis = data.get("analysis", raw)
        action_str = data.get("recommended_action", "investigation_report")
        try:
            action = AgentAction(action_str)
        except ValueError:
            action = AgentAction.INVESTIGATION_REPORT
        suggested = data.get("suggested_file_changes", {})
    except (ValueError, KeyError):
        analysis = raw
        action = AgentAction.INVESTIGATION_REPORT
        suggested = {}

    # Check if we need more context (agent self-assessment)
    needs_more = "NEED_MORE_CONTEXT" in analysis and state.iteration < state.max_iterations

    return {
        "analysis": analysis,
        "recommended_action": action,
        "suggested_file_changes": suggested,
        "needs_more_context": needs_more,
        "iteration": state.iteration + 1,
    }


# ── Node: create_output ─────────────────────────────────────────


async def create_output(
    state: AgentState,
    devops: AzureDevOpsClient,
) -> dict[str, Any]:
    """Post the agent's findings. Always appends — never overwrites.

    Default behaviour (report_only / INVESTIGATION_REPORT):
        Post the analysis as a work item comment. Existing work item
        content is never modified or removed.

    When branch/PR is requested:
        Also creates a branch, pushes files, and opens a PR.
        The report is still appended as a comment for traceability.
    """
    updates: dict[str, Any] = {}
    work_id = state.work_item_id or 0
    report_md = _build_markdown_report(state)

    # Always append the report as a work item comment (safe, append-only)
    if work_id:
        try:
            comment_html = _markdown_to_html_comment(report_md, state)
            await devops.add_work_item_comment(work_id, comment_html)
            logger.info("Posted agent report as comment on WI #%d", work_id)
        except Exception as exc:
            logger.warning("Failed to post comment on WI #%d: %s", work_id, exc)

    if state.recommended_action == AgentAction.INVESTIGATION_REPORT:
        updates["output_summary"] = state.analysis
        return updates

    # For feature skeletons, bug fixes, or PR-with-changes: create a branch
    branch_prefix = devops._settings.branch_prefix
    branch_name = f"{branch_prefix}/{state.recommended_action.value}/{work_id}"

    try:
        await devops.create_branch(branch_name)
        updates["output_branch"] = branch_name

        files_to_push: dict[str, str] = {}

        report_path = f"/docs/agent-reports/{work_id}-analysis.md"
        files_to_push[report_path] = report_md

        for path, content in state.suggested_file_changes.items():
            files_to_push[path] = content

        if files_to_push:
            await devops.push_files(
                branch_name,
                files_to_push,
                commit_message=f"Agent: {state.recommended_action.value} for #{work_id}\n\n{state.work_item_title}",
            )

        pr = await devops.create_pull_request(
            source_branch=branch_name,
            title=f"[Agent] {state.work_item_title or 'Investigation'} (#{work_id})",
            description=_build_pr_description(state),
            work_item_ids=[work_id] if work_id else None,
        )
        pr_url = pr.get("url", "")
        updates["output_pr_url"] = pr_url
        updates["output_summary"] = f"Created PR: {pr_url}\n\n{state.analysis}"

        logger.info("Output complete: branch=%s, PR=%s", branch_name, pr_url)

    except Exception as exc:
        logger.error("Failed to create output: %s", exc)
        updates["error"] = f"Output creation failed: {exc}"
        updates["output_summary"] = state.analysis

    return updates


# ── Helpers ──────────────────────────────────────────────────────


def _build_markdown_report(state: AgentState) -> str:
    """Build a markdown investigation report."""
    sections = [
        f"# Agent Analysis: #{state.work_item_id}",
        f"**Title:** {state.work_item_title}",
        f"**Type:** {state.request_type}",
        f"**Action:** {state.recommended_action.value}",
        "",
        "## Plan",
        f"**Reasoning:** {state.plan_reasoning}",
        "",
        "**Files reviewed:**",
    ]
    for pf in state.planned_files:
        sections.append(f"- `{pf.path}` — {pf.reason}")

    sections.extend([
        "",
        "## Analysis",
        state.analysis,
    ])

    if state.suggested_file_changes:
        sections.extend([
            "",
            "## Suggested Changes",
        ])
        for path in state.suggested_file_changes:
            sections.append(f"- `{path}`")

    if state.fetch_errors:
        sections.extend([
            "",
            "## Fetch Errors",
        ])
        for err in state.fetch_errors:
            sections.append(f"- {err}")

    return "\n".join(sections)


def _markdown_to_html_comment(report_md: str, state: AgentState) -> str:
    """Convert the markdown report to an HTML comment for the work item.

    Azure DevOps work item comments support HTML. We wrap the report
    in a clear header so it's easy to identify agent output in history.
    """
    escaped = (
        report_md
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    lines = escaped.split("\n")
    html_lines = []
    for line in lines:
        if line.startswith("# "):
            html_lines.append(f"<h3>{line[2:]}</h3>")
        elif line.startswith("## "):
            html_lines.append(f"<h4>{line[3:]}</h4>")
        elif line.startswith("- "):
            html_lines.append(f"<li>{line[2:]}</li>")
        elif line.startswith("**"):
            html_lines.append(f"<p><b>{line.strip('*')}</b></p>")
        elif line.strip():
            html_lines.append(f"<p>{line}</p>")
        else:
            html_lines.append("<br/>")

    body = "\n".join(html_lines)
    return (
        f'<div style="border-left:3px solid #4a148c;padding-left:12px;margin:8px 0">'
        f"<p><b>🤖 Agent Report</b> — <i>appended by DevOps Agent (iteration {state.iteration})</i></p>"
        f"{body}"
        f"</div>"
    )


def _build_pr_description(state: AgentState) -> str:
    """Build a PR description in markdown."""
    lines = [
        "## Agent-Generated Pull Request",
        "",
        f"**Work Item:** #{state.work_item_id}",
        f"**Action:** {state.recommended_action.value}",
        "",
        "### Summary",
        state.analysis[:2000] if state.analysis else "See attached report.",
        "",
        "### Files Changed",
    ]
    for path in state.suggested_file_changes:
        lines.append(f"- `{path}`")

    lines.extend([
        "",
        "---",
        "*This PR was generated by the DevOps Agent using targeted code retrieval.*",
    ])
    return "\n".join(lines)
