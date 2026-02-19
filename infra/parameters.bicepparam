using './main.bicep'

// Required — projectName = your project under the workload (e.g. domeinteam_devops_agent)
param projectName = 'domeinteam_devops_agent'
param devopsOrgUrl = 'https://dev.azure.com/<your-org>'
param devopsProject = '<your-project>'
param devopsRepository = '<your-repo>'
param acrName = '<your-acr-name>'

// Optional — uncomment to enable
// param subnetId = '/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Network/virtualNetworks/<vnet>/subnets/<subnet>'
// param aiFoundryEndpoint = 'https://<your-project>.services.ai.azure.com'
// param branchPrefix = 'agent'
// param deployServiceBus = true
