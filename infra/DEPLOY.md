# Deployment Guide

Deploy the DevOps Agent into your **workload** (e.g. resource group `a-azwl`). Within the workload you may have **multiple projects**; each deployment uses a **project name** (e.g. `domeinteam_devops_agent`) so all resources are named after that project. **All credentials** are stored in the project’s Key Vault.

**Workload vs project:** Deploy into the workload’s **resource group**. Pass **projectName** = your project (e.g. `domeinteam_devops_agent`). Resources will be named from the project: Managed Identity `domeinteam-devops-agent-id`, Container App `domeinteam-devops-agent-app`, etc. (underscores in projectName are turned into hyphens for Azure resource names.)

**Workload setup (get the Container App running):** Follow **Steps 1–7 in order**. The Container App will not run correctly until the image is built (Step 2), Bicep has been deployed (Step 3), the AI key is in Key Vault (Step 4), the Managed Identity is in Azure DevOps (Step 5), and the identity has ACR pull (Step 6). Step 7 confirms the app is healthy.

---

## What gets deployed

### Core (always)

Names are derived from **projectName** (e.g. `domeinteam_devops_agent` → underscores become hyphens in resource names).

| Resource | Name | Purpose |
|----------|------|---------|
| Managed Identity | `<project>-id` | Auth to DevOps API, ACR pull, Key Vault access |
| Key Vault | `<project>kv<unique>` | Project-scoped vault for all agent credentials |
| Container App Env | `<project>-env` | Hosting environment |
| Container App | `<project>-app` | The agent (FastAPI + uvicorn) |

### Optional (opt-in via parameters)

| Resource | Parameter | Purpose |
|----------|-----------|---------|
| Service Bus | `deployServiceBus=true` | Queue-based processing; connection string stored in Key Vault |
| VNet integration | `subnetId='...'` | Place the app inside your VNet |

Without Service Bus, the app processes requests **directly** in a background task — simpler, no extra cost.

### Key Vault secrets (used by the Container App)

| Secret name | Who creates it | Purpose |
|-------------|-----------------|---------|
| `AzureAIFoundryApiKey` | **You** (after deploy) | AI Foundry API key; required for the app to call the LLM |
| `ServiceBusConnectionString` | **Bicep** (when `deployServiceBus=true`) | Service Bus connection string; only present if you deploy Service Bus |

---

## Prerequisites

- **Azure CLI** logged in to the subscription where your workload lives.
- **Resource group** for the workload (use an existing one or create it).

```bash
az login
az account show --query '{sub: name, id: id}' -o table
az bicep version
```

Set your **workload** resource group, **project** name, and ACR name:

```bash
RG="a-azwl"                          # workload resource group (e.g. a-azwl)
PROJECT_NAME="domeinteam_devops_agent" # project name — used for all resource names
ACR_NAME="youracr"                    # short name for ACR
```

---

## Step 1: Create ACR (if you don't have one)

The Container App pulls the agent image from Azure Container Registry. Create an ACR in the same subscription (same RG or a shared RG).

```bash
az acr create -n $ACR_NAME -g $RG --sku Basic
```

---

## Step 2: Build and push the container image

Run from the repo root. The image name must match what Bicep uses (`devops-agent:latest` unless you override `imageTag`).

```bash
# From the devops_agent repo root
az acr build --registry $ACR_NAME --image devops-agent:latest .
```

---

## Step 3: Deploy with Bicep

Deploys Managed Identity, Key Vault, Container App Environment, and Container App (plus optional Service Bus).

**What to use where:**

