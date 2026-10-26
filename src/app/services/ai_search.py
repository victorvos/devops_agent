from __future__ import annotations

from typing import Any
import httpx

from app.auth.entra import KeylessEntraTokenFactory
from app.config import Settings


class EnterpriseGroundingService:
    """
    Queries Azure AI Search (hybrid vector + semantic ranker) using a Managed Identity
    Bearer token (`Search Index Data Reader`), with zero API keys.
    """

    def __init__(self, settings: Settings, token_factory: KeylessEntraTokenFactory) -> None:
        self._settings = settings
        self._token_factory = token_factory

    async def retrieve_context(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        if self._settings.dry_run_ai:
            return [
                {
                    "id": "adr-0012",
                    "title": "ADR-0012: Branch Protection & Keyless Entra Auth Standard",
                    "snippet": (
                        "All service mutations in Azure DevOps must target feature/* or feat/* branches "
                        "and open a Pull Request with at least one human approval via Adaptive Card."
                    ),
                    "score": 0.96,
                },
                {
                    "id": "runbook-aca-net",
                    "title": "Runbook: Azure Container Apps VNet & DevBox Whitelisting",
                    "snippet": (
                        "Container Apps ingress restricts public access to corporate DevBox NAT CIDRs "
                        "and Microsoft Graph webhook ranges."
                    ),
                    "score": 0.91,
                },
            ]

        bearer = self._token_factory.get_search_bearer_token()
        url = (
            f"{self._settings.azure_search_endpoint.rstrip('/')}"
            f"/indexes/{self._settings.azure_search_index}/docs/search?api-version=2024-07-01"
        )
        headers = {
            "Authorization": f"Bearer {bearer}",
            "Content-Type": "application/json",
        }
        payload = {
            "search": query,
            "queryType": "semantic",
            "top": top_k,
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return [
                {
                    "id": doc.get("id", "doc"),
                    "title": doc.get("title", "Enterprise Doc"),
                    "snippet": doc.get("content", "")[:400],
                    "score": doc.get("@search.score", 0.0),
                }
                for doc in data.get("value", [])
            ]
