@description('Azure region for the User-Assigned Managed Identity.')
param location string

@description('Name of the User-Assigned Managed Identity.')
param uamiName string

resource uami 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: uamiName
  location: location
}

output resourceId string = uami.id
output principalId string = uami.properties.principalId
output clientId string = uami.properties.clientId
output name string = uami.name
