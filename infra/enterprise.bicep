targetScope = 'resourceGroup'

@description('Workload / project prefix used for naming Azure resources.')
@minLength(3)
@maxLength(16)
param workloadName string = 'lgorchestrator'

@description('Deployment environment name (dev, staging, prod).')
@allowed([
  'dev'
  'staging'
  'prod'
])
param environmentName string = 'dev'

@description('Primary Azure region for all resources.')
param location string = resourceGroup().location

@description('Microsoft Entra Tenant ID for keyless authentication.')
param tenantId string = subscription().tenantId

@description('Object ID (Principal ID) of the engineering group or user who has DevBox admin access.')
param devAdminObjectId string

@description('UPN or Group display name of the DevBox admin.')
param devAdminPrincipalName string

@description('List of corporate DevBox NAT egress CIDRs or VPN subnets allowed through ACA and AI firewalls (e.g., ["20.105.44.12/32", "198.51.100.0/24"]).')
param devBoxAllowedCidrs array = []

@description('List of Microsoft Graph webhook proxy / Azure Front Door / Bot Service CIDRs allowed to POST to ACA ingress.')
param graphWebhookAllowedCidrs array = []

@description('VNet address space for the workload spoke VNet.')
param vnetAddressPrefix string = '10.40.0.0/16'

@description('Subnet CIDR for Azure Container Apps Environment infrastructure (minimum /23 required by ACA).')
param acaSubnetPrefix string = '10.40.0.0/23'

@description('Subnet CIDR for Private Endpoints (Snowflake PrivateLink, AI Foundry, AI Search, ACR, Service Bus).')
param privateEndpointSubnetPrefix string = '10.40.2.0/24'

@description('Optional resource ID of your Microsoft Dev Center / DevBox VNet to peer with the workload VNet.')
param devBoxVnetResourceId string = ''

@description('Enable Private Endpoints and disable public network access.')
param usePrivateEndpoints bool = false

@description('Enable external HTTP ingress on Azure Container Apps (protected by ipSecurityRestrictions).')
param acaExternalIngress bool = true

@description('Container image to deploy (defaults to Microsoft quickstart image until first CI/CD push).')
param containerImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('Snowflake Account Identifier (e.g., xy12345.west-europe.azure or xy12345.privatelink).')
param snowflakeAccount string = 'xy12345.west-europe.azure'

@description('Entra ID OAuth scope configured in Snowflake External OAuth security integration.')
param snowflakeOauthScope string = 'api://snowflake-entra-resource-id/.default'

@description('Snowflake Warehouse used by the LangGraph orchestrator.')
param snowflakeWarehouse string = 'WH_LANGGRAPH_XS'

@description('Snowflake Role assigned to the ACA Managed Identity.')
param snowflakeRole string = 'RL_LANGGRAPH_AGENT'

@description('Snowflake Database storing dbt analytics models.')
param snowflakeAnalyticsDatabase string = 'ANALYTICS_PROD'

@description('Snowflake Database storing LangGraph checkpoints and HITL audit logs.')
param snowflakeStateDatabase string = 'AGENT_STATE_DB'

@description('Optional: Deploy Azure Database for PostgreSQL Flexible Server alongside Snowflake (false by default since Snowflake is the primary database).')
param enablePostgres bool = false

@description('Azure DevOps Organization URL (e.g., https://dev.azure.com/my-org).')
param adoOrgUrl string = 'https://dev.azure.com/your-org'

@description('Default Azure DevOps Project name.')
param adoDefaultProject string = 'DataPlatform'

@description('Default Azure DevOps Repository containing the dbt project.')
param adoDbtRepoName string = 'dbt-snowflake-analytics'

@description('Inbound Entra App Registration Client ID used to validate Bearer tokens on /api/v1/dev/invoke and /api/v1/hitl/callback.')
param inboundApiClientId string = ''

@description('Shared/Service Outlook Mailbox UPN watched for email triggers (e.g., ai-orchestrator@contoso.com).')
param triggerMailboxUpn string = 'ai-orchestrator@contoso.com'

@description('Default Microsoft Teams ID watched for channel message triggers.')
param triggerTeamId string = ''

@description('Default Microsoft Teams Channel ID watched for channel message triggers.')
param triggerChannelId string = ''

var resourceSuffix = '${workloadName}-${environmentName}-${uniqueString(resourceGroup().id, workloadName, environmentName)}'
var shortSuffix = substring(replace(resourceSuffix, '-', ''), 0, 18)

// 1. User-Assigned Managed Identity (Used across ACA, Snowflake External OAuth, Azure DevOps, AI Foundry, Service Bus, and Azure Bot Service)
module identity 'modules/identity.bicep' = {
  name: 'deploy-identity'
  params: {
    location: location
    uamiName: 'id-${resourceSuffix}'
  }
}

// 2. Network & Firewall Topology (VNet, ACA Subnet, Private Endpoint Subnet for Snowflake PrivateLink & Azure services, DevBox Peering)
module network 'modules/network.bicep' = {
  name: 'deploy-network'
  params: {
    location: location
    vnetName: 'vnet-${resourceSuffix}'
    vnetAddressPrefix: vnetAddressPrefix
    acaSubnetPrefix: acaSubnetPrefix
    privateEndpointSubnetPrefix: privateEndpointSubnetPrefix
    devBoxAllowedCidrs: devBoxAllowedCidrs
    devBoxVnetResourceId: devBoxVnetResourceId
  }
}

