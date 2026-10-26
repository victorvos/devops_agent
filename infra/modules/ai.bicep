@description('Azure region for AI Foundry and AI Search.')
param location string

@description('Name of the Azure AI Services / OpenAI account.')
param aiServicesName string

@description('Name of the Azure AI Search service.')
param searchServiceName string

@description('Principal ID of the ACA User-Assigned Managed Identity.')
param uamiPrincipalId string

@description('Object ID of the DevBox engineer or group for local testing.')
param devAdminObjectId string

@description('Whitelisted DevBox CIDRs allowed through AI Foundry and AI Search firewalls.')
param devBoxAllowedCidrs array = []

@description('Enable public network access restricted to DevBox CIDRs + Azure Services.')
param enablePublicAccess bool = true

var aiIpRules = [for cidr in devBoxAllowedCidrs: {
  value: cidr
}]

resource aiServices 'Microsoft.CognitiveServices/accounts@2024-04-01-preview' = {
  name: aiServicesName
  location: location
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: aiServicesName
    // Keyless Only: Disable local API keys so all callers MUST use Entra ID tokens
    disableLocalAuth: true
    publicNetworkAccess: enablePublicAccess ? 'Enabled' : 'Disabled'
    networkAcls: {
      defaultAction: empty(devBoxAllowedCidrs) ? 'Allow' : 'Deny'
      bypass: 'AzureServices'
      ipRules: aiIpRules
    }
  }
}

// Model Deployment: GPT-4o for LangGraph planning & tool routing
resource gpt4oDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-04-01-preview' = {
  parent: aiServices
  name: 'gpt-4o'
  sku: {
    name: 'GlobalStandard'
    capacity: 30
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-4o'
      version: '2024-08-06'
    }
  }
}

resource searchService 'Microsoft.Search/searchServices@2024-03-01-preview' = {
  name: searchServiceName
  location: location
  sku: {
    name: 'basic'
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    publicNetworkAccess: enablePublicAccess ? 'enabled' : 'disabled'
    // Enforce Entra ID RBAC authentication
    disableLocalAuth: true
    networkRuleSet: {
      ipRules: aiIpRules
      bypass: 'AzureServices'
    }
    semanticSearch: 'free'
  }
}

// Built-in Role IDs
// Cognitive Services OpenAI User: 5e0bd9bd-7b93-4f28-af87-19fc36ad61bd
var cognitiveServicesOpenAiUserRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
// Search Index Data Reader: 1407120a-92aa-4202-b7e9-c0e197c71c8f
var searchIndexDataReaderRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '1407120a-92aa-4202-b7e9-c0e197c71c8f')

// Assign Cognitive Services OpenAI User to ACA Managed Identity
resource uamiOpenAiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiServices.id, uamiPrincipalId, cognitiveServicesOpenAiUserRoleId)
  scope: aiServices
  properties: {
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: cognitiveServicesOpenAiUserRoleId
  }
}

// Assign Search Index Data Reader to ACA Managed Identity
resource uamiSearchRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchService.id, uamiPrincipalId, searchIndexDataReaderRoleId)
  scope: searchService
  properties: {
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: searchIndexDataReaderRoleId
  }
}

// Assign Cognitive Services OpenAI User & Search Index Data Reader to DevBox Engineer for local debugging
resource devOpenAiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(devAdminObjectId) && devAdminObjectId != '00000000-0000-0000-0000-000000000000') {
  name: guid(aiServices.id, devAdminObjectId, cognitiveServicesOpenAiUserRoleId)
  scope: aiServices
  properties: {
    principalId: devAdminObjectId
    principalType: 'User'
    roleDefinitionId: cognitiveServicesOpenAiUserRoleId
  }
}

resource devSearchRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(devAdminObjectId) && devAdminObjectId != '00000000-0000-0000-0000-000000000000') {
  name: guid(searchService.id, devAdminObjectId, searchIndexDataReaderRoleId)
  scope: searchService
  properties: {
    principalId: devAdminObjectId
    principalType: 'User'
    roleDefinitionId: searchIndexDataReaderRoleId
  }
}

output aiFoundryEndpoint string = aiServices.properties.endpoint
output aiSearchEndpoint string = 'https://${searchService.name}.search.windows.net'
