# Azure DevOps Agent

An intelligent agent that investigates Azure DevOps work items (feature requests, bugs, backlog items) using **targeted code retrieval** — never clones the full repo.

Built with [LangGraph](https://github.com/langchain-ai/langgraph), [Azure AI Foundry](https://ai.azure.com/) (GPT 5.3), [Azure Pipelines](https://learn.microsoft.com/en-us/azure/devops/pipelines/), and [uv](https://docs.astral.sh/uv/).

Triggered via **`@agent`** mentions in Azure DevOps work item comments or Microsoft Teams.

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

    style A fill:#f66,color:#fff
    style C fill:#f66,color:#fff
    style D fill:#6c6,color:#fff
    style G fill:#6c6,color:#fff
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

### End-to-End Flow

```mermaid
flowchart TB
    subgraph triggers["Trigger Sources"]
        T1["Azure DevOps<br/>@agent in comment"]
        T2["Microsoft Teams<br/>@agent in channel"]
        T3["Manual<br/>Pipeline UI / API"]
    end

    subgraph webhook["Webhook Receiver<br/>(FastAPI on Azure Container App)"]
        WH["POST /webhooks/devops<br/>POST /webhooks/teams<br/>POST /webhooks/trigger"]
    end

    subgraph pipeline["Azure Pipeline<br/>(webhook-trigger.yml)<br/>container: python:3.12-slim"]
        direction TB
        P1["Checkout agent code<br/>(shallow, fetchDepth: 1)"]
        P2["uv sync via ProGet<br/>(templates/python-setup.yml)"]
        P3["Run agent<br/>(templates/run-agent.yml)"]
        P1 --> P2 --> P3
    end

    subgraph agent["LangGraph Agent"]
        direction TB
        N1["receive_request<br/>Fetch work item + repo tree"]
        N2["plan_files<br/>LLM picks 5-20 relevant files"]
        N3["fetch_files<br/>Git REST API (targeted)"]
        N4["reason<br/>Analyze code vs. work item"]
        N5["create_output<br/>Branch + PR or report"]

        N1 --> N2 --> N3 --> N4
        N4 -->|"needs more context"| N2
        N4 -->|"done"| N5
    end

    subgraph external["External Services"]
        AZ["Azure DevOps<br/>REST API"]
        LLM["Azure AI Foundry<br/>GPT 5.3"]
    end

    T1 --> WH
    T2 --> WH
    T3 -->|"direct"| pipeline
    WH -->|"Pipeline REST API"| pipeline
    P3 --> agent
    N1 & N3 & N5 <--> AZ
    N2 & N4 <--> LLM
```

### LangGraph Agent Detail

```mermaid
stateDiagram-v2
    [*] --> receive_request

    receive_request: receive_request
    note right of receive_request
        Fetch work item fields,
        comments, tags via REST API.
        Get repo file tree (metadata only).
    end note

    receive_request --> plan_files

    plan_files: plan_files
    note right of plan_files
        LLM analyzes work item +
        repo structure to select
        5-20 relevant file paths.
    end note

    plan_files --> fetch_files

    fetch_files: fetch_files
    note right of fetch_files
        Download file contents via
        Azure DevOps Git Items API.
        No clone needed.
    end note

    fetch_files --> reason

    reason: reason
    note right of reason
        Analyze code against work item.
        Produce report + suggested changes.
        May request more context.
    end note

    reason --> plan_files: needs_more_context\n(up to 3 iterations)
    reason --> create_output: done

    create_output: create_output
    note right of create_output
        Create branch, push files,
        open PR — or return report only.
    end note

    create_output --> [*]
```

### Pipeline Template Reuse

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

    style TPY fill:#36f,color:#fff
    style TRA fill:#36f,color:#fff
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
# (uv reads these env vars to authenticate with the ProGet index
# defined in pyproject.toml under [tool.uv.index])
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
# Copy and fill in your credentials
cp .env.example .env
```

Key settings in `.env`:

| Variable | Description | Example |
|----------|-------------|---------|
| `AZURE_DEVOPS_ORG_URL` | Your org URL | `https://dev.azure.com/contoso` |
| `AZURE_DEVOPS_PAT` | Personal Access Token | `xxxx...` |
| `AZURE_DEVOPS_PROJECT` | Project name | `MyProject` |
| `AZURE_DEVOPS_REPOSITORY` | Repo name | `backend-api` |
| `AZURE_AI_FOUNDRY_ENDPOINT` | AI Foundry endpoint | `https://proj.services.ai.azure.com` |
| `AZURE_AI_FOUNDRY_API_KEY` | Foundry API key | `xxxx...` |
| `AZURE_AI_FOUNDRY_MODEL` | Model deployment | `gpt-5.3` |
| `AGENT_PIPELINE_ID` | Webhook-trigger pipeline ID | `42` |
| `PROGET_PYPI_URL` | ProGet PyPI feed URL | `https://proget.contoso.com/pypi/python-packages/simple/` |
| `PROGET_USERNAME` | ProGet username (usually `api`) | `api` |
| `PROGET_API_KEY` | ProGet API key | `xxxx...` |

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

### Start the Webhook Receiver

```bash
# Start the FastAPI webhook server
uv run devops-agent serve --port 8000
```

Endpoints:
| Method | Path | Source |
|--------|------|--------|
| `POST` | `/webhooks/devops` | Azure DevOps service hook |
| `POST` | `/webhooks/teams` | MS Teams bot / webhook |
| `POST` | `/webhooks/trigger` | Manual / generic HTTP |
| `GET` | `/health` | Health check |

### Trigger from Azure DevOps (`@agent`)

```mermaid
sequenceDiagram
    participant Dev as Developer
    participant ADO as Azure DevOps
    participant WH as Webhook Receiver
    participant Pipe as Azure Pipeline
    participant Agent as LangGraph Agent
    participant LLM as GPT 5.3

    Dev->>ADO: Comment on WI #1234:<br/>"@agent investigate the auth bug"
    ADO->>WH: Service hook POST<br/>/webhooks/devops
    WH->>WH: Parse @agent mention<br/>+ extract work item ID
    WH->>Pipe: Queue pipeline run<br/>(Pipeline REST API)
    Pipe->>Pipe: uv sync + setup
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
2. Create a new subscription:
   - Service: **Web Hooks**
   - Event: **Work item commented on**
   - Filter: (optional) area path, work item type
   - URL: `https://your-webhook-receiver.azurecontainerapps.io/webhooks/devops`
3. Test with a comment containing `@agent`

### Trigger from MS Teams (`@agent`)

```mermaid
sequenceDiagram
    participant User as Team Member
    participant Teams as MS Teams
    participant WH as Webhook Receiver
    participant Pipe as Azure Pipeline
    participant Agent as LangGraph Agent

    User->>Teams: "@agent investigate #1234<br/>focus on payment retries"
    Teams->>WH: Bot/webhook POST<br/>/webhooks/teams
    WH->>WH: Extract WI #1234<br/>+ context text
    WH->>Pipe: Queue pipeline run
    Pipe->>Agent: Execute agent
    Agent->>Agent: Full investigation flow
    Note over Agent: plan → fetch → reason → output
```

**Setup steps:**

1. Register a Teams bot or use an Outgoing Webhook in your channel
2. Point it to `https://your-webhook-receiver.azurecontainerapps.io/webhooks/teams`
3. Mention `@agent` with a work item reference: `@agent investigate #1234`

### Azure Pipeline (manual)

The pipeline at `pipelines/azure-pipeline.yml` can be run from the Azure DevOps UI with parameters:

| Parameter | Type | Description |
|-----------|------|-------------|
| `workItemId` | number | Work item to investigate |
| `requestType` | string | `investigation`, `feature_request`, or `bug` |
| `additionalContext` | string | Extra context for the agent |
| `reportOnly` | boolean | Skip branch/PR creation |

**Variable group setup:** Create a variable group named `devops-agent-secrets` in **Pipelines > Library** containing:

| Variable | Description |
|----------|-------------|
| `AZURE_DEVOPS_ORG_URL` | Organization URL |
| `AZURE_DEVOPS_PAT` | Personal Access Token |
| `AZURE_DEVOPS_PROJECT` | Project name |
| `AZURE_DEVOPS_REPOSITORY` | Target repository |
| `LLM_PROVIDER` | `azure_ai_foundry` |
| `AZURE_AI_FOUNDRY_ENDPOINT` | Foundry endpoint |
| `AZURE_AI_FOUNDRY_API_KEY` | Foundry API key |
| `AZURE_AI_FOUNDRY_MODEL` | `gpt-5.3` |
| `PROGET_PYPI_URL` | ProGet PyPI feed URL |
| `PROGET_USERNAME` | ProGet user (usually `api`) |
| `PROGET_API_KEY` | ProGet API key |

### Programmatic Usage

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

Sets up the Python environment using `uv` with ProGet as the package index. All pipelines run inside a `python:3.12-slim` container — Python is already available, so uv just manages the venv and packages.

**The venv is cached between runs** via Azure Pipelines `Cache@2`, keyed on `uv.lock`. If dependencies haven't changed, `uv sync` is a near-instant no-op (~1-2s instead of 30-60s).

```yaml
container: python:3.12-slim

steps:
  - template: templates/python-setup.yml
    parameters:
      installDev: true    # false for production runs
```

What it does:
1. Installs `uv` via pip (already available in the slim container)
2. **Restores cached `.venv/` and uv package cache** (keyed on `uv.lock`)
3. Runs `uv sync --python-preference=only-system` — if cache hit, this is a no-op
4. On cache miss (deps changed), downloads from ProGet and saves cache for next run
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
    subgraph "Per Request Cost Breakdown"
        direction TB
        A1["Azure DevOps API<br/>~10-30 calls<br/>FREE (included)"] --- A2["Azure AI Foundry<br/>~5k-30k tokens<br/>$0.02-$0.20"]
        A2 --- A3["Azure Pipeline<br/>~30s-2min<br/>~$0.004/min"]
    end

    subgraph "Monthly Estimate (100 requests)"
        direction TB
        B1["LLM: $2 - $20"]
        B2["Pipeline: $0.40 - $3.30"]
        B3["Total: ~$3 - $25/month"]
    end
```

GPT 5.3 via Azure AI Foundry supports ~400k token context windows, but this agent deliberately stays under 120k to maximize cost efficiency and reasoning quality.

---

## Development

### Running Tests

```bash
# Run all tests
uv run pytest

# Verbose with coverage
uv run pytest -v --cov=src --cov-report=html

# Specific test file
uv run pytest tests/test_agent.py -v
```

### Linting & Formatting

```bash
# Check lint
uv run ruff check src/ tests/

# Auto-fix
uv run ruff check --fix src/ tests/

# Format
uv run ruff format src/ tests/

# Type check
uv run mypy src/ --ignore-missing-imports
```

### CI Pipeline

The `pipelines/ci.yml` runs on every push/PR and executes:

```mermaid
graph LR
    A[Checkout] --> B["uv sync<br/>(with dev deps)"]
    B --> C[Ruff lint]
    B --> D[Mypy types]
    B --> E[Pytest + coverage]
    C --> F[Publish results]
    D --> F
    E --> F
```

---

## Project Structure

```
devops_agent/
├── src/
│   ├── main.py                 # CLI entry point (Typer) — investigate, request, serve
│   ├── config.py               # Pydantic settings from env vars
│   ├── agent/
│   │   ├── graph.py            # LangGraph graph definition
│   │   ├── nodes.py            # Node functions (plan, fetch, reason, output)
│   │   ├── prompts.py          # LLM prompt templates
│   │   └── state.py            # Agent state (Pydantic model)
│   ├── clients/
│   │   ├── devops.py           # Azure DevOps REST API client (httpx)
│   │   └── llm.py              # LLM client factory (AI Foundry / OpenAI / Anthropic)
│   ├── utils/
│   │   └── tokens.py           # Token counting & context budget management
│   └── webhooks/
│       └── receiver.py         # FastAPI webhook receiver (DevOps + Teams triggers)
├── pipelines/
│   ├── azure-pipeline.yml      # Manual trigger pipeline
│   ├── webhook-trigger.yml     # @agent trigger pipeline
│   ├── ci.yml                  # CI: lint + test on PR
│   └── templates/
│       ├── python-setup.yml    # Reusable: uv + ProGet (python:3.12-slim)
│       └── run-agent.yml       # Reusable: agent execution
├── tests/
│   ├── test_agent.py           # Agent node tests
│   └── test_devops_client.py   # DevOps client tests
├── .env.example                # Environment variable template
├── .gitignore
├── pyproject.toml              # Project metadata & deps (uv-managed)
└── README.md
```

---

## License

MIT
