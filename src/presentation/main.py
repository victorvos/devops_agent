"""CLI entry point for the Azure DevOps Agent.

Usage:
    # Investigate a work item (report-only by default)
    devops-agent investigate --work-item 1234

    # Investigate with extra context
    devops-agent investigate --work-item 1234 --context "Focus on auth flow"

    # Free-form request (no work item)
    devops-agent request --text "How does the payment module handle retries?"

    # Start the API server (Container App deployment)
    devops-agent serve
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

from src.infrastructure.agent.graph import build_graph
from src.core.agent.state import AgentState
from src.infrastructure.clients.devops import AzureDevOpsClient
from src.infrastructure.clients.llm import get_chat_model
from src.core.config import get_settings

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

    devops = AzureDevOpsClient(settings)
    llm = get_chat_model(settings)

    try:
        graph = build_graph(settings, devops, llm)

        initial_state = AgentState(
            work_item_id=work_item_id,
            request_text=request_text,
            request_type=request_type,
        )

        if report_only:
            initial_state.max_iterations = 1

        console.print(f"\n[dim]Work item:[/dim] #{work_item_id or 'N/A'}")
        console.print(f"[dim]Request type:[/dim] {request_type}")
        console.print(f"[dim]Context:[/dim] {request_text[:200] or '(from work item)'}\n")

        with console.status("[bold green]Agent working..."):
            try:
                # Wait up to 5 minutes (300 seconds) for the agent to complete
                result = await asyncio.wait_for(graph.ainvoke(initial_state), timeout=300.0)
            except asyncio.TimeoutError:
                console.print("[bold red]Error:[/bold red] Agent invocation timed out after 5 minutes.")
                sys.exit(1)

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
def serve(
    host: str = typer.Option("0.0.0.0", "--host", "-h", help="Bind address"),
    port: int = typer.Option(8000, "--port", "-p", help="Listen port"),
) -> None:
    """Start the FastAPI server (for Container App deployment)."""
    import uvicorn

    uvicorn.run("src.presentation.api:app", host=host, port=port)


def main() -> None:
    """Entry point for direct ``python -m src.presentation.main`` execution."""
    app()


if __name__ == "__main__":
    main()
