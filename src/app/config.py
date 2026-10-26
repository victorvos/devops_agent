from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Zero-secret configuration. All authentication uses Entra ID tokens via DefaultAzureCredential / OBO."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Identity & Tenant
    azure_tenant_id: str = "00000000-0000-0000-0000-000000000000"
    azure_client_id: str | None = None  # Client ID of the User-Assigned Managed Identity in ACA
    inbound_api_client_id: str = "00000000-0000-0000-0000-000000000000"
    enforce_inbound_jwt: bool = False  # Flip to True in staging/prod

    # Corporate MS Teams & Outlook Trigger Configuration
    graph_expected_client_state: str = "enterprise-lg-webhook-state-v1"
    trigger_mailbox_upn: str = "ai-orchestrator@contoso.com"
    trigger_team_id: str = "00000000-0000-0000-0000-000000000000"
    trigger_channel_id: str = "19:general@thread.tacv2"
    service_bus_fqdn: str = "sb-lgorchestrator-dev.servicebus.windows.net"
    service_bus_queue_name: str = "q-teams-outlook-triggers"

    # M365 Copilot Retrieval API / Graph Search / Copilot Studio Configuration
    m365_copilot_retrieval_enabled: bool = True
    copilot_studio_bot_endpoint: str = ""  # Optional Direct Line / M365 Agents endpoint
    dry_run_m365: bool = True

    # Snowflake + dbt Primary Database & State Backend (100% Keyless Entra External OAuth)
    snowflake_account: str = "xy12345.west-europe.azure"
    snowflake_oauth_scope: str = "api://snowflake-entra-resource-id/.default"
    snowflake_user: str = "id-lgorchestrator-dev"
    snowflake_role: str = "RL_LANGGRAPH_AGENT"
    snowflake_warehouse: str = "WH_LANGGRAPH_XS"
    snowflake_analytics_database: str = "ANALYTICS_PROD"
    snowflake_analytics_schema: str = "MARTS"
    snowflake_state_database: str = "AGENT_STATE_DB"
    snowflake_state_schema: str = "LANGGRAPH"
    dry_run_snowflake: bool = True

    # Optional PostgreSQL Flexible Server (When enablePostgres=true)
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "langgraph_state"
    postgres_user: str = "id-lgorchestrator-dev"
    postgres_sslmode: str = "require"
    dry_run_checkpointer: bool = True

    # Azure AI Foundry & AI Search (Entra ID RBAC Auth)
    azure_openai_endpoint: str = "https://ais-placeholder.openai.azure.com/"
    azure_openai_deployment: str = "gpt-4o"
    azure_openai_api_version: str = "2024-08-01-preview"
    azure_search_endpoint: str = "https://srch-placeholder.search.windows.net"
    azure_search_index: str = "enterprise-engineering-docs"
    dry_run_ai: bool = True

    # Azure DevOps Services (Entra ID Resource 499b84ac-1321-427f-aa17-267ca6975798)
    ado_org_url: str = "https://dev.azure.com/your-enterprise-org"
    ado_default_project: str = "DataPlatform"
    ado_dbt_repo_name: str = "dbt-snowflake-analytics"
    ado_default_area_path: str = "DataPlatform\\CustomerAnalytics"
    ado_default_iteration_path: str = "DataPlatform\\2026-Q4-Sprint-2"
    dry_run_ado: bool = True

    # Public callback base URL for Adaptive Cards
    public_base_url: str = "https://ca-lgorchestrator-dev.westeurope.azurecontainerapps.io"


@lru_cache
def get_settings() -> Settings:
    return Settings()
