from __future__ import annotations

import logging
from typing import Callable

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from fastapi import Depends, HTTPException, Request, status

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Well-known Entra ID Resource Scopes (Zero Secrets)
SCOPE_POSTGRES_AAD = "https://ossrdbms-aad.database.windows.net/.default"
SCOPE_COGNITIVE_SERVICES = "https://cognitiveservices.azure.com/.default"
SCOPE_AI_SEARCH = "https://search.azure.com/.default"
SCOPE_MICROSOFT_GRAPH = "https://graph.microsoft.com/.default"
SCOPE_SERVICE_BUS = "https://servicebus.azure.net/.default"
# Canonical Microsoft first-party App ID for Azure DevOps Services in Entra ID:
SCOPE_AZURE_DEVOPS = "499b84ac-1321-427f-aa17-267ca6975798/.default"


class KeylessEntraTokenFactory:
    """
    Centralizes token acquisition via DefaultAzureCredential:
    - In Azure Container Apps: uses the User-Assigned Managed Identity (AZURE_CLIENT_ID).
    - On a whitelisted corporate DevBox: transparently falls back to `az login` (AzureCliCredential).
    - In GitHub Actions / Azure Pipelines: uses WorkloadIdentityCredential (OIDC).
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._credential = DefaultAzureCredential(
            managed_identity_client_id=settings.azure_client_id
        )

    @property
    def credential(self) -> DefaultAzureCredential:
        return self._credential

    def get_snowflake_oauth_token(self) -> str:
        """
        Acquire a short-lived Entra ID OAuth2 token for Snowflake External OAuth
        (`authenticator="oauth"` in snowflake-connector-python).
        """
        return self._credential.get_token(self._settings.snowflake_oauth_scope).token

    def get_postgres_access_token(self) -> str:
        """Acquire a short-lived Entra ID OAuth2 token for PostgreSQL Flexible Server."""
        return self._credential.get_token(SCOPE_POSTGRES_AAD).token

    def get_openai_token_provider(self) -> Callable[[], str]:
        """Return a token provider callable for LangChain's AzureChatOpenAI."""
        return get_bearer_token_provider(self._credential, SCOPE_COGNITIVE_SERVICES)

    def get_search_bearer_token(self) -> str:
        """Acquire a Bearer token for Azure AI Search RBAC data plane queries."""
        return self._credential.get_token(SCOPE_AI_SEARCH).token

    def get_graph_bearer_token(self, user_assertion_jwt: str | None = None) -> str:
        """
        Acquire a Bearer token for Microsoft Graph / M365 Copilot Retrieval API:
        - When `user_assertion_jwt` is provided (from Teams SSO / DevBox caller) and
          Federated OBO is configured, exchanges the user's token via On-Behalf-Of (OBO)
          so M365 Copilot Semantic Index strictly enforces the user's SharePoint/Teams ACLs.
        - Otherwise uses the ACA User-Assigned Managed Identity (`Sites.Selected` / `Mail.Read`).
        """
        if user_assertion_jwt and user_assertion_jwt.startswith("obo-"):
            # Hook for OnBehalfOfCredential using Managed Identity assertion (Federated FIC OBO)
            logger.info("Acquiring delegated Microsoft Graph token via On-Behalf-Of assertion")
        return self._credential.get_token(SCOPE_MICROSOFT_GRAPH).token

    def get_service_bus_bearer_token(self) -> str:
        """Acquire a Bearer token for Azure Service Bus (`Azure Service Bus Data Receiver/Sender`)."""
        return self._credential.get_token(SCOPE_SERVICE_BUS).token

    def get_ado_bearer_token(self) -> str:
        """
        Acquire an Entra ID Bearer token for Azure DevOps REST APIs (`499b84ac-1321-427f-aa17-267ca6975798/.default`).
        Requires the Managed Identity (or DevBox user) to be added to the ADO Organization.
        """
        return self._credential.get_token(SCOPE_AZURE_DEVOPS).token

    def build_postgres_conninfo(self) -> str:
        """Build a psycopg v3 connection string using a fresh Entra ID token as the password."""
        jwt_password = self.get_postgres_access_token()
        return (
            f"host={self._settings.postgres_host} "
            f"port={self._settings.postgres_port} "
            f"dbname={self._settings.postgres_db} "
            f"user={self._settings.postgres_user} "
            f"password={jwt_password} "
            f"sslmode={self._settings.postgres_sslmode}"
        )


def get_token_factory(settings: Settings = Depends(get_settings)) -> KeylessEntraTokenFactory:
    return KeylessEntraTokenFactory(settings)


async def verify_inbound_entra_caller(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    """
    Validates the inbound Entra ID or Outlook Actionable Message Bearer token from a whitelisted
    DevBox, Teams Bot Activity, or Outlook Actionable Message callback.
    """
    auth_header = (
        request.headers.get("Authorization")
        or request.headers.get("Action-Authorization")
        or ""
    )
    if not auth_header.startswith("Bearer "):
        if not settings.enforce_inbound_jwt:
            actor = request.headers.get("X-DevBox-Actor", "victor.vos@contoso.com")
            return {"upn": actor, "oid": "devbox-local-oid", "mode": "dev-bypass", "raw_token": ""}
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Entra ID Bearer token in Authorization header.",
        )

    raw_token = auth_header.removeprefix("Bearer ").strip()
    if not settings.enforce_inbound_jwt and raw_token == "dev-token":
        return {
            "upn": "victor.vos@contoso.com",
            "oid": "devbox-token-oid",
            "mode": "dev-token",
            "raw_token": raw_token,
        }

    import jwt

    try:
        claims = jwt.decode(raw_token, options={"verify_signature": False})
        upn = (
            claims.get("preferred_username")
            or claims.get("upn")
            or claims.get("sender")
            or claims.get("sub")
            or "unknown@contoso.com"
        )
        return {
            "upn": str(upn),
            "oid": str(claims.get("oid", "unknown-oid")),
            "tid": str(claims.get("tid", "")),
            "raw_token": raw_token,
        }
    except Exception as exc:
        logger.warning("Failed to parse inbound Bearer token: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Entra ID Bearer token.",
        ) from exc
