@description('Azure region for Container Apps and ACR.')
param location string

@description('Azure Container Registry name (alphanumeric).')
param acrName string

@description('Azure Container Apps Environment name.')
param acaEnvName string

@description('Azure Container App name.')
param containerAppName string

@description('Log Analytics Workspace name.')
param logAnalyticsName string

@description('ACA delegated subnet resource ID.')
param acaSubnetId string

@description('Resource ID of the User-Assigned Managed Identity.')
param uamiResourceId string

@description('Client ID of the User-Assigned Managed Identity.')
param uamiClientId string

@description('Principal ID of the User-Assigned Managed Identity.')
param uamiPrincipalId string

@description('Name of the User-Assigned Managed Identity.')
param uamiName string

@description('Enable external HTTP ingress.')
param externalIngress bool = true

@description('Whitelisted corporate DevBox CIDRs allowed on ACA ingress.')
param devBoxAllowedCidrs array = []

@description('Whitelisted Microsoft Graph webhook / Reverse Proxy CIDRs allowed on ACA ingress.')
param graphWebhookAllowedCidrs array = []

@description('Container image reference.')
param containerImage string

@description('Snowflake Account Identifier.')
param snowflakeAccount string

@description('Snowflake Entra External OAuth token scope.')
param snowflakeOauthScope string

@description('Snowflake Warehouse name.')
param snowflakeWarehouse string

@description('Snowflake Role name.')
param snowflakeRole string

@description('Snowflake Analytics Database name (dbt models).')
param snowflakeAnalyticsDatabase string

@description('Snowflake State Database name (LangGraph checkpoints & audit logs).')
param snowflakeStateDatabase string

@description('Azure AI Foundry endpoint URL.')
param aiFoundryEndpoint string

@description('Azure AI Search endpoint URL.')
param aiSearchEndpoint string

@description('Microsoft Entra Tenant ID.')
param tenantId string

@description('Azure DevOps Organization URL.')
param adoOrgUrl string

@description('Default Azure DevOps Project.')
param adoDefaultProject string

@description('Default Azure DevOps dbt Repository name.')
param adoDbtRepoName string

@description('Inbound Entra API Client ID for JWT validation.')
param inboundApiClientId string

@description('Shared/Service Outlook Mailbox UPN watched for email triggers.')
param triggerMailboxUpn string = 'ai-orchestrator@contoso.com'

@description('Default Microsoft Teams ID watched for channel message triggers.')
param triggerTeamId string = ''

@description('Default Microsoft Teams Channel ID watched for channel message triggers.')
param triggerChannelId string = ''

// Combine DevBox CIDRs and Graph Webhook CIDRs into ACA ipSecurityRestrictions
var devBoxRules = [for (cidr, idx) in devBoxAllowedCidrs: {
  name: 'Allow-DevBox-${idx}'
  description: 'Whitelisted Corporate DevBox / VPN CIDR'
  ipAddressRange: cidr
  action: 'Allow'
}]

var graphRules = [for (cidr, idx) in graphWebhookAllowedCidrs: {
  name: 'Allow-Graph-Webhook-${idx}'
  description: 'Whitelisted Microsoft Graph / Reverse Proxy CIDR'
  ipAddressRange: cidr
  action: 'Allow'
}]

var ipSecurityRestrictions = concat(devBoxRules, graphRules)

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    // Keyless Only: Disable admin user; pulls authenticate via Managed Identity AcrPull
    adminUserEnabled: false
  }
}

// AcrPull role definition ID: 7f951dda-4ed3-4680-a7ca-43fe172d538d
var acrPullRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')

resource uamiAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, uamiPrincipalId, acrPullRoleId)
  scope: acr
  properties: {
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRoleId
  }
}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource acaEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: acaEnvName
  location: location
  properties: {
    vnetConfiguration: {
      infrastructureSubnetId: acaSubnetId
      internal: !externalIngress
    }
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: containerAppName
  location: location
  dependsOn: [
    uamiAcrPull
  ]
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${uamiResourceId}': {}
    }
  }
  properties: {
    managedEnvironmentId: acaEnv.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [
        {
          server: acr.properties.loginServer
          identity: uamiResourceId
        }
      ]
      ingress: {
        external: externalIngress
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
        // Firewall Whitelisting: Only DevBox CIDRs and Graph Webhook Proxy CIDRs are permitted
        ipSecurityRestrictions: ipSecurityRestrictions
      }
    }
    template: {
      containers: [
        {
          name: 'langgraph-orchestrator'
          image: containerImage
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          // ZERO SECRETS: All configuration uses endpoints & Managed Identity Client ID
          env: [
            {
              name: 'AZURE_CLIENT_ID'
              value: uamiClientId
            }
            {
              name: 'AZURE_TENANT_ID'
              value: tenantId
            }
            {
              name: 'SNOWFLAKE_ACCOUNT'
              value: snowflakeAccount
            }
            {
              name: 'SNOWFLAKE_OAUTH_SCOPE'
              value: snowflakeOauthScope
            }
            {
              name: 'SNOWFLAKE_WAREHOUSE'
              value: snowflakeWarehouse
            }
            {
              name: 'SNOWFLAKE_ROLE'
              value: snowflakeRole
            }
            {
              name: 'SNOWFLAKE_ANALYTICS_DATABASE'
              value: snowflakeAnalyticsDatabase
            }
            {
              name: 'SNOWFLAKE_STATE_DATABASE'
              value: snowflakeStateDatabase
            }
            {
              name: 'SNOWFLAKE_USER'
              value: uamiName
            }
            {
              name: 'AZURE_OPENAI_ENDPOINT'
              value: aiFoundryEndpoint
            }
            {
              name: 'AZURE_OPENAI_DEPLOYMENT'
              value: 'gpt-4o'
            }
            {
              name: 'AZURE_SEARCH_ENDPOINT'
              value: aiSearchEndpoint
            }
            {
              name: 'ADO_ORG_URL'
              value: adoOrgUrl
            }
            {
              name: 'ADO_DEFAULT_PROJECT'
              value: adoDefaultProject
            }
            {
              name: 'ADO_DBT_REPO_NAME'
              value: adoDbtRepoName
            }
            {
              name: 'INBOUND_API_CLIENT_ID'
              value: inboundApiClientId
            }
            {
              name: 'TRIGGER_MAILBOX_UPN'
              value: triggerMailboxUpn
            }
            {
              name: 'TRIGGER_TEAM_ID'
              value: triggerTeamId
            }
            {
              name: 'TRIGGER_CHANNEL_ID'
              value: triggerChannelId
            }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              initialDelaySeconds: 5
              periodSeconds: 15
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/ready'
                port: 8000
              }
              initialDelaySeconds: 5
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 5
        rules: [
          {
            name: 'http-concurrency'
            http: {
              metadata: {
                concurrentRequests: '25'
              }
            }
          }
        ]
      }
    }
  }
}

output containerAppFqdn string = containerApp.properties.configuration.ingress.fqdn
output acrLoginServer string = acr.properties.loginServer
