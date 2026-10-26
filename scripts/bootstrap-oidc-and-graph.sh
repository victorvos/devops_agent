#!/usr/bin/env bash
# ============================================================================
# Bootstrap Script:
# 1. Configures Workload Identity Federation (OIDC) on a Deployment Identity
#    for GitHub Actions.
# 2. Assigns Microsoft Graph App Roles (ChatMessage.Send / Mail.Send) to the
#    ACA User-Assigned Managed Identity (since Graph App Roles are Microsoft
#    Graph directory objects rather than Azure RM resources).
# ============================================================================
set -euo pipefail

TENANT_ID="${AZURE_TENANT_ID:-$(az account show --query tenantId -o tsv)}"
SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID:-$(az account show --query id -o tsv)}"
RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-rg-lgorchestrator-dev}"
GITHUB_REPO="${GITHUB_REPO:-your-github-username/azure-langgraph-blueprint}"
GITHUB_ENV="${GITHUB_ENV_NAME:-dev}"
UAMI_NAME="${UAMI_NAME:-id-lgorchestrator-dev}"

echo "==> [1/2] Configuring GitHub OIDC Federated Credential (if DEPLOY_APP_OBJECT_ID is set)..."
if [[ -n "${DEPLOY_APP_OBJECT_ID:-}" ]]; then
  az ad app federated-credential create \
    --id "$DEPLOY_APP_OBJECT_ID" \
    --parameters "{
      \"name\": \"github-oidc-${GITHUB_ENV}\",
      \"issuer\": \"https://token.actions.githubusercontent.com\",
      \"subject\": \"repo:${GITHUB_REPO}:environment:${GITHUB_ENV}\",
      \"description\": \"Keyless OIDC deployment from GitHub Actions\",
      \"audiences\": [\"api://AzureADTokenExchange\"]
    }"
  echo "    Federated credential created for repo:${GITHUB_REPO}:environment:${GITHUB_ENV}"
else
  echo "    Skipping step 1 (set DEPLOY_APP_OBJECT_ID to create GitHub federated credential)."
fi

echo "==> [2/2] Assigning Microsoft Graph App Roles to ACA User-Assigned Managed Identity..."
if az identity show --name "$UAMI_NAME" --resource-group "$RESOURCE_GROUP" >/dev/null 2>&1; then
  UAMI_PRINCIPAL_ID=$(az identity show --name "$UAMI_NAME" --resource-group "$RESOURCE_GROUP" --query principalId -o tsv)
  GRAPH_SP_ID=$(az ad sp show --id 00000003-0000-0000-c000-000000000000 --query id -o tsv)

  # Mail.Send AppRole ID in Microsoft Graph: b633e1c5-b582-4048-a93e-9f11b44c7e96
  MAIL_SEND_ROLE_ID="b633e1c5-b582-4048-a93e-9f11b44c7e96"

  az rest --method POST \
    --uri "https://graph.microsoft.com/v1.0/servicePrincipals/${UAMI_PRINCIPAL_ID}/appRoleAssignments" \
    --headers "Content-Type=application/json" \
    --body "{
      \"principalId\": \"${UAMI_PRINCIPAL_ID}\",
      \"resourceId\": \"${GRAPH_SP_ID}\",
      \"appRoleId\": \"${MAIL_SEND_ROLE_ID}\"
    }" || echo "    Role assignment already exists or requires Directory.ReadWrite.All."
else
  echo "    Managed Identity ${UAMI_NAME} not found yet. Run Bicep deployment first."
fi
