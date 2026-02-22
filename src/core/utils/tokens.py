"""Token counting and context budget management.

Keeps the agent within the configured max_tokens_context budget so we
never blow past the LLM context window or waste money on oversized prompts.
"""

from __future__ import annotations

import logging

import tiktoken

logger = logging.getLogger(__name__)

# cl100k_base covers GPT-4o / GPT-4 / Claude (close enough approximation)
_ENCODING = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    """Return the approximate token count for a string."""
    return len(_ENCODING.encode(text, disallowed_special=()))


def truncate_to_budget(text: str, max_tokens: int) -> str:
    """Truncate text to fit within a token budget, preserving the beginning."""
    tokens = _ENCODING.encode(text, disallowed_special=())
    if len(tokens) <= max_tokens:
        return text
    truncated = _ENCODING.decode(tokens[:max_tokens])
    logger.warning("Truncated content from %d to %d tokens", len(tokens), max_tokens)
    return truncated + "\n\n[... truncated to fit context budget ...]"


def build_context_block(
    files: list[tuple[str, str]],
    max_tokens: int,
) -> str:
    """Combine file contents into a single context string within a token budget.

    Args:
        files: List of (path, content) tuples.
        max_tokens: Maximum total tokens for the combined block.

    Returns:
        Formatted context string with file headers and contents.
    """
    parts: list[str] = []
    remaining = max_tokens

    for path, content in files:
        header = f"--- FILE: {path} ---\n"
        header_tokens = count_tokens(header)

        if remaining <= header_tokens + 50:
            parts.append(f"\n[... {len(files) - len(parts)} more files omitted — context budget reached ...]")
            break

        content_budget = remaining - header_tokens
        trimmed_content = truncate_to_budget(content, content_budget)
        block = header + trimmed_content + "\n"
        parts.append(block)
        remaining -= count_tokens(block)

    result = "\n".join(parts)
    total = count_tokens(result)
    logger.info("Context block: %d files, ~%d tokens (budget %d)", len(parts), total, max_tokens)
    return result