// 3. Optional Azure Database for PostgreSQL Flexible Server (Disabled by default when Snowflake is used as the state & analytics DB)
module postgres 'modules/postgres.bicep' = if (enablePostgres) {
  name: 'deploy-postgres'
  params: {
    location: location
    serverName: 'psql-${resourceSuffix}'
    tenantId: tenantId
    uamiPrincipalId: identity.outputs.principalId
    uamiPrincipalName: identity.outputs.name
    devAdminObjectId: devAdminObjectId
    devAdminPrincipalName: devAdminPrincipalName
    devBoxAllowedCidrs: devBoxAllowedCidrs
    enablePublicAccess: !usePrivateEndpoints
    vnetId: network.outputs.vnetId
    privateEndpointSubnetId: network.outputs.privateEndpointSubnetId
    usePrivateEndpoints: usePrivateEndpoints
  }
}

// 4. Azure AI Foundry (OpenAI) & Azure AI Search (Keyless RBAC + DevBox IP Rules)
module ai 'modules/ai.bicep' = {
  name: 'deploy-ai'
  params: {
    location: location
    aiServicesName: 'ais-${shortSuffix}'
    searchServiceName: 'srch-${shortSuffix}'
    uamiPrincipalId: identity.outputs.principalId
    devAdminObjectId: devAdminObjectId
    devBoxAllowedCidrs: devBoxAllowedCidrs
    enablePublicAccess: !usePrivateEndpoints
  }
}

// 5. Azure Container Registry + Azure Container Apps Environment & App (with ipSecurityRestrictions + Snowflake OAuth Config)
module aca 'modules/aca.bicep' = {
  name: 'deploy-aca'
  params: {
    location: location
    acrName: 'cr${shortSuffix}'
    acaEnvName: 'cae-${resourceSuffix}'
    containerAppName: 'ca-${workloadName}-${environmentName}'
    logAnalyticsName: 'log-${resourceSuffix}'
    acaSubnetId: network.outputs.acaSubnetId
    uamiResourceId: identity.outputs.resourceId
    uamiClientId: identity.outputs.clientId
    uamiPrincipalId: identity.outputs.principalId
    uamiName: identity.outputs.name
    externalIngress: acaExternalIngress
    devBoxAllowedCidrs: devBoxAllowedCidrs
    graphWebhookAllowedCidrs: graphWebhookAllowedCidrs
    containerImage: containerImage
    snowflakeAccount: snowflakeAccount
    snowflakeOauthScope: snowflakeOauthScope
    snowflakeWarehouse: snowflakeWarehouse
    snowflakeRole: snowflakeRole
    snowflakeAnalyticsDatabase: snowflakeAnalyticsDatabase
    snowflakeStateDatabase: snowflakeStateDatabase
    aiFoundryEndpoint: ai.outputs.aiFoundryEndpoint
    aiSearchEndpoint: ai.outputs.aiSearchEndpoint
    tenantId: tenantId
    adoOrgUrl: adoOrgUrl
    adoDefaultProject: adoDefaultProject
    adoDbtRepoName: adoDbtRepoName
    inboundApiClientId: inboundApiClientId
    triggerMailboxUpn: triggerMailboxUpn
    triggerTeamId: triggerTeamId
    triggerChannelId: triggerChannelId
  }
}

// 6. Corporate MS Teams & Outlook Trigger Infrastructure:
//    - Keyless Azure Service Bus Namespace (Event Grid Partner Topic / Webhook buffer)
//    - Keyless Azure Bot Service (UserAssignedMSI) with MS Teams & Outlook Actionable Message channels
module triggers 'modules/triggers.bicep' = {
  name: 'deploy-triggers'
  params: {
    location: location
    serviceBusNamespaceName: 'sb-${resourceSuffix}'
    botServiceName: 'bot-${resourceSuffix}'
    uamiResourceId: identity.outputs.resourceId
    uamiClientId: identity.outputs.clientId
    uamiPrincipalId: identity.outputs.principalId
    tenantId: tenantId
    devAdminObjectId: devAdminObjectId
    containerAppBaseUrl: 'https://${aca.outputs.containerAppFqdn}'
    enablePublicAccess: !usePrivateEndpoints
  }
}

output containerAppFqdn string = aca.outputs.containerAppFqdn
output containerAppUrl string = 'https://${aca.outputs.containerAppFqdn}'
output acrLoginServer string = aca.outputs.acrLoginServer
output managedIdentityClientId string = identity.outputs.clientId
output managedIdentityPrincipalId string = identity.outputs.principalId
output snowflakeAccount string = snowflakeAccount
output aiFoundryEndpoint string = ai.outputs.aiFoundryEndpoint
output aiSearchEndpoint string = ai.outputs.aiSearchEndpoint
output serviceBusFqdn string = triggers.outputs.serviceBusFqdn
output botServiceName string = triggers.outputs.botServiceName
