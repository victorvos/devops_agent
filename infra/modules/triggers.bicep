@description('Azure region for Service Bus and Bot resources.')
param location string

@description('Name of the Azure Service Bus Namespace for buffering Graph / Event Grid / Teams triggers.')
param serviceBusNamespaceName string

@description('Name of the Azure Bot Service resource (Single-Tenant / User-Assigned Managed Identity).')
param botServiceName string

@description('Resource ID of the ACA User-Assigned Managed Identity.')
param uamiResourceId string

@description('Client ID (App ID) of the ACA User-Assigned Managed Identity.')
param uamiClientId string

@description('Principal ID (Object ID) of the ACA User-Assigned Managed Identity.')
param uamiPrincipalId string

@description('Microsoft Entra Tenant ID.')
param tenantId string

@description('Object ID of the DevBox engineer or group for local Service Bus debugging.')
param devAdminObjectId string

@description('Public HTTPS base URL of the Azure Container App (used for Bot messaging endpoint).')
param containerAppBaseUrl string

@description('Enable public network access on Service Bus (protected by IP rules or Private Endpoint).')
param enablePublicAccess bool = true

// 1. Azure Service Bus Namespace (100% Keyless: disableLocalAuth = true)
// Used for Pattern A (Microsoft Graph -> Event Grid Partner Topic -> Service Bus -> ACA Worker)
resource serviceBus 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {
  name: serviceBusNamespaceName
  location: location
  sku: {
    name: 'Standard'
    tier: 'Standard'
  }
  properties: {
    // Keyless Only: Disable SAS connection strings; require Entra ID RBAC tokens
    disableLocalAuth: true
    publicNetworkAccess: enablePublicAccess ? 'Enabled' : 'Disabled'
    minimumTlsVersion: '1.2'
  }
}

resource ingressQueue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: serviceBus
  name: 'q-teams-outlook-triggers'
  properties: {
    lockDuration: 'PT2M'
    maxDeliveryCount: 5
    deadLetteringOnMessageExpiration: true
    requiresDuplicateDetection: true
    duplicateDetectionHistoryTimeWindow: 'PT10M'
  }
}

// Built-in Role IDs for Azure Service Bus
// Azure Service Bus Data Receiver: 4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0
var sbDataReceiverRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0')
// Azure Service Bus Data Sender: 69a216fc-b8fb-44d8-bc22-1f3c2cd27a39
var sbDataSenderRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39')

resource uamiSbReceiver 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBus.id, uamiPrincipalId, sbDataReceiverRoleId)
  scope: serviceBus
  properties: {
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: sbDataReceiverRoleId
  }
}

resource uamiSbSender 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBus.id, uamiPrincipalId, sbDataSenderRoleId)
  scope: serviceBus
  properties: {
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: sbDataSenderRoleId
  }
}

resource devSbReceiver 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(devAdminObjectId) && devAdminObjectId != '00000000-0000-0000-0000-000000000000') {
  name: guid(serviceBus.id, devAdminObjectId, sbDataReceiverRoleId)
  scope: serviceBus
  properties: {
    principalId: devAdminObjectId
    principalType: 'User'
    roleDefinitionId: sbDataReceiverRoleId
  }
}

// 2. Keyless Azure Bot Service (Pattern C: Backed by User-Assigned Managed Identity — Zero Client Secrets!)
resource botService 'Microsoft.BotService/botServices@2022-09-15' = {
  name: botServiceName
  location: 'global'
  kind: 'azurebot'
  sku: {
    name: 'F0'
  }
  properties: {
    displayName: 'LangGraph Enterprise Architect Bot'
    description: 'Keyless Entra Managed Identity Bot for corporate Microsoft Teams & Outlook Actionable Messages'
    endpoint: '${containerAppBaseUrl}/api/v1/bot/messages'
    // 100% KEYLESS: Uses the ACA User-Assigned Managed Identity instead of an App Registration client secret
    msaAppType: 'UserAssignedMSI'
    msaAppId: uamiClientId
    msaAppTenantId: tenantId
    msaAppMSIResourceId: uamiResourceId
    publicNetworkAccess: 'Enabled'
  }
}

// Enable Microsoft Teams Channel on the Bot
resource teamsChannel 'Microsoft.BotService/botServices/channels@2022-09-15' = {
  parent: botService
  name: 'MsTeamsChannel'
  location: 'global'
  properties: {
    channelName: 'MsTeamsChannel'
    properties: {
      isEnabled: true
    }
  }
}

// Enable Outlook Actionable Messages Channel on the Bot
resource outlookChannel 'Microsoft.BotService/botServices/channels@2022-09-15' = {
  parent: botService
  name: 'OutlookChannel'
  location: 'global'
  properties: {
    channelName: 'OutlookChannel'
    properties: {}
  }
}

output serviceBusFqdn string = '${serviceBus.name}.servicebus.windows.net'
output serviceBusQueueName string = ingressQueue.name
output botServiceName string = botService.name
output botAppId string = uamiClientId
