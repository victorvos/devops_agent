@description('Azure region for networking resources.')
param location string

@description('Name of the spoke Virtual Network.')
param vnetName string

@description('Address prefix for the VNet.')
param vnetAddressPrefix string

@description('Subnet CIDR for Azure Container Apps infrastructure.')
param acaSubnetPrefix string

@description('Subnet CIDR for Private Endpoints.')
param privateEndpointSubnetPrefix string

@description('Whitelisted DevBox CIDRs for NSG inbound rules.')
param devBoxAllowedCidrs array = []

@description('Optional Microsoft Dev Center VNet Resource ID for VNet peering.')
param devBoxVnetResourceId string = ''

resource acaNsg 'Microsoft.Network/networkSecurityGroups@2023-11-01' = {
  name: 'nsg-${vnetName}-aca'
  location: location
  properties: {
    securityRules: [
      {
        name: 'Allow-DevBox-And-Corp-HTTPS'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourcePortRange: '*'
          destinationPortRange: '443'
          sourceAddressPrefixes: empty(devBoxAllowedCidrs) ? [ 'VirtualNetwork' ] : devBoxAllowedCidrs
          destinationAddressPrefix: acaSubnetPrefix
        }
      }
      {
        name: 'Allow-AzureLoadBalancer'
        properties: {
          priority: 110
          direction: 'Inbound'
          access: 'Allow'
          protocol: '*'
          sourcePortRange: '*'
          destinationPortRange: '*'
          sourceAddressPrefix: 'AzureLoadBalancer'
          destinationAddressPrefix: acaSubnetPrefix
        }
      }
    ]
  }
}

resource peNsg 'Microsoft.Network/networkSecurityGroups@2023-11-01' = {
  name: 'nsg-${vnetName}-pe'
  location: location
  properties: {
    securityRules: [
      {
        name: 'Allow-ACA-To-PrivateEndpoints'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourcePortRange: '*'
          destinationPortRanges: [
            '443'
            '5432'
          ]
          sourceAddressPrefix: acaSubnetPrefix
          destinationAddressPrefix: privateEndpointSubnetPrefix
        }
      }
    ]
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' = {
  name: vnetName
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: [
        vnetAddressPrefix
      ]
    }
    subnets: [
      {
        name: 'snet-aca-infra'
        properties: {
          addressPrefix: acaSubnetPrefix
          networkSecurityGroup: {
            id: acaNsg.id
          }
          delegations: [
            {
              name: 'aca-delegation'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        name: 'snet-private-endpoints'
        properties: {
          addressPrefix: privateEndpointSubnetPrefix
          privateEndpointNetworkPolicies: 'Disabled'
          networkSecurityGroup: {
            id: peNsg.id
          }
        }
      }
    ]
  }
}

// Optional VNet Peering to Corporate DevBox / Dev Center VNet
resource peeringToDevBox 'Microsoft.Network/virtualNetworks/virtualNetworkPeerings@2023-11-01' = if (!empty(devBoxVnetResourceId)) {
  parent: vnet
  name: 'peer-to-devbox-vnet'
  properties: {
    allowVirtualNetworkAccess: true
    allowForwardedTraffic: true
    allowGatewayTransit: false
    useRemoteGateways: false
    remoteVirtualNetwork: {
      id: devBoxVnetResourceId
    }
  }
}

output vnetId string = vnet.id
output acaSubnetId string = vnet.properties.subnets[0].id
output privateEndpointSubnetId string = vnet.properties.subnets[1].id
