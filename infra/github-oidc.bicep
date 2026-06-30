@description('GitHub repository in owner/name format.')
param githubRepository string = 'ProgrammerAsahi/airco-tracking-nl'

@description('Only this branch is allowed to deploy.')
param githubBranch string = 'main'

param identityName string = 'airco-github-deployer'

resource deployIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: resourceGroup().location
}

resource githubCredential 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = {
  parent: deployIdentity
  name: 'github-${uniqueString(githubRepository, githubBranch)}'
  properties: {
    audiences: [
      'api://AzureADTokenExchange'
    ]
    issuer: 'https://token.actions.githubusercontent.com'
    subject: 'repo:${githubRepository}:ref:refs/heads/${githubBranch}'
  }
}

var contributorRole = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'b24988ac-6180-42a0-ab88-20f7382dd24c'
)

resource resourceGroupContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, deployIdentity.id, contributorRole)
  properties: {
    principalId: deployIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: contributorRole
  }
}

output clientId string = deployIdentity.properties.clientId
output principalId string = deployIdentity.properties.principalId
output subscriptionId string = subscription().subscriptionId
output tenantId string = tenant().tenantId
