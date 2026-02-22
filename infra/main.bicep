// ────────────────────────────────────────────────────────────────
// Azure DevOps Agent — lightweight Container App deployment
//
// Within a workload (e.g. resource group a-azwl) you may have multiple
// projects. Resource names are derived from projectName (e.g. domeinteam_devops_agent).
// All credentials are stored in the project's Key Vault.
//
// Usage:
//   az deployment group create -g <workload-rg> -f infra/main.bicep \
//     -p projectName='domeinteam_devops_agent' \
//        devopsOrgUrl='https://dev.azure.com/contoso' \
//        devopsProject='MyProject' \
//        devopsRepository='backend-api' \
//        acrName='myacr'
// ────────────────────────────────────────────────────────────────

targetScope = 'resourceGroup'

// ── Required ────────────────────────────────────────────────────

@description('Project name — all resource names are derived from this (e.g. domeinteam_devops_agent). Use one per project within the workload.')
@minLength(3)
@maxLength(64)
param projectName string

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

@description('Azure AI Foundry endpoint URL (non-secret; can stay in env)')
param aiFoundryEndpoint string = ''

@description('Prefix for agent-created branches (e.g. feature_ai)')
param branchPrefix string = 'feature_ai'

@description('Deploy Service Bus namespace + queue (set true for queue-based processing)')
param deployServiceBus bool = false

// ── Naming (sanitized for Azure: underscores → hyphens; Key Vault 3–24 chars, no underscores) ──

var sanitized = replace(projectName, '_', '-')
var identityName = '${sanitized}-id'
var envName = '${sanitized}-env'
var appName = '${sanitized}-app'
var sbNamespaceName = '${sanitized}-sb'
var storageAccountName = toLower('${take(replace(projectName, '_', ''), 15)}st${uniqueString(resourceGroup().id)}')
var keyVaultName = '${take(replace(projectName, '_', ''), 11)}kv${uniqueString(resourceGroup().id)}'

// ═══════════════════════════════════════════════════════════════
// CORE — always deployed
// ═══════════════════════════════════════════════════════════════

resource managedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
}

// Workload Key Vault — all credentials for this agent live here
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    softDeleteRetentionInDays: 7
  }
}

var kvSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

resource kvRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, managedIdentity.id, kvSecretsUserRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
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

// Workload Storage Account — used for distributed Job Store (Azure Tables)
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageAccountName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
  }
}

// ═══════════════════════════════════════════════════════════════
// OPTIONAL — Service Bus (deployServiceBus = true)
// ═══════════════════════════════════════════════════════════════

resource serviceBusNamespace 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = if (deployServiceBus) {
  name: sbNamespaceName
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

// Secret names in Key Vault — app expects these when using KV for credentials
var secretNameServiceBus = 'ServiceBusConnectionString'
var secretNameAIFoundryApiKey = 'AzureAIFoundryApiKey'

// Store Service Bus connection string in Key Vault when Service Bus is deployed
resource serviceBusConnectionSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (deployServiceBus) {
  parent: keyVault
  name: secretNameServiceBus
  properties: {
    value: listKeys(serviceBusNamespace.id, serviceBusNamespace.apiVersion).primaryConnectionString
  }
}

var secretNameTableAuth = 'AzureTableConnectionString'

// Store Table Storage connection string in Key Vault 
resource tableConnectionSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: secretNameTableAuth
  properties: {
    value: 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};EndpointSuffix=${environment().suffixes.storage};AccountKey=${storageAccount.listKeys().keys[0].value}'
  }
}

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  dependsOn: concat([keyVault, containerAppEnv, storageAccount, tableConnectionSecret], deployServiceBus ? [serviceBusConnectionSecret] : [])
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
      secrets: concat(
        deployServiceBus ? [
          {
            name: 'sb-connection'
            keyVaultUrl: '${keyVault.properties.vaultUri}secrets/${secretNameServiceBus}'
            identity: managedIdentity.id
          }
        ] : [],
        [
          {
            name: 'ai-foundry-api-key'
            keyVaultUrl: '${keyVault.properties.vaultUri}secrets/${secretNameAIFoundryApiKey}'
            identity: managedIdentity.id
          }
          {
            name: 'table-connection'
            keyVaultUrl: '${keyVault.properties.vaultUri}secrets/${secretNameTableAuth}'
            identity: managedIdentity.id
          }
        ]
      )
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
          env: concat(
            [
              { name: 'AZURE_DEVOPS_ORG_URL', value: devopsOrgUrl }
              { name: 'AZURE_DEVOPS_PROJECT', value: devopsProject }
              { name: 'AZURE_DEVOPS_REPOSITORY', value: devopsRepository }
              { name: 'DEVOPS_AUTH_MODE', value: 'managed_identity' }
              { name: 'MANAGED_IDENTITY_CLIENT_ID', value: managedIdentity.properties.clientId }
              { name: 'AZURE_AI_FOUNDRY_ENDPOINT', value: aiFoundryEndpoint }
              { name: 'SERVICE_BUS_QUEUE_NAME', value: 'agent-requests' }
              { name: 'KEY_VAULT_URL', value: keyVault.properties.vaultUri }
              { name: 'BRANCH_PREFIX', value: branchPrefix }
              { name: 'LOG_LEVEL', value: 'INFO' }
            ],
            deployServiceBus ? [{ name: 'SERVICE_BUS_CONNECTION_STR', secretRef: 'sb-connection' }] : [],
            [
              { name: 'AZURE_AI_FOUNDRY_API_KEY', secretRef: 'ai-foundry-api-key' }
              { name: 'AZURE_TABLE_CONNECTION_STR', secretRef: 'table-connection' }
            ]
          )
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 3
      }
    }
  }
}

// ── Outputs ─────────────────────────────────────────────────────

output containerAppFqdn string = containerApp.properties.configuration.ingress.fqdn
output containerAppName string = containerApp.name
output managedIdentityClientId string = managedIdentity.properties.clientId
output managedIdentityPrincipalId string = managedIdentity.properties.principalId
output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
