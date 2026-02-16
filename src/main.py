"""CLI entry point for the Azure DevOps Agent.

Usage:
    # Investigate a work item
    devops-agent investigate --work-item 1234

    # Investigate with extra context
    devops-agent investigate --work-item 1234 --context "Focus on auth flow"

    # Free-form request (no work item)
    devops-agent request --text "How does the payment module handle retries?"

    # Report only (no branch/PR creation)
    devops-agent investigate --work-item 1234 --report-only
"""

from __future__ import annotations

import asyncio
import logging
import sys

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.markdown import Markdown
from rich.panel import Panel

from src.agent.graph import build_graph
from src.agent.state import AgentState
from src.clients.devops import AzureDevOpsClient
from src.clients.llm import get_chat_model
from src.config import get_settings

app = typer.Typer(
    name="devops-agent",
    help="Intelligent Azure DevOps agent — targeted retrieval, not full repo cloning.",
    add_completion=False,
)
console = Console()


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


async def _run_agent(
    work_item_id: int | None,
    request_text: str,
    request_type: str,
    report_only: bool,
) -> None:
    settings = get_settings()
    _setup_logging(settings.log_level)

    console.print(Panel("[bold]Azure DevOps Agent[/bold]", subtitle="Targeted Retrieval"))

    # Initialize clients
    devops = AzureDevOpsClient(settings)
    llm = get_chat_model(settings)

    try:
        # Build the graph
        graph = build_graph(settings, devops, llm)

        # Prepare initial state
        initial_state = AgentState(
            work_item_id=work_item_id,
            request_text=request_text,
            request_type=request_type,
        )

        # If report-only, override max action
        if report_only:
            initial_state.max_iterations = 1

        console.print(f"\n[dim]Work item:[/dim] #{work_item_id or 'N/A'}")
        console.print(f"[dim]Request type:[/dim] {request_type}")
        console.print(f"[dim]Context:[/dim] {request_text[:200] or '(from work item)'}\n")

        # Execute the graph
        with console.status("[bold green]Agent working..."):
            result = await graph.ainvoke(initial_state)

        # Display results
        console.print()
        if isinstance(result, dict):
            state = AgentState(**result)
        else:
            state = result

        if state.error:
            console.print(f"[bold red]Error:[/bold red] {state.error}")
            sys.exit(1)

        if state.output_summary:
            console.print(Panel(Markdown(state.output_summary), title="Agent Report", border_style="green"))

        if state.output_branch:
            console.print(f"\n[bold]Branch:[/bold] {state.output_branch}")
        if state.output_pr_url:
            console.print(f"[bold]PR:[/bold] {state.output_pr_url}")

        # Show plan summary
        if state.planned_files:
            console.print(f"\n[dim]Files reviewed: {len(state.planned_files)}[/dim]")
            for pf in state.planned_files:
                console.print(f"  [dim]• {pf.path}[/dim]")

        if state.fetch_errors:
            console.print("\n[yellow]Fetch warnings:[/yellow]")
            for err in state.fetch_errors:
                console.print(f"  [yellow]• {err}[/yellow]")

    finally:
        await devops.close()


@app.command()
def investigate(
    work_item: int = typer.Option(..., "--work-item", "-w", help="Azure DevOps work item ID"),
    context: str = typer.Option("", "--context", "-c", help="Additional context for the agent"),
    request_type: str = typer.Option("investigation", "--type", "-t", help="Request type: investigation, feature_request, bug"),
    report_only: bool = typer.Option(True, "--report-only", "-r", help="Report only (default). Use --no-report-only to create branch/PR"),
) -> None:
    """Investigate a work item: fetch relevant code, analyze, and post findings as a comment."""
    asyncio.run(_run_agent(work_item, context, request_type, report_only))


@app.command()
def request(
    text: str = typer.Option(..., "--text", "-t", help="Free-form request text"),
    request_type: str = typer.Option("investigation", "--type", help="Request type"),
    report_only: bool = typer.Option(True, "--report-only", "-r", help="Generate report without creating branch/PR"),
) -> None:
    """Submit a free-form request without a specific work item."""
    asyncio.run(_run_agent(None, text, request_type, report_only))


@app.command()
def trigger(
    work_item: int = typer.Option(..., "--work-item", "-w", help="Work item ID to trigger the pipeline for"),
    pipeline_id: int = typer.Option(..., "--pipeline-id", "-p", help="Azure DevOps pipeline ID"),
    context: str = typer.Option("", "--context", "-c", help="Additional context"),
    request_type: str = typer.Option("investigation", "--type", "-t", help="Request type"),
    report_only: bool = typer.Option(True, "--report-only", "-r", help="Report only (default). Use --no-report-only to create branch/PR"),
) -> None:
    """Trigger the agent pipeline via Azure DevOps REST API.

    This is the same API call that service hooks and Power Automate use.
    No separate server needed — just credentials + one POST.
    """
    from src.clients.trigger import PipelineTrigger

    async def _trigger() -> None:
        settings = get_settings()
        _setup_logging(settings.log_level)
        client = PipelineTrigger(settings, pipeline_id)

        console.print(f"[dim]Triggering pipeline {pipeline_id} for WI #{work_item}...[/dim]")
        result = await client.trigger(
            work_item_id=work_item,
            request_type=request_type,
            additional_context=context,
            trigger_source="cli",
            report_only=report_only,
        )
        run_id = result.get("id")
        run_url = result.get("_links", {}).get("web", {}).get("href", "")
        console.print(f"[bold green]Pipeline run #{run_id} queued[/bold green]")
        if run_url:
            console.print(f"[dim]{run_url}[/dim]")

    asyncio.run(_trigger())


def main() -> None:
    """Entry point for direct `python -m src.main` execution."""
    app()


if __name__ == "__main__":
    main()
