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
    report_only: bool = typer.Option(False, "--report-only", "-r", help="Generate report without creating branch/PR"),
) -> None:
    """Investigate a work item: fetch relevant code, analyze, and optionally create a PR."""
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
def serve(
    host: str = typer.Option("0.0.0.0", "--host", "-h", help="Bind address"),
    port: int = typer.Option(8000, "--port", "-p", help="Bind port"),
) -> None:
    """Start the webhook receiver server (Azure DevOps + Teams triggers)."""
    import uvicorn

    console.print(Panel("[bold]DevOps Agent — Webhook Receiver[/bold]", subtitle=f"{host}:{port}"))
    console.print("[dim]Endpoints:[/dim]")
    console.print("  POST /webhooks/devops  — Azure DevOps service hook (@agent in comments)")
    console.print("  POST /webhooks/teams   — MS Teams bot (@agent in channel)")
    console.print("  POST /webhooks/trigger — Manual / generic trigger")
    console.print("  GET  /health           — Health check\n")

    uvicorn.run("src.webhooks.receiver:app", host=host, port=port, log_level="info")


def main() -> None:
    """Entry point for direct `python -m src.main` execution."""
    app()


if __name__ == "__main__":
    main()
