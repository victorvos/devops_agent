"""Application configuration loaded from environment variables."""

from __future__ import annotations

from enum import Enum

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProvider(str, Enum):
    AZURE_AI_FOUNDRY = "azure_ai_foundry"  # Primary — Azure AI Foundry (GPT 5.x, etc.)
    AZURE_OPENAI = "azure_openai"          # Legacy Azure OpenAI Service
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class Settings(BaseSettings):
    """All configuration sourced from env vars / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Azure DevOps ──────────────────────────────────────────────
    azure_devops_org_url: str = Field(description="e.g. https://dev.azure.com/my-org")
    azure_devops_pat: str = Field(description="Personal Access Token")
    azure_devops_project: str = Field(description="Project name")
    azure_devops_repository: str = Field(description="Repository name or ID")

    # ── LLM provider ─────────────────────────────────────────────
    llm_provider: LLMProvider = LLMProvider.AZURE_AI_FOUNDRY

    # Azure AI Foundry (primary — supports GPT 5.x, Grok, Phi, etc.)
    azure_ai_foundry_endpoint: str = Field(
        default="",
        description="Azure AI Foundry endpoint, e.g. https://<project>.services.ai.azure.com",
    )
    azure_ai_foundry_api_key: str = ""
    azure_ai_foundry_model: str = Field(
        default="gpt-5.3",
        description="Model deployment name in AI Foundry (e.g. gpt-5.3, gpt-4o, grok-3)",
    )
    azure_ai_foundry_api_version: str = "2025-04-01-preview"

    # Azure OpenAI (legacy)
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_deployment: str = "gpt-4o"
    azure_openai_api_version: str = "2024-12-01-preview"

    # OpenAI direct
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # Anthropic
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"

    # ── Agent behaviour ──────────────────────────────────────────
    max_files_per_request: int = Field(default=20, ge=1, le=50)
    max_tokens_context: int = Field(default=120_000, ge=1_000)
    default_branch: str = "main"
    log_level: str = "INFO"

    # ── Derived helpers ──────────────────────────────────────────

    @property
    def devops_org_name(self) -> str:
        """Extract the org name from the URL."""
        return self.azure_devops_org_url.rstrip("/").rsplit("/", 1)[-1]


def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()  # type: ignore[call-arg]
