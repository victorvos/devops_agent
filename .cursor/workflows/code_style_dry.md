---
description: Code Style and DRY Principles
---

# Python Development Guidelines

Standards for writing clean, maintainable, and SOLID Python code in the devops_agent project.

## Code Organization

### File Size Limits
- **CLI/Routers**: < 400 lines
- **Services/Clients**: < 300 lines
- **Utilities**: < 200 lines
- **Interfaces**: < 100 lines
- **Functions**: < 50 lines (extract if longer)
- **Classes**: < 300 lines (single responsibility)

### Module Structure
```
src/
├── config.py              # Settings (Pydantic)
├── main.py                # CLI entry point (Typer)
├── agent/                 # LangGraph domain logic
│   ├── graph.py           # Graph wiring
│   ├── nodes.py           # Node functions
│   ├── prompts.py         # Prompt templates
│   └── state.py           # State model
├── clients/               # External service clients
│   ├── devops.py          # Azure DevOps REST API
│   ├── llm.py             # LLM factory
│   └── trigger.py         # Pipeline trigger
└── utils/                 # Pure utility functions
    └── tokens.py          # Token counting
```

## SOLID Principles

### Single Responsibility (SRP)
Each module has one reason to change:
```python
# Good: Focused client
class AzureDevOpsClient:
    async def get_work_item(self, work_item_id: int) -> dict: ...

# Bad: Multiple responsibilities
class AgentService:
    async def get_work_item(self): ...
    async def call_llm(self): ...       # Should be LLM client
    async def trigger_pipeline(self): ...  # Should be PipelineTrigger
```

### Dependency Inversion (DIP)
Inject dependencies via constructor:
```python
# Good: Inject clients
def build_graph(settings: Settings, devops: AzureDevOpsClient, llm: BaseChatModel):
    ...

# Bad: Concrete dependency
def build_graph():
    devops = AzureDevOpsClient(Settings())  # Tightly coupled
```

## Coding Standards

### Type Hints
Always use type hints for function signatures:
```python
def process_data(items: list[dict[str, Any]], limit: int = 10) -> list[str]:
    ...

async def fetch_user(user_id: str) -> dict[str, Any] | None:
    ...
```

### Docstrings
Use Google-style docstrings for public APIs:
```python
def calculate_total(items: list[Item], discount: float = 0.0) -> float:
    """Calculate the total price of items with optional discount.

    Args:
        items: List of items to calculate.
        discount: Discount percentage (0.0 to 1.0).

    Returns:
        Total price after discount.

    Raises:
        ValueError: If discount is outside valid range.
    """
```

### Naming Conventions
```python
# Classes: PascalCase
class AzureDevOpsClient: ...
class AgentState: ...

# Functions/methods: snake_case
def get_file_content(): ...
async def plan_files(): ...

# Constants: UPPER_SNAKE_CASE
MAX_RETRIES = 3
DEFAULT_TIMEOUT = 30

# Private: _leading_underscore
def _extract_json(): ...
_internal_cache = {}
```

### Error Handling
```python
# Define specific exceptions
class EntityNotFoundError(Exception):
    """Raised when entity doesn't exist."""

class TokenBudgetExceededError(Exception):
    """Raised when context exceeds token budget."""
```

### Async/Await
```python
# Good: Concurrent execution
async def fetch_all_data():
    work_item, repo_tree = await asyncio.gather(
        client.get_work_item(wi_id),
        client.get_repo_tree("/")
    )
    return work_item, repo_tree
```

## When to Extract

Extract code to a new module when:
1. A function exceeds 50 lines
2. A class has unrelated methods (violates SRP)
3. Code is duplicated across files
4. A file exceeds size limits
5. Testing requires mocking internals
