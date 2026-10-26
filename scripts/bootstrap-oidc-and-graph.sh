#!/usr/bin/env bash
# ============================================================================
# Bootstrap Script (Azure DevOps + Entra ID — no GitHub Actions):
# 1. Configures Workload Identity Federation (OIDC) on a Deployment Identity
#    for an Azure DevOps ARM Service Connection.
#    Issuer:  https://vstoken.dev.azure.com/<ADO_ORG_ID>
#    Subject: sc://<ADO_ORG>/<ADO_PROJECT>/<SERVICE_CONNECTION_NAME>
# 2. Assigns Microsoft Graph App Roles (Mail.Send) to the ACA User-Assigned
#    Managed Identity (Graph App Roles are directory objects, not ARM).
# ============================================================================
set -euo pipefail

TENANT_ID="${AZURE_TENANT_ID:-$(az account show --query tenantId -o tsv)}"
SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID:-$(az account show --query id -o tsv)}"
RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-rg-lgorchestrator-dev}"
UAMI_NAME="${UAMI_NAME:-id-lgorchestrator-dev}"

# Azure DevOps organization (friendly name), org GUID, project, and WIF service connection
ADO_ORG="${ADO_ORG:-your-ado-org}"
ADO_ORG_ID="${ADO_ORG_ID:-00000000-0000-0000-0000-000000000000}"
ADO_PROJECT="${ADO_PROJECT:-DataPlatform}"
SERVICE_CONNECTION_NAME="${SERVICE_CONNECTION_NAME:-sc-entra-wif-lgorchestrator}"

echo "==> Tenant ${TENANT_ID} / subscription ${SUBSCRIPTION_ID}"
echo "==> [1/2] Configuring Azure DevOps WIF Federated Credential (if DEPLOY_APP_OBJECT_ID is set)..."
if [[ -n "${DEPLOY_APP_OBJECT_ID:-}" ]]; then
  az ad app federated-credential create \
    --id "$DEPLOY_APP_OBJECT_ID" \
    --parameters "{
      \"name\": \"ado-wif-${SERVICE_CONNECTION_NAME}\",
      \"issuer\": \"https://vstoken.dev.azure.com/${ADO_ORG_ID}\",
      \"subject\": \"sc://${ADO_ORG}/${ADO_PROJECT}/${SERVICE_CONNECTION_NAME}\",
      \"description\": \"Keyless OIDC from Azure DevOps Service Connection ${SERVICE_CONNECTION_NAME}\",
      \"audiences\": [\"api://AzureADTokenExchange\"]
    }"
  echo "    Federated credential created:"
  echo "      issuer:  https://vstoken.dev.azure.com/${ADO_ORG_ID}"
  echo "      subject: sc://${ADO_ORG}/${ADO_PROJECT}/${SERVICE_CONNECTION_NAME}"
  echo "    In Azure DevOps: Project Settings > Service connections > ${SERVICE_CONNECTION_NAME}"
  echo "    (Azure Resource Manager + Workload Identity federation, no client secret)."
else
  echo "    Skipping step 1 (set DEPLOY_APP_OBJECT_ID, ADO_ORG, ADO_ORG_ID, ADO_PROJECT,"
  echo "    SERVICE_CONNECTION_NAME to create the Azure DevOps federated credential)."
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