| Parameter | Meaning | Example |
|-----------|---------|---------|
| `devopsOrgUrl` | Azure DevOps **organization** URL | `https://dev.azure.com/your-org` |
| `devopsProject` | Azure DevOps **project** name (the project that contains the target repo) | `devops_agent` or `MyTeam` |
| `devopsRepository` | **Target** repo the agent operates on: the Git repo the agent will search (metadata/file fetch) and create branches/PRs in. Use your **team code repo** here so the agent uses it as context for new features and investigations. Not the repo where the devops_agent source code lives. | e.g. `team-backend` or `product-api` — your team’s main codebase |
| `aiFoundryEndpoint` | Azure AI Foundry **project** endpoint (where your model is deployed; get it from AI Studio) | `https://your-ai-project.services.ai.azure.com` |

**Minimal (no VNet, no Service Bus):**

```bash
# Use your team code repo for devopsProject + devopsRepository (agent uses it for context/features)
az deployment group create -g $RG -f infra/main.bicep \
  -p projectName=$PROJECT_NAME \
     devopsOrgUrl='https://dev.azure.com/your-org' \
     devopsProject='YourTeamProject' \
     devopsRepository='your-team-repo' \
     acrName=$ACR_NAME
```

**With AI Foundry endpoint (recommended so the app can call the LLM):**

```bash
az deployment group create -g $RG -f infra/main.bicep \
  -p projectName=$PROJECT_NAME \
     devopsOrgUrl='https://dev.azure.com/your-org' \
     devopsProject='YourTeamProject' \
     devopsRepository='your-team-repo' \
     acrName=$ACR_NAME \
     aiFoundryEndpoint='https://your-ai-project.services.ai.azure.com'
```

**With VNet + Service Bus:**

```bash
az deployment group create -g $RG -f infra/main.bicep \
  -p projectName=$PROJECT_NAME \
     devopsOrgUrl='https://dev.azure.com/your-org' \
     devopsProject='YourTeamProject' \
     devopsRepository='your-team-repo' \
     acrName=$ACR_NAME \
     aiFoundryEndpoint='https://your-ai-project.services.ai.azure.com' \
     subnetId='/subscriptions/<sub-id>/resourceGroups/<rg>/providers/Microsoft.Network/virtualNetworks/<vnet>/subnets/<subnet>' \
     deployServiceBus=true
```

Or use the parameters file:

```bash
cp infra/parameters.bicepparam infra/parameters.local.bicepparam
# edit it, then:
az deployment group create -g $RG -f infra/main.bicep -p infra/parameters.local.bicepparam
```

---

## Step 4: Add the AI Foundry API key to Key Vault

The Container App reads `AZURE_AI_FOUNDRY_API_KEY` from Key Vault (secret name **`AzureAIFoundryApiKey`**). Add it after the first deployment:

```bash
# Get the vault name from the deployment output
KV=$(az deployment group show -g $RG -n main --query 'properties.outputs.keyVaultName.value' -o tsv)

# Set your AI Foundry API key (get it from the Azure AI Foundry / AI Studio portal)
az keyvault secret set --vault-name $KV --name AzureAIFoundryApiKey --value "your-api-key"
```

Until this secret exists, the app may fail to start when it calls the LLM. No redeploy is needed after adding the secret — the Container App will use it on the next run.

---

## Step 5: Grant the MI access to Azure DevOps

The Bicep creates the Managed Identity, but you need to add it to your Azure DevOps org.

```bash
# Get the principal ID
MI_PRINCIPAL=$(az deployment group show -g $RG -n main \
  --query 'properties.outputs.managedIdentityPrincipalId.value' -o tsv)

echo "Add this principal to Azure DevOps: $MI_PRINCIPAL"
```

In Azure DevOps: **Organization Settings > Users > Add users** — search by the principal ID, grant Basic access, add to the project.

---

## Step 6: Grant ACR pull permission

The Container App’s Managed Identity needs **AcrPull** on the registry so it can pull the image. The identity name is **projectName with underscores replaced by hyphens** plus `-id` (e.g. `domeinteam_devops_agent` → `domeinteam-devops-agent-id`).

