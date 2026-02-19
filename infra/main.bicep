// ────────────────────────────────────────────────────────────────
// Azure DevOps Agent — lightweight Container App deployment
//
// Minimum:  Managed Identity + Container App Environment + Container App
// Optional: Service Bus (set deployServiceBus = true)
//           Key Vault role assignment (set keyVaultName)
//
// Usage:
//   az deployment group create -g <rg> -f infra/main.bicep \
//     -p workloadName='devops-agent' \
//        devopsOrgUrl='https://dev.azure.com/contoso' \
//        devopsProject='MyProject' \
//        devopsRepository='backend-api' \
//        acrName='myacr'
// ────────────────────────────────────────────────────────────────

targetScope = 'resourceGroup'

// ── Required ────────────────────────────────────────────────────

@description('Workload name — all resource names are derived from this')
@minLength(3)
@maxLength(24)
param workloadName string

@description('Azure DevOps organization URL')
param devopsOrgUrl string

@description('Azure DevOps project name')
param devopsProject string

@description('Azure DevOps repository name')
param devopsRepository string

@description('Azure Container Registry name (must already exist)')
param acrName string

// ── Optional ────────────────────────────────────────────────────

@description('Azure region')
param location string = resourceGroup().location

@description('Container image tag')
param imageTag string = 'latest'

@description('Subnet ID for VNet integration (leave empty to skip)')
param subnetId string = ''

@description('Azure AI Foundry endpoint URL')
param aiFoundryEndpoint string = ''

@description('Deploy Service Bus namespace + queue (set true for queue-based processing)')
param deployServiceBus bool = false

@description('Existing Key Vault name for role assignment (leave empty to skip)')
param keyVaultName string = ''

// ── Naming ──────────────────────────────────────────────────────

var identityName = '${workloadName}-id'
var envName = '${workloadName}-env'
var appName = '${workloadName}-app'

// ═══════════════════════════════════════════════════════════════
// CORE — always deployed
// ═══════════════════════════════════════════════════════════════

resource managedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
}

resource containerAppEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: envName
  location: location
  properties: {
    vnetConfiguration: subnetId != '' ? {
      infrastructureSubnetId: subnetId
      internal: false
    } : null
    zoneRedundant: false
  }
}

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
      }
      registries: [
        {
          server: '${acrName}.azurecr.io'
          identity: managedIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'devops-agent'
          image: '${acrName}.azurecr.io/devops-agent:${imageTag}'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'AZURE_DEVOPS_ORG_URL', value: devopsOrgUrl }
            { name: 'AZURE_DEVOPS_PROJECT', value: devopsProject }
            { name: 'AZURE_DEVOPS_REPOSITORY', value: devopsRepository }
            { name: 'DEVOPS_AUTH_MODE', value: 'managed_identity' }
            { name: 'MANAGED_IDENTITY_CLIENT_ID', value: managedIdentity.properties.clientId }
            { name: 'AZURE_AI_FOUNDRY_ENDPOINT', value: aiFoundryEndpoint }
            { name: 'SERVICE_BUS_CONNECTION_STR', value: deployServiceBus ? listKeys(serviceBusNamespace.id, serviceBusNamespace.apiVersion).primaryConnectionString : '' }
            { name: 'SERVICE_BUS_QUEUE_NAME', value: 'agent-requests' }
            { name: 'KEY_VAULT_URL', value: keyVaultName != '' ? keyVault.properties.vaultUri : '' }
            { name: 'LOG_LEVEL', value: 'INFO' }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 3
      }
    }
  }
}

// ═══════════════════════════════════════════════════════════════
// OPTIONAL — Service Bus (deployServiceBus = true)
// ═══════════════════════════════════════════════════════════════

resource serviceBusNamespace 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = if (deployServiceBus) {
  name: '${workloadName}-sb'
  location: location
  sku: { name: 'Standard'; tier: 'Standard' }
}

resource agentQueue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = if (deployServiceBus) {
  parent: serviceBusNamespace
  name: 'agent-requests'
  properties: {
    maxDeliveryCount: 3
    lockDuration: 'PT5M'
    defaultMessageTimeToLive: 'P1D'
    deadLetteringOnMessageExpiration: true
  }
}

var sbDataOwnerRoleId = '090c5cfd-751d-490a-894a-3ce6f1109419'

resource sbRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployServiceBus) {
  name: guid(serviceBusNamespace.id, managedIdentity.id, sbDataOwnerRoleId)
  scope: serviceBusNamespace
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', sbDataOwnerRoleId)
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ═══════════════════════════════════════════════════════════════
// OPTIONAL — Key Vault role assignment (keyVaultName != '')
// ═══════════════════════════════════════════════════════════════

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = if (keyVaultName != '') {
  name: keyVaultName
}

var kvSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

resource kvRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (keyVaultName != '') {
  name: guid(keyVault.id, managedIdentity.id, kvSecretsUserRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ── Outputs ─────────────────────────────────────────────────────

output containerAppFqdn string = containerApp.properties.configuration.ingress.fqdn
output containerAppName string = containerApp.name
output managedIdentityClientId string = managedIdentity.properties.clientId
output managedIdentityPrincipalId string = managedIdentity.properties.principalId
