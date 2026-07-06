@description('GitHub repository in owner/name format.')
param githubRepository string = 'ProgrammerAsahi/airco-tracking'

@description('Only this branch is allowed to deploy.')
param githubBranch string = 'main'

param identityName string = 'airco-github-deployer'

@description('Stable custom role definition GUID for the Airco GitHub deployer role.')
param deployRoleDefinitionGuid string = '3ba933f8-b598-41cd-a675-32daa4034b60'

var deployRoleDefinitionId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', deployRoleDefinitionGuid)

resource deployIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: resourceGroup().location
}

resource githubCredential 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = {
  parent: deployIdentity
  name: 'github-${last(split(githubRepository, '/'))}'
  properties: {
    audiences: [
      'api://AzureADTokenExchange'
    ]
    issuer: 'https://token.actions.githubusercontent.com'
    subject: 'repo:${githubRepository}:ref:refs/heads/${githubBranch}'
  }
}

module deployRole 'github-deployer-role.bicep' = {
  name: 'airco-github-deployer-role'
  scope: subscription()
  params: {
    roleDefinitionGuid: deployRoleDefinitionGuid
    assignableScope: resourceGroup().id
  }
}

resource resourceGroupDeployer 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, deployIdentity.id, deployRoleDefinitionId)
  properties: {
    principalId: deployIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: deployRoleDefinitionId
  }
  dependsOn: [
    deployRole
  ]
}

output clientId string = deployIdentity.properties.clientId
output principalId string = deployIdentity.properties.principalId
output deployRoleDefinitionId string = deployRoleDefinitionId
output subscriptionId string = subscription().subscriptionId
output tenantId string = tenant().tenantId
