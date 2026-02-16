---
description: LangChain & LangGraph Prompt Engineering Guidelines
---

# LangChain & LangGraph Guidelines

Best practices for prompt engineering and agent workflows in the devops_agent project.

## 1. Prompt Engineering

### Chain of Thought (CoT)
Encourage the model to reason before acting:
- "Before selecting files, analyze the work item requirements step by step."
- "Outline your reasoning, then provide the JSON response."

### Few-Shot Prompting
Provide examples of expected output format in prompts:
```python
PLAN_FILES_PROMPT = """
...
Example output:
{
  "reasoning": "The work item mentions authentication...",
  "files": [{"path": "/src/auth.py", "reason": "main auth logic"}]
}
"""
```

### Delimiters & Structure
Use clear delimiters to separate instructions from data:
- Use `---` or `"""` to wrap context chunks.
- Use Markdown headers for different prompt sections.
- Always specify the expected output format (JSON schema).

## 2. LangGraph Agent Design

### State-Driven Architecture
The agent uses a typed state (`AgentState`) that flows through nodes:
```
receive_request → plan_files → fetch_files → reason → create_output
```

### Node Contracts
Each node function should:
1. Accept `AgentState` + injected dependencies (llm, settings).
2. Return a `dict` of state updates (partial state).
3. Be independently testable with mocked dependencies.

```python
async def plan_files(state: AgentState, *, llm: BaseChatModel) -> dict:
    """Pick relevant files based on work item and repo structure."""
    ...
    return {"planned_files": files, "plan_reasoning": reasoning}
```

### Conditional Edges
Use conditional routing for iterative refinement:
```python
def should_continue(state: AgentState) -> str:
    if state.needs_more_context and state.iteration < 3:
        return "plan_files"
    return "create_output"
```

## 3. Context Budget Management

### Token Awareness
- Always count tokens before sending context to the LLM.
- Use `truncate_to_budget()` to stay within limits.
- Log when truncation happens for debugging.

### Targeted Retrieval
- Fetch 5-20 files max per iteration (not the whole repo).
- Prioritize files the LLM explicitly asked for.
- Include file path headers in context blocks for grounding.

## 4. Centralized Prompt Management

All prompts live in `src/agent/prompts.py`:
- `PLAN_FILES_SYSTEM` / `PLAN_FILES_USER` — file selection.
- `REASON_SYSTEM` / `REASON_USER` — code analysis.
- `OUTPUT_SYSTEM` / `OUTPUT_USER` — report/PR generation.

Never inline prompt strings in node functions.
