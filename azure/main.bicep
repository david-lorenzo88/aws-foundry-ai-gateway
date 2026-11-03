// Foundry resource + project with Application Insights connected, ready to govern the AWS agent.
// The AI Gateway (API Management) is added afterwards from the Foundry portal: Manage > AI Gateway.

param location string = resourceGroup().location
param foundryName string = 'foundry-aws-gateway-${uniqueString(resourceGroup().id)}'
param projectName string = 'proj-aws-gateway'

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${foundryName}'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${foundryName}'
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
  }
}

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: foundryName
  location: location
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    allowProjectManagement: true
    customSubDomainName: foundryName
    publicNetworkAccess: 'Enabled'
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: foundry
  name: projectName
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: projectName
    description: 'Governs the AWS logistics agent through the AI Gateway.'
  }
}

// Shows up under Manage > Project details > Connected resources (AppInsights). Foundry Control Plane
// reads custom-agent traces from here.
resource appInsightsConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: project
  name: appInsights.name
  properties: {
    category: 'AppInsights'
    target: appInsights.id
    authType: 'ApiKey'
    isSharedToAll: false
    credentials: {
      key: appInsights.properties.ConnectionString
    }
    metadata: {
      ApiType: 'Azure'
      ResourceId: appInsights.id
    }
  }
}

output foundryName string = foundry.name
output projectName string = project.name
output appInsightsName string = appInsights.name
