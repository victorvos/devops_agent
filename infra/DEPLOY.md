# Deployment Guide

Deploy the DevOps Agent to your Azure workload. Pass your **workload name** and the Bicep template derives everything else.

---

## What gets deployed

### Core (always)

| Resource | Name | Purpose |
|----------|------|---------|
| Managed Identity | `<workload>-id` | Auth to DevOps API + ACR pull |
| Container App Env | `<workload>-env` | Hosting environment |
| Container App | `<workload>-app` | The agent (FastAPI + uvicorn) |

### Optional (opt-in via parameters)

| Resource | Parameter | Purpose |
|----------|-----------|---------|
| Service Bus | `deployServiceBus=true` | Queue-based processing with retries |
| Key Vault role | `keyVaultName='...'` | MI gets Secrets User on existing KV |
| VNet integration | `subnetId='...'` | Place the app inside your VNet |

Without Service Bus, the app processes requests **directly** in a background task — simpler, no extra cost.

---

## Prerequisites

```bash
# Azure CLI logged in to the right subscription
az login
az account show --query '{sub: name, id: id}' -o table

# Verify Bicep is available
az bicep version
```

---

## Step 1: Create ACR (if you don't have one)

```bash
RG="rg-devops-agent"    # your resource group

az acr create -n devopsagentacr -g $RG --sku Basic
```

---

## Step 2: Build and push the container image

```bash
# Build in ACR (no local Docker needed)
az acr build --registry devopsagentacr --image devops-agent:latest .
```

---

## Step 3: Deploy with Bicep

**Minimal (3 resources):**

```bash
az deployment group create -g $RG -f infra/main.bicep \
  -p workloadName='devops-agent' \
     devopsOrgUrl='https://dev.azure.com/contoso' \
     devopsProject='MyProject' \
     devopsRepository='backend-api' \
     acrName='devopsagentacr'
```

**With VNet + Service Bus + Key Vault:**

```bash
az deployment group create -g $RG -f infra/main.bicep \
  -p workloadName='devops-agent' \
     devopsOrgUrl='https://dev.azure.com/contoso' \
     devopsProject='MyProject' \
     devopsRepository='backend-api' \
     acrName='devopsagentacr' \
     subnetId='/subscriptions/.../subnets/apps' \
     deployServiceBus=true \
     keyVaultName='devops-agent-kv'
```

Or use the parameters file:

```bash
cp infra/parameters.bicepparam infra/parameters.local.bicepparam
# edit it, then:
az deployment group create -g $RG -f infra/main.bicep -p infra/parameters.local.bicepparam
```

---

## Step 4: Grant the MI access to Azure DevOps

The Bicep creates the Managed Identity, but you need to add it to your Azure DevOps org.

```bash
# Get the principal ID
MI_PRINCIPAL=$(az deployment group show -g $RG -n main \
  --query 'properties.outputs.managedIdentityPrincipalId.value' -o tsv)

echo "Add this principal to Azure DevOps: $MI_PRINCIPAL"
```

In Azure DevOps: **Organization Settings > Users > Add users** — search by the principal ID, grant Basic access, add to the project.

---

## Step 5: Grant ACR pull permission

```bash
MI_PRINCIPAL=$(az identity show -n devops-agent-id -g $RG --query principalId -o tsv)
ACR_ID=$(az acr show -n devopsagentacr --query id -o tsv)

az role assignment create --assignee $MI_PRINCIPAL --role AcrPull --scope $ACR_ID
```

---

## Step 6: Verify

```bash
FQDN=$(az deployment group show -g $RG -n main \
  --query 'properties.outputs.containerAppFqdn.value' -o tsv)

curl https://$FQDN/health
# {"status":"healthy"}
```

---

## CLI Troubleshooting

### Deployment failed

```bash
az deployment group show -g $RG -n main --query 'properties.error' -o json
az deployment group create -g $RG -f infra/main.bicep -p ... --verbose
```

### Container App not starting

```bash
az containerapp logs show -n devops-agent-app -g $RG --follow
az containerapp revision list -n devops-agent-app -g $RG -o table
```

### Check environment variables

```bash
az containerapp show -n devops-agent-app -g $RG \
  --query 'properties.template.containers[0].env[].{name:name, value:value}' -o table
```

### Managed Identity issues

```bash
az identity show -n devops-agent-id -g $RG -o table
MI_PRINCIPAL=$(az identity show -n devops-agent-id -g $RG --query principalId -o tsv)
az role assignment list --assignee $MI_PRINCIPAL -o table
```

### ACR pull fails

```bash
az acr repository show-tags -n devopsagentacr --repository devops-agent -o table
# If missing AcrPull, grant it:
az role assignment create --assignee $MI_PRINCIPAL --role AcrPull \
  --scope $(az acr show -n devopsagentacr --query id -o tsv)
```

### Update after code changes

```bash
az acr build --registry devopsagentacr --image devops-agent:latest .
az containerapp update -n devops-agent-app -g $RG \
  --image devopsagentacr.azurecr.io/devops-agent:latest
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
az containerapp delete -n devops-agent-app -g $RG --yes
# Or delete the full deployment:
az group delete -n $RG --yes --no-wait
```
