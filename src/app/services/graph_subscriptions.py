from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any
import httpx

from app.auth.entra import KeylessEntraTokenFactory
from app.config import Settings

logger = logging.getLogger(__name__)


class GraphSubscriptionManager:
    """
    Manages Microsoft Graph Change Notification subscriptions for corporate MS Teams channels/chats
    and Outlook shared/service mailboxes using the ACA User-Assigned Managed Identity:
      - Handles short Graph webhook TTLs (Teams/Outlook subscriptions expire every 1-3 days)
      - Responds to `reauthorizationRequired` / `subscriptionRemoved` events on
        `/api/v1/webhooks/graph/lifecycle`
      - Supports both direct HTTPS webhooks (`notificationUrl`) and firewall-friendly
        Azure Event Grid Partner Topics (`EventGrid:?azuresubscriptionid=...`)!
    """

    def __init__(self, settings: Settings, token_factory: KeylessEntraTokenFactory) -> None:
        self._settings = settings
        self._token_factory = token_factory

    def build_subscription_specs(self) -> list[dict[str, Any]]:
        expiration = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
        base_url = self._settings.public_base_url.rstrip("/")
        specs: list[dict[str, Any]] = [
            {
                "changeType": "created",
                "notificationUrl": f"{base_url}/api/v1/webhooks/graph",
                "lifecycleNotificationUrl": f"{base_url}/api/v1/webhooks/graph/lifecycle",
                "resource": f"/users/{self._settings.trigger_mailbox_upn}/mailFolders('Inbox')/messages",
                "expirationDateTime": expiration,
                "clientState": self._settings.graph_expected_client_state,
            }
        ]
        if self._settings.trigger_team_id and self._settings.trigger_channel_id:
            specs.append(
                {
                    "changeType": "created,updated",
                    "notificationUrl": f"{base_url}/api/v1/webhooks/graph",
                    "lifecycleNotificationUrl": f"{base_url}/api/v1/webhooks/graph/lifecycle",
                    "resource": (
                        f"/teams/{self._settings.trigger_team_id}"
                        f"/channels/{self._settings.trigger_channel_id}/messages"
                    ),
                    "expirationDateTime": expiration,
                    "clientState": self._settings.graph_expected_client_state,
                }
            )
        return specs

    async def renew_subscription(self, subscription_id: str) -> dict[str, Any]:
        """Renews an expiring Microsoft Graph webhook subscription for another 48 hours."""
        new_expiration = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
        if self._settings.dry_run_m365:
            return {
                "subscriptionId": subscription_id,
                "status": "renewed_dry_run",
                "expirationDateTime": new_expiration,
            }

        bearer = self._token_factory.get_graph_bearer_token()
        url = f"https://graph.microsoft.com/v1.0/subscriptions/{subscription_id}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.patch(
                url,
                headers={"Authorization": f"Bearer {bearer}", "Content-Type": "application/json"},
                json={"expirationDateTime": new_expiration},
            )
            resp.raise_for_status()
            return resp.json()
