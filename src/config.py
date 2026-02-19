"""Application configuration loaded from environment variables."""

from __future__ import annotations

from enum import Enum

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProvider(str, Enum):
    AZURE_AI_FOUNDRY = "azure_ai_foundry"
    AZURE_OPENAI = "azure_openai"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class DevOpsAuthMode(str, Enum):
    """How the agent authenticates to Azure DevOps REST APIs.

    MANAGED_IDENTITY — Use a User-assigned Managed Identity (Container App).
                       Bearer auth via azure-identity, no secrets.
    SYSTEM_TOKEN     — Use $(System.AccessToken) from a pipeline.
                       Bearer auth, short-lived, no secrets to manage.
    """

    MANAGED_IDENTITY = "managed_identity"
    SYSTEM_TOKEN = "system_token"


class Settings(BaseSettings):
    """All configuration sourced from env vars / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Azure DevOps ──────────────────────────────────────────────
    azure_devops_org_url: str = Field(description="e.g. https://dev.azure.com/my-org")
    azure_devops_project: str = Field(description="Project name")
    azure_devops_repository: str = Field(description="Repository name or ID")

    devops_auth_mode: DevOpsAuthMode = Field(
        default=DevOpsAuthMode.MANAGED_IDENTITY,
        description="Auth mode: 'managed_identity' (Container App) | 'system_token' (pipeline)",
    )
    system_access_token: str = Field(
        default="",
        description="$(System.AccessToken) — injected by Azure Pipelines",
    )
    managed_identity_client_id: str = Field(
        default="",
        description="Client ID of the User-assigned Managed Identity",
    )

    @model_validator(mode="after")
    def _validate_devops_auth(self) -> Settings:
        """Ensure the correct credential is provided for the chosen auth mode."""
        if self.devops_auth_mode == DevOpsAuthMode.SYSTEM_TOKEN and not self.system_access_token:
            raise ValueError("devops_auth_mode=system_token requires SYSTEM_ACCESS_TOKEN")
        return self

    # ── LLM provider ─────────────────────────────────────────────
    llm_provider: LLMProvider = LLMProvider.AZURE_AI_FOUNDRY

    azure_ai_foundry_endpoint: str = Field(
        default="",
        description="Azure AI Foundry endpoint, e.g. https://<project>.services.ai.azure.com",
    )
    azure_ai_foundry_api_key: str = ""
    azure_ai_foundry_model: str = Field(
        default="gpt-5.3",
        description="Model deployment name in AI Foundry",
    )
    azure_ai_foundry_api_version: str = "2025-04-01-preview"

    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_deployment: str = "gpt-4o"
    azure_openai_api_version: str = "2024-12-01-preview"

    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"

    # ── Agent behaviour ──────────────────────────────────────────
    max_files_per_request: int = Field(default=20, ge=1, le=50)
    max_tokens_context: int = Field(default=120_000, ge=1_000)
    default_branch: str = "main"
    log_level: str = "INFO"

    # ── Service Bus (Container App deployment) ───────────────────
    service_bus_connection_str: str = Field(
        default="",
        description="Azure Service Bus connection string (or use Managed Identity)",
    )
    service_bus_queue_name: str = Field(
        default="agent-requests",
        description="Queue name for agent work items",
    )

    # ── Key Vault (Container App deployment) ─────────────────────
    key_vault_url: str = Field(
        default="",
        description="e.g. https://devops-agent-kv.vault.azure.net",
    )

    # ── Derived helpers ──────────────────────────────────────────

    @property
    def devops_org_name(self) -> str:
        """Extract the org name from the URL."""
        return self.azure_devops_org_url.rstrip("/").rsplit("/", 1)[-1]


def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()  # type: ignore[call-arg]
