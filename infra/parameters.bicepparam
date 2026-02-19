using './main.bicep'

// Required
param workloadName = 'devops-agent'
param devopsOrgUrl = 'https://dev.azure.com/<your-org>'
param devopsProject = '<your-project>'
param devopsRepository = '<your-repo>'
param acrName = '<your-acr-name>'

// Optional — uncomment to enable
// param subnetId = '/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Network/virtualNetworks/<vnet>/subnets/<subnet>'
// param aiFoundryEndpoint = 'https://<your-project>.services.ai.azure.com'
// param deployServiceBus = true
// param keyVaultName = 'devops-agent-kv'
