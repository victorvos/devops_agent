# Azure DevOps Agent

An intelligent agent that investigates Azure DevOps work items (feature requests, bugs, backlog items) using **targeted code retrieval** — never clones the full repo.

Built with [LangGraph](https://github.com/langchain-ai/langgraph), [Azure AI Foundry](https://ai.azure.com/) (GPT 5.3), [Azure Pipelines](https://learn.microsoft.com/en-us/azure/devops/pipelines/), and [uv](https://docs.astral.sh/uv/).

Triggered via **`@agent`** mentions in Azure DevOps work item comments or Microsoft Teams.

## Table of Contents

- [Why targeted retrieval?](#why-targeted-retrieval)
- [Architecture](#architecture)
- [Setup](#setup)
- [Usage](#usage)
- [Pipeline templates](#pipeline-templates)
- [Cost efficiency](#cost-efficiency)
- [Development](#development)
- [Project structure](#project-structure)

---

## Why Targeted Retrieval?

```mermaid
graph LR
    subgraph "Traditional (expensive)"
        A[Full repo clone] -->|2-10 min| B[Stuff into LLM]
        B -->|100k-500k tokens| C[Poor quality output]
    end

    subgraph "This agent (cost-efficient)"
        D["Read work item"] -->|seconds| E["Plan 5-20 files"]
        E -->|Git REST API| F["Fetch only relevant files"]
        F -->|5k-30k tokens| G[High quality analysis]
    end

    %% Styling
    style A fill:#f44336,stroke:#c62828,stroke-width:2px,color:#000000
    style B fill:#fff8e1,stroke:#e65100,stroke-width:2px,color:#000000
    style C fill:#f44336,stroke:#c62828,stroke-width:2px,color:#000000
    style D fill:#e1f5fe,stroke:#01579b,stroke-width:2px,color:#000000
    style E fill:#fff8e1,stroke:#e65100,stroke-width:2px,color:#000000
    style F fill:#fff8e1,stroke:#e65100,stroke-width:2px,color:#000000
    style G fill:#4caf50,stroke:#2e7d32,stroke-width:2px,color:#000000
```

| Metric | Full Repo Approach | This Agent |
|--------|-------------------|------------|
| Tokens per request | 100k-500k+ | 5k-30k |
| Cost per request (GPT 5.3) | $0.50-$5.00+ | $0.02-$0.20 |
| Pipeline minutes | 5-15 min | 30s-2 min |
| Azure DevOps API calls | Hundreds | 10-30 |
| Quality of analysis | Low (needle-in-haystack) | High (focused context) |

---

## Architecture

### End-to-end flow

```mermaid
flowchart TB
    subgraph triggers["Trigger Sources"]
        T1["Azure DevOps service hook\n→ Power Automate → Pipeline API"]
        T2["MS Teams @agent\n→ Power Automate → Pipeline API"]
        T3["CLI: devops-agent trigger"]
        T4["Pipeline UI: manual run"]
    end

    subgraph pipeline["Azure Pipeline — python:3.12-slim"]
        direction TB
        P1["Checkout agent code"]
        P2["uv sync via ProGet"]
        P3["Run agent"]
        P1 --> P2 --> P3
    end

    subgraph agent["LangGraph Agent"]
        direction TB
        N1["receive_request\nFetch work item + repo tree"]
        N2["plan_files\nLLM picks 5-20 relevant files"]
        N3["fetch_files\nGit REST API targeted"]
        N4["reason\nAnalyze code vs work item"]
        N5["create_output\nBranch + PR or report"]

        N1 --> N2 --> N3 --> N4
        N4 -->|"needs more context"| N2
        N4 -->|"done"| N5
    end

    AZ["Azure DevOps REST API"]
    LLM["Azure AI Foundry — GPT 5.3"]

    T1 -->|"POST pipelines/runs"| pipeline
    T2 -->|"POST pipelines/runs"| pipeline
    T3 -->|"POST pipelines/runs"| pipeline
    T4 --> pipeline
    P3 --> agent
    N1 <--> AZ
    N3 <--> AZ
    N5 <--> AZ
    N2 <--> LLM
    N4 <--> LLM

    %% Styling
    style T1 fill:#e1f5fe,stroke:#01579b,stroke-width:2px,color:#000000
    style T2 fill:#e1f5fe,stroke:#01579b,stroke-width:2px,color:#000000
    style T3 fill:#e1f5fe,stroke:#01579b,stroke-width:2px,color:#000000
    style T4 fill:#e1f5fe,stroke:#01579b,stroke-width:2px,color:#000000
    style P1 fill:#fff8e1,stroke:#e65100,stroke-width:2px,color:#000000
    style P2 fill:#fff8e1,stroke:#e65100,stroke-width:2px,color:#000000
    style P3 fill:#fff8e1,stroke:#e65100,stroke-width:2px,color:#000000
    style N1 fill:#f3e5f5,stroke:#4a148c,stroke-width:2px,color:#000000
    style N2 fill:#f3e5f5,stroke:#4a148c,stroke-width:2px,color:#000000
    style N3 fill:#f3e5f5,stroke:#4a148c,stroke-width:2px,color:#000000
    style N4 fill:#f3e5f5,stroke:#4a148c,stroke-width:2px,color:#000000
    style N5 fill:#f3e5f5,stroke:#4a148c,stroke-width:2px,color:#000000
    style AZ fill:#e1f5fe,stroke:#01579b,stroke-width:2px,color:#000000
    style LLM fill:#e1f5fe,stroke:#01579b,stroke-width:2px,color:#000000
```

### LangGraph agent detail

```mermaid
flowchart TB
    START(( )) --> N1

    N1["receive_request<br/>Fetch work item fields, comments,<br/>tags via REST API. Get repo tree."]
    N1 --> N2

    N2["plan_files<br/>LLM analyzes work item + repo<br/>structure to select 5-20 file paths."]
    N2 --> N3

    N3["fetch_files<br/>Download file contents via<br/>Azure DevOps Git Items API."]
    N3 --> N4

    N4["reason<br/>Analyze code against work item.<br/>Produce report + suggested changes."]
    N4 -->|"needs_more_context<br/>(up to 3 iterations)"| N2
    N4 -->|"done"| N5

    N5["create_output<br/>Create branch, push files,<br/>open PR — or return report only."]
    N5 --> END(( ))

    %% Styling
    style START fill:#000000,stroke:#000000,color:#000000
    style END fill:#000000,stroke:#000000,color:#000000
    style N1 fill:#f3e5f5,stroke:#4a148c,stroke-width:2px,color:#000000
    style N2 fill:#f3e5f5,stroke:#4a148c,stroke-width:2px,color:#000000
    style N3 fill:#f3e5f5,stroke:#4a148c,stroke-width:2px,color:#000000
    style N4 fill:#fff8e1,stroke:#e65100,stroke-width:2px,color:#000000
    style N5 fill:#4caf50,stroke:#2e7d32,stroke-width:2px,color:#000000
```

### Pipeline template reuse

```mermaid
graph TB
    subgraph "Pipeline Templates (pipelines/templates/)"
        TPY["python-setup.yml<br/>uv + ProGet sync<br/>(inside python:3.12-slim)"]
        TRA["run-agent.yml<br/>Execute agent + publish artifacts"]
    end

    subgraph "Pipelines"
        CI["ci.yml<br/>Lint + Test on PR"]
        MAN["azure-pipeline.yml<br/>Manual trigger"]
        WEB["webhook-trigger.yml<br/>@agent trigger"]
    end

    CI --> TPY
    MAN --> TPY
    MAN --> TRA
    WEB --> TPY
    WEB --> TRA

    %% Styling
    style TPY fill:#e1f5fe,stroke:#01579b,stroke-width:2px,color:#000000
    style TRA fill:#e1f5fe,stroke:#01579b,stroke-width:2px,color:#000000
    style CI fill:#fff8e1,stroke:#e65100,stroke-width:2px,color:#000000
    style MAN fill:#fff8e1,stroke:#e65100,stroke-width:2px,color:#000000
    style WEB fill:#fff8e1,stroke:#e65100,stroke-width:2px,color:#000000
```

---

## Setup

### Prerequisites

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)** (Python package manager)
- **[ProGet](https://inedo.com/proget)** PyPI feed (your org's private package index)
- **Azure DevOps** organization with a PAT that has: Code (Read & Write), Work Items (Read & Write), Pull Requests (Read & Write)
- **Azure AI Foundry** project with GPT 5.3 deployed (or another supported model)

### Installation

```bash
cd devops_agent

# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Configure ProGet credentials for uv
export UV_HTTP_BASIC_PROGET_USERNAME=api
export UV_HTTP_BASIC_PROGET_PASSWORD=your-proget-api-key

# Sync all dependencies from ProGet (production + dev)
uv sync --all-extras

# Or production only
uv sync --no-dev
```

> **Note:** The ProGet index URL is configured in `pyproject.toml` under `[[tool.uv.index]]`. Update it to match your organization's ProGet feed URL. Authentication uses the `UV_HTTP_BASIC_PROGET_*` environment variables, which uv automatically maps to the index named `proget`.

### Configuration

```bash
cp .env.example .env
```

Key settings in `.env`:

| Variable | Description | Example |
|----------|-------------|---------|
| `AZURE_DEVOPS_ORG_URL` | Your org URL | `https://dev.azure.com/contoso` |
| `AZURE_DEVOPS_PROJECT` | Project name | `MyProject` |
| `AZURE_DEVOPS_REPOSITORY` | Repo name | `backend-api` |
| `DEVOPS_AUTH_MODE` | `system_token` (pipeline) or `pat` (local) | `pat` |
| `AZURE_DEVOPS_PAT` | PAT — only needed when auth mode = `pat` | `xxxx...` |
| `AZURE_AI_FOUNDRY_ENDPOINT` | AI Foundry endpoint | `https://proj.services.ai.azure.com` |
| `AZURE_AI_FOUNDRY_API_KEY` | Foundry API key | `xxxx...` |
| `AZURE_AI_FOUNDRY_MODEL` | Model deployment | `gpt-5.3` |
| `AGENT_PIPELINE_ID` | Webhook-trigger pipeline ID | `42` |
| `PROGET_PYPI_URL` | ProGet PyPI feed URL | `https://proget.contoso.com/pypi/...` |
| `PROGET_USERNAME` | ProGet username (usually `api`) | `api` |
| `PROGET_API_KEY` | ProGet API key | `xxxx...` |

> **Pipeline auth:** Inside Azure Pipelines the agent uses `System.AccessToken` (Bearer auth) — no PAT needed. The pipeline template sets `DEVOPS_AUTH_MODE=system_token` and injects the token automatically. For local development, use `DEVOPS_AUTH_MODE=pat` with your PAT.

---

## Usage

### CLI (local development)

```bash
# Investigate a work item
uv run devops-agent investigate --work-item 1234

# With additional context
uv run devops-agent investigate --work-item 1234 --context "Focus on the retry logic"

# Feature request analysis
uv run devops-agent investigate --work-item 1234 --type feature_request

# Report only (no branch/PR creation)
uv run devops-agent investigate --work-item 1234 --report-only

# Free-form request (no work item)
uv run devops-agent request --text "How does the auth flow work?"
```

### Trigger via CLI

```bash
# Trigger the agent pipeline (just an API call with credentials)
uv run devops-agent trigger --work-item 1234 --pipeline-id 42

# With extra context
uv run devops-agent trigger -w 1234 -p 42 --context "Focus on auth" --type bug
```

### Trigger from Azure DevOps (`@agent`)

No separate server needed. Use **Power Automate** or a **Logic App** to bridge the service hook to the Pipeline API:

```mermaid
sequenceDiagram
    participant Dev as Developer
    participant ADO as Azure DevOps
    participant PA as Power Automate
    participant Pipe as Azure Pipeline
    participant Agent as LangGraph Agent
    participant LLM as GPT 5.3

    Dev->>ADO: Comment on WI #1234:<br/>"@agent investigate the auth bug"
    ADO->>PA: Service hook event<br/>(work item commented)
    PA->>PA: Parse @agent mention<br/>+ extract WI ID + context
    PA->>Pipe: POST /_apis/pipelines/{id}/runs<br/>(with PAT credentials)
    Pipe->>Pipe: python:3.12-slim + uv sync
    Pipe->>Agent: Execute agent
    Agent->>ADO: Fetch WI #1234 details
    Agent->>LLM: Plan relevant files
    LLM-->>Agent: [/src/auth.py, /tests/...]
    Agent->>ADO: Fetch 12 files via Git API
    Agent->>LLM: Analyze code vs. work item
    LLM-->>Agent: Analysis + suggested fixes
    Agent->>ADO: Create branch + push files
    Agent->>ADO: Open PR with report
    ADO-->>Dev: PR notification
```

**Setup steps:**

1. In Azure DevOps, go to **Project Settings > Service hooks**
2. Create a subscription: Event = **Work item commented on** → target = your Power Automate HTTP trigger
3. In the Power Automate flow:
   - Parse the comment text for `@agent`
   - Extract the work item ID
   - POST to `https://dev.azure.com/{org}/{project}/_apis/pipelines/{id}/runs?api-version=7.1` with `templateParameters`
   - Auth: Basic with your PAT

### Trigger from MS Teams (`@agent`)

Same approach — Power Automate flow triggered by a Teams message containing `@agent`:

```mermaid
sequenceDiagram
    participant User as Team Member
    participant Teams as MS Teams
    participant PA as Power Automate
    participant Pipe as Azure Pipeline
    participant Agent as LangGraph Agent

    User->>Teams: "@agent investigate #1234<br/>focus on payment retries"
    Teams->>PA: Message trigger<br/>(keyword: @agent)
    PA->>PA: Extract WI #1234 + context
    PA->>Pipe: POST /_apis/pipelines/{id}/runs
    Pipe->>Agent: Execute agent
    Agent->>Agent: Full investigation flow
    Note over Agent: plan → fetch → reason → output
```

**Setup steps:**

1. Create a Power Automate flow triggered by "When a keyword is mentioned" in Teams
2. Set keyword to `@agent`
3. Parse the message for a work item reference (`#1234`)
4. Call the Pipeline REST API (same POST as above)

### Azure Pipeline (manual)

The pipeline at `pipelines/azure-pipeline.yml` can be run from the Azure DevOps UI with parameters:

| Parameter | Type | Description |
|-----------|------|-------------|
| `workItemId` | number | Work item to investigate |
| `requestType` | string | `investigation`, `feature_request`, or `bug` |
| `additionalContext` | string | Extra context for the agent |
| `reportOnly` | boolean | Skip branch/PR creation |

**Variable group setup:** Create `devops-agent-secrets` in **Pipelines > Library**:

| Variable | Description |
|----------|-------------|
| `AZURE_DEVOPS_ORG_URL` | Organization URL |
| `AZURE_DEVOPS_PROJECT` | Project name |
| `AZURE_DEVOPS_REPOSITORY` | Target repository |
| `LLM_PROVIDER` | `azure_ai_foundry` |
| `AZURE_AI_FOUNDRY_ENDPOINT` | Foundry endpoint |
| `AZURE_AI_FOUNDRY_API_KEY` | Foundry API key |
| `AZURE_AI_FOUNDRY_MODEL` | `gpt-5.3` |
| `PROGET_PYPI_URL` | ProGet PyPI feed URL |
| `PROGET_USERNAME` | ProGet user (usually `api`) |
| `PROGET_API_KEY` | ProGet API key |

> **No PAT needed here.** The pipeline uses `$(System.AccessToken)` for DevOps API calls (PRs, work items, branches). Grant the **Build Service** account the required permissions in Project Settings > Repositories and Boards.

### Programmatic usage

```python
import asyncio
from src.config import get_settings
from src.clients.devops import AzureDevOpsClient
from src.clients.llm import get_chat_model
from src.agent.graph import build_graph
from src.agent.state import AgentState

async def main():
    settings = get_settings()
    devops = AzureDevOpsClient(settings)
    llm = get_chat_model(settings)
    graph = build_graph(settings, devops, llm)

    state = AgentState(
        work_item_id=1234,
        request_type="feature_request",
    )

    result = await graph.ainvoke(state)
    print(result["output_summary"])
    await devops.close()

asyncio.run(main())
```

---

## Pipeline Templates

All pipelines reuse shared templates in `pipelines/templates/`:

### `python-setup.yml`

Sets up the Python environment using `uv` with ProGet. All pipelines run inside `python:3.12-slim` — Python is already available, so uv just manages the venv and packages.

**The venv is cached between runs** via `Cache@2`, keyed on `uv.lock`. If dependencies haven't changed, `uv sync` is a no-op (~1-2s instead of 30-60s).

```yaml
container: python:3.12-slim

steps:
  - template: templates/python-setup.yml
    parameters:
      installDev: true
```

What it does:
1. Installs `uv` via pip (available in the slim container)
2. **Restores cached `.venv/` and uv package cache** (keyed on `uv.lock`)
3. Runs `uv sync --python-preference=only-system` — cache hit = no-op
4. On cache miss, downloads from ProGet and saves cache for next run
5. Verifies the environment

```mermaid
flowchart LR
    subgraph "Per pipeline run"
        direction TB
        C{"Cache hit?<br/>(uv.lock unchanged)"}
        C -->|"Yes"| NOOP["uv sync = no-op<br/>~1-2 seconds"]
        C -->|"No"| SYNC["uv sync from ProGet<br/>~30-60 seconds"]
        SYNC --> SAVE["Save .venv + cache<br/>for next run"]
    end

    UV_LOCK["uv.lock"] -->|"cache key"| C

    %% Styling
    style C fill:#fff8e1,stroke:#e65100,stroke-width:2px,color:#000000
    style NOOP fill:#4caf50,stroke:#2e7d32,stroke-width:2px,color:#000000
    style SYNC fill:#f3e5f5,stroke:#4a148c,stroke-width:2px,color:#000000
    style SAVE fill:#e1f5fe,stroke:#01579b,stroke-width:2px,color:#000000
    style UV_LOCK fill:#e1f5fe,stroke:#01579b,stroke-width:2px,color:#000000
```

### `run-agent.yml`

Executes the agent with parameters:

```yaml
steps:
  - template: templates/run-agent.yml
    parameters:
      workItemId: 1234
      requestType: "investigation"
      triggerSource: "devops_mention"
```

---

## Cost Efficiency

```mermaid
graph LR
    subgraph "Per request cost breakdown"
        direction TB
        A1["Azure DevOps API<br/>~10-30 calls<br/>FREE (included)"] --- A2["Azure AI Foundry<br/>~5k-30k tokens<br/>$0.02-$0.20"]
        A2 --- A3["Azure Pipeline<br/>~30s-2min<br/>~$0.004/min"]
    end

    subgraph "Monthly estimate (100 requests)"
        direction TB
        B1["LLM: $2 - $20"]
        B2["Pipeline: $0.40 - $3.30"]
        B3["Total: ~$3 - $25/month"]
    end

    %% Styling
    style A1 fill:#4caf50,stroke:#2e7d32,stroke-width:2px,color:#000000
    style A2 fill:#fff8e1,stroke:#e65100,stroke-width:2px,color:#000000
    style A3 fill:#e1f5fe,stroke:#01579b,stroke-width:2px,color:#000000
    style B1 fill:#f3e5f5,stroke:#4a148c,stroke-width:2px,color:#000000
    style B2 fill:#f3e5f5,stroke:#4a148c,stroke-width:2px,color:#000000
    style B3 fill:#e1f5fe,stroke:#01579b,stroke-width:2px,color:#000000
```

GPT 5.3 via Azure AI Foundry supports ~400k token context windows, but this agent deliberately stays under 120k to maximize cost efficiency and reasoning quality.

---

## Development

### Running tests

```bash
# Run all tests
uv run pytest

# Unit tests only
uv run pytest tests/unit/ -v

# With coverage
uv run pytest -v --cov=src --cov-report=html
```

### Linting & formatting

```bash
uv run ruff check src/ tests/        # Lint
uv run ruff check --fix src/ tests/  # Auto-fix
uv run ruff format src/ tests/       # Format
uv run mypy src/ --ignore-missing-imports  # Type check
```

### CI pipeline

The `pipelines/ci.yml` runs on every push/PR:

```mermaid
graph LR
    A[Checkout] --> B["uv sync<br/>(with dev deps)"]
    B --> C[Ruff lint]
    B --> D[Mypy types]
    B --> E[Pytest + coverage]
    C --> F[Publish results]
    D --> F
    E --> F

    %% Styling
    style A fill:#e1f5fe,stroke:#01579b,stroke-width:2px,color:#000000
    style B fill:#fff8e1,stroke:#e65100,stroke-width:2px,color:#000000
    style C fill:#f3e5f5,stroke:#4a148c,stroke-width:2px,color:#000000
    style D fill:#f3e5f5,stroke:#4a148c,stroke-width:2px,color:#000000
    style E fill:#f3e5f5,stroke:#4a148c,stroke-width:2px,color:#000000
    style F fill:#4caf50,stroke:#2e7d32,stroke-width:2px,color:#000000
```

---

## Project Structure

```
devops_agent/
├── src/
│   ├── main.py                 # CLI entry point (Typer) — investigate, request, trigger
│   ├── config.py               # Pydantic settings from env vars
│   ├── agent/
│   │   ├── graph.py            # LangGraph graph definition
│   │   ├── nodes.py            # Node functions (plan, fetch, reason, output)
│   │   ├── prompts.py          # LLM prompt templates
│   │   └── state.py            # Agent state (Pydantic model)
│   ├── clients/
│   │   ├── devops.py           # Azure DevOps REST API client (httpx)
│   │   ├── llm.py              # LLM client factory (AI Foundry / OpenAI / Anthropic)
│   │   └── trigger.py          # Pipeline trigger via REST API
│   └── utils/
│       └── tokens.py           # Token counting & context budget management
├── pipelines/
│   ├── azure-pipeline.yml      # Manual trigger pipeline
│   ├── webhook-trigger.yml     # @agent trigger pipeline
│   ├── ci.yml                  # CI: lint + test on PR
│   └── templates/
│       ├── python-setup.yml    # Reusable: uv + ProGet (python:3.12-slim)
│       └── run-agent.yml       # Reusable: agent execution
├── tests/
│   ├── conftest.py             # Shared fixtures
│   └── unit/
│       ├── test_agent.py       # Agent node tests
│       └── test_devops_client.py  # DevOps client tests
├── .env.example
├── .gitignore
├── pyproject.toml              # Project metadata & deps (uv-managed)
└── README.md
```

---

## License

MIT