```bash
# Identity name: projectName with _ → - , then '-id'
IDENTITY_NAME="${PROJECT_NAME//_/-}-id"
MI_PRINCIPAL=$(az identity show -n "$IDENTITY_NAME" -g $RG --query principalId -o tsv)
ACR_ID=$(az acr show -n $ACR_NAME -g $RG --query id -o tsv)

az role assignment create --assignee $MI_PRINCIPAL --role AcrPull --scope $ACR_ID
```

---

## Step 7: Verify

```bash
FQDN=$(az deployment group show -g $RG -n main \
  --query 'properties.outputs.containerAppFqdn.value' -o tsv)

curl https://$FQDN/health
# {"status":"healthy"}
```

**Quick checklist (workload → running Container App):**  
1) Workload RG + ACR, 2) Build image, 3) Deploy Bicep with **projectName** (e.g. `domeinteam_devops_agent`), 4) Add `AzureAIFoundryApiKey` to Key Vault, 5) Add MI to Azure DevOps, 6) Grant MI AcrPull on ACR, 7) Verify `/health`.

---

## Triggering from a work item

To run the agent when someone comments `@agent` on a work item, set up a **service hook** in Azure DevOps and a **Power Automate** flow that POSTs to `https://<FQDN>/api/investigate`. Full steps: **[../docs/TRIGGER-FROM-WORK-ITEM.md](../docs/TRIGGER-FROM-WORK-ITEM.md)**.

---

## CLI Troubleshooting

### Deployment failed

```bash
az deployment group show -g $RG -n main --query 'properties.error' -o json
az deployment group create -g $RG -f infra/main.bicep -p ... --verbose
```

### Container App not starting

Use your **project** app name (projectName with `_` → `-` plus `-app`, e.g. `domeinteam-devops-agent-app`):

```bash
APP_NAME="${PROJECT_NAME//_/-}-app"
az containerapp logs show -n $APP_NAME -g $RG --follow
az containerapp revision list -n $APP_NAME -g $RG -o table
```

### Check environment variables

```bash
APP_NAME="${PROJECT_NAME//_/-}-app"
az containerapp show -n $APP_NAME -g $RG \
  --query 'properties.template.containers[0].env[].{name:name, value:value}' -o table
```

### Managed Identity issues

Identity name is projectName with `_` → `-` plus `-id` (e.g. `domeinteam-devops-agent-id`):

```bash
IDENTITY_NAME="${PROJECT_NAME//_/-}-id"
az identity show -n $IDENTITY_NAME -g $RG -o table
MI_PRINCIPAL=$(az identity show -n $IDENTITY_NAME -g $RG --query principalId -o tsv)
az role assignment list --assignee $MI_PRINCIPAL -o table
```

### ACR pull fails

Ensure the MI has AcrPull (Step 6). Use your ACR name:

```bash
az acr repository show-tags -n $ACR_NAME --repository devops-agent -o table
az role assignment create --assignee $MI_PRINCIPAL --role AcrPull \
  --scope $(az acr show -n $ACR_NAME --query id -o tsv)
```

### Update after code changes

```bash
az acr build --registry $ACR_NAME --image devops-agent:latest .
APP_NAME="${PROJECT_NAME//_/-}-app"
az containerapp update -n $APP_NAME -g $RG \
  --image ${ACR_NAME}.azurecr.io/devops-agent:latest
```

### VNet / NSG blocking traffic

The subnet needs outbound HTTPS (443) to:
- `*.azurecr.io` (image pull)
- `dev.azure.com` (DevOps API)
- `*.services.ai.azure.com` (AI Foundry)
- `*.servicebus.windows.net` (if Service Bus enabled)

```bash
curl -v https://$FQDN/health
```

### Delete everything

```bash
APP_NAME="${PROJECT_NAME//_/-}-app"
az containerapp delete -n $APP_NAME -g $RG --yes
# Or delete the full deployment (removes all resources in the workload RG):
az group delete -n $RG --yes --no-wait
```
