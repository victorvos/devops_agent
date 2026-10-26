@description('Azure region for PostgreSQL Flexible Server.')
param location string

@description('PostgreSQL Flexible Server name.')
param serverName string

@description('Microsoft Entra Tenant ID.')
param tenantId string

@description('Principal ID (OID) of the ACA User-Assigned Managed Identity.')
param uamiPrincipalId string

@description('Name of the ACA User-Assigned Managed Identity.')
param uamiPrincipalName string

@description('Object ID of the DevBox engineer or admin group.')
param devAdminObjectId string

@description('UPN or Display Name of the DevBox engineer or admin group.')
param devAdminPrincipalName string

@description('Whitelisted DevBox CIDRs allowed through the Postgres firewall when public access is enabled.')
param devBoxAllowedCidrs array = []

@description('Enable public network access (protected by firewall rules + Entra ID token auth).')
param enablePublicAccess bool = true

@description('Workload VNet ID for Private DNS Zone link.')
param vnetId string

@description('Private Endpoint Subnet ID.')
param privateEndpointSubnetId string

@description('Create Private Endpoint and Private DNS Zone.')
param usePrivateEndpoints bool = false

var databaseName = 'langgraph_state'

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2023-12-01-preview' = {
  name: serverName
  location: location
  sku: {
    name: 'Standard_B2ms'
    tier: 'Burstable'
  }
  properties: {
    version: '16'
    // 100% KEYLESS: Enable Entra ID Authentication ONLY and disable password authentication
    authConfig: {
      activeDirectoryAuth: 'Enabled'
      passwordAuth: 'Disabled'
      tenantId: tenantId
    }
    storage: {
      storageSizeGB: 32
      autoGrow: 'Enabled'
    }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    network: {
      publicNetworkAccess: enablePublicAccess ? 'Enabled' : 'Disabled'
    }
    highAvailability: {
      mode: 'Disabled'
    }
  }
}

// Register the Container App's User-Assigned Managed Identity as an Entra Administrator on PostgreSQL
// so it can immediately connect and run migrations without manual SQL bootstrap
resource uamiEntraAdmin 'Microsoft.DBforPostgreSQL/flexibleServers/administrators@2023-12-01-preview' = {
  parent: postgres
  name: uamiPrincipalId
  properties: {
    principalName: uamiPrincipalName
    principalType: 'ServicePrincipal'
    tenantId: tenantId
  }
}

// Register the DevBox Engineer / Group as a second Entra Administrator for DevBox inspection & debugging
resource devEntraAdmin 'Microsoft.DBforPostgreSQL/flexibleServers/administrators@2023-12-01-preview' = if (!empty(devAdminObjectId) && devAdminObjectId != '00000000-0000-0000-0000-000000000000') {
  parent: postgres
  name: devAdminObjectId
  dependsOn: [
    uamiEntraAdmin
  ]
  properties: {
    principalName: devAdminPrincipalName
    principalType: 'User'
    tenantId: tenantId
  }
}

// Create the LangGraph checkpoint database
resource langgraphDb 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-12-01-preview' = {
  parent: postgres
  name: databaseName
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

// Allow Azure services (ACA egress) when public access mode is used in Dev
resource allowAzureServices 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-12-01-preview' = if (enablePublicAccess) {
  parent: postgres
  name: 'AllowAllWindowsAzureIps'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

// Note: For single/32 DevBox IPs passed in devBoxAllowedCidrs, strip '/32' and register explicit firewall rules
@batchSize(1)
resource devBoxFirewallRules 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-12-01-preview' = [for (cidr, idx) in devBoxAllowedCidrs: if (enablePublicAccess && endsWith(cidr, '/32')) {
  parent: postgres
  name: 'Allow-DevBox-IP-${idx}'
  properties: {
    startIpAddress: replace(cidr, '/32', '')
    endIpAddress: replace(cidr, '/32', '')
  }
}]

// Optional Private Endpoint + Private DNS Zone for Zero-Trust Mode
resource pgPrivateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = if (usePrivateEndpoints) {
  name: 'privatelink.postgres.database.azure.com'
  location: 'global'
}

resource pgPrivateDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = if (usePrivateEndpoints) {
  parent: pgPrivateDnsZone
  name: 'link-${serverName}'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnetId
    }
  }
}

resource pgPrivateEndpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = if (usePrivateEndpoints) {
  name: 'pe-${serverName}'
  location: location
  properties: {
    subnet: {
      id: privateEndpointSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'plsc-${serverName}'
        properties: {
          privateLinkServiceId: postgres.id
          groupIds: [
            'postgresqlServer'
          ]
        }
      }
    ]
  }
}

resource pgDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = if (usePrivateEndpoints) {
  parent: pgPrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'postgres-dns-config'
        properties: {
          privateDnsZoneId: pgPrivateDnsZone.id
        }
      }
    ]
  }
}

output fqdn string = postgres.properties.fullyQualifiedDomainName
output databaseName string = databaseName
