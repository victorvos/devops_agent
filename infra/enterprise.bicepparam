using './main.bicep'

// ============================================================================
// Enterprise Workload & Environment Parameters (dbt + Snowflake + Azure + Teams/Outlook)
// Copy/adjust these values after pulling the repository into your work DevBox.
// ============================================================================

param workloadName = 'lgorchestrator'
param environmentName = 'dev'
param location = 'westeurope'

// Replace with your Entra User or Engineering Group Object ID (`az ad signed-in-user show --query id -o tsv`)
param devAdminObjectId = '00000000-0000-0000-0000-000000000000'
param devAdminPrincipalName = 'victor.vos@contoso.com'

// Whitelist your corporate DevBox egress NAT IP(s) and VPN/ExpressRoute subnets
// Tip: Run `curl -s https://ifconfig.me` inside your DevBox to get its public egress IP.
param devBoxAllowedCidrs = [
  '20.105.44.12/32'
  '198.51.100.0/24'
]

// Whitelist your Reverse Proxy / Azure Front Door / Graph Webhook / Bot Service proxy CIDRs
param graphWebhookAllowedCidrs = [
  '13.107.6.152/31'
  '13.107.18.10/31'
  '13.107.128.0/22'
]

// VNet & Subnet Layout
param vnetAddressPrefix = '10.40.0.0/16'
param acaSubnetPrefix = '10.40.0.0/23'
param privateEndpointSubnetPrefix = '10.40.2.0/24'

// Optional: Set to your Microsoft Dev Center VNet Resource ID if peering directly
param devBoxVnetResourceId = ''

// Set `usePrivateEndpoints = false` during initial DevBox hybrid testing (uses IP whitelisting),
// then flip to `true` for Zero-Trust production (Snowflake PrivateLink + Event Grid Partner Topic -> Service Bus).
param usePrivateEndpoints = false
param acaExternalIngress = true

// Snowflake + dbt Configuration (100% Keyless via Entra ID External OAuth)
param snowflakeAccount = 'xy12345.west-europe.azure'
param snowflakeOauthScope = 'api://snowflake-entra-resource-id/.default'
param snowflakeWarehouse = 'WH_LANGGRAPH_XS'
param snowflakeRole = 'RL_LANGGRAPH_AGENT'
param snowflakeAnalyticsDatabase = 'ANALYTICS_PROD'
param snowflakeStateDatabase = 'AGENT_STATE_DB'
param enablePostgres = false

// Azure DevOps target organization, project & dbt repository
param adoOrgUrl = 'https://dev.azure.com/your-enterprise-org'
param adoDefaultProject = 'DataPlatform'
param adoDbtRepoName = 'dbt-snowflake-analytics'
param inboundApiClientId = '00000000-0000-0000-0000-000000000000'

// Corporate MS Teams & Outlook Trigger Targets
param triggerMailboxUpn = 'ai-orchestrator@contoso.com'
param triggerTeamId = '00000000-0000-0000-0000-000000000000'
param triggerChannelId = '19:general@thread.tacv2'
