---
description: Unified Testing Strategy
---

# Testing Guidelines

Standards for ensuring code reliability through automated tests.

## Testing Layers

### 1. Unit tests (`tests/unit/`)
**Scope**: Isolated functions/classes.
**Dependencies**: Mock EVERYTHING external.
**Speed**: Instant (< 10ms per test).

```python
def test_count_tokens_basic():
    # Arrange
    text = "Hello world"

    # Act
    result = count_tokens(text)

    # Assert
    assert result > 0
```

### 2. Integration tests (`tests/integration/`)
**Scope**: Interaction between 2+ modules (e.g., Client + API).
**Dependencies**: Mock 3rd-party APIs with `httpx.Response`.
**Speed**: Moderate (< 5s per test).

```python
@pytest.mark.asyncio
async def test_devops_client_fetches_work_item(devops_client):
    # Arrange — mock the HTTP response
    # Act — call the client method
    # Assert — verify parsed result
```

## Test Structure

### Arrange-Act-Assert
Every test MUST follow the AAA pattern with comments:
```python
def test_feature():
    # Arrange
    service = UserService(mock_repo)

    # Act
    result = service.create(valid_data)

    # Assert
    assert result.id is not None
```

### Shared Fixtures
Define common fixtures in `tests/conftest.py`:
```python
@pytest.fixture
def settings() -> Settings:
    return Settings(
        azure_devops_org_url="https://dev.azure.com/test-org",
        azure_devops_pat="fake-pat-token",
        azure_devops_project="test-project",
        azure_devops_repository="test-repo",
    )

@pytest.fixture
def devops_client(settings: Settings) -> AzureDevOpsClient:
    return AzureDevOpsClient(settings)

@pytest.fixture
def mock_llm() -> AsyncMock:
    llm = AsyncMock()
    llm.ainvoke.return_value = MagicMock(content="{}")
    return llm
```

## Tools & Libraries

### Pytest
Standard `pytest` conventions:
- **Files**: `test_*.py`
- **Functions**: `test_*`
- **Fixtures**: Defined in `conftest.py`
- **Async**: `@pytest.mark.asyncio` (or `pytest-asyncio` auto mode)

### Mocking
Use `unittest.mock` or `pytest-mock`:
```python
def test_service_calls_repo(mocker):
    mock_repo = mocker.Mock()
    service = UserService(mock_repo)

    service.do_work()

    mock_repo.save.assert_called_once()
```

## Coverage Rules

- **Core logic** (`src/agent/`): 100% coverage required.
- **Utils** (`src/utils/`): 80% coverage required.
- **Clients** (`src/clients/`): Smoke tests only (don't test httpx itself).

## Running Tests

```bash
# All tests
uv run pytest

# Unit tests only
uv run pytest tests/unit/ -v

# With coverage
uv run pytest -v --cov=src --cov-report=html
```
