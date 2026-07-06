targetScope = 'subscription'

@description('Stable custom role definition GUID for the Airco GitHub deployer role.')
param roleDefinitionGuid string

@description('Scope where the custom role can be assigned.')
param assignableScope string

resource deployRoleDefinition 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: roleDefinitionGuid
  properties: {
    roleName: 'Airco GitHub Deployer Minimal'
    description: 'Least-privilege deployment role for Airco Tracker GitHub Actions. Allows ACR remote builds, ARM group deployments, Container Apps/App Jobs deployment and verification, and read access to existing dependent resources.'
    type: 'CustomRole'
    assignableScopes: [
      assignableScope
    ]
    permissions: [
      {
        actions: [
          'Microsoft.Resources/deployments/*'
          'Microsoft.Resources/subscriptions/resourceGroups/read'
          'Microsoft.Resources/resources/read'
          'Microsoft.ContainerRegistry/registries/read'
          'Microsoft.ContainerRegistry/registries/listBuildSourceUploadUrl/action'
          'Microsoft.ContainerRegistry/registries/scheduleRun/action'
          'Microsoft.ContainerRegistry/registries/runs/*'
          'Microsoft.App/containerApps/*'
          'Microsoft.App/jobs/*'
          'Microsoft.App/managedEnvironments/read'
          'Microsoft.App/managedEnvironments/join/action'
          'Microsoft.ManagedIdentity/userAssignedIdentities/read'
          'Microsoft.ManagedIdentity/userAssignedIdentities/assign/action'
          'Microsoft.Storage/storageAccounts/read'
          'Microsoft.Communication/communicationServices/read'
          'Microsoft.Communication/emailServices/read'
          'Microsoft.Communication/emailServices/domains/read'
          'Microsoft.KeyVault/vaults/read'
        ]
        notActions: [
          'Microsoft.App/containerApps/delete'
          'Microsoft.App/jobs/delete'
          'Microsoft.Resources/deployments/delete'
          'Microsoft.ContainerRegistry/registries/delete'
          'Microsoft.Storage/storageAccounts/delete'
          'Microsoft.KeyVault/vaults/delete'
          'Microsoft.Communication/communicationServices/delete'
          'Microsoft.Communication/emailServices/delete'
          'Microsoft.ManagedIdentity/userAssignedIdentities/delete'
        ]
        dataActions: []
        notDataActions: []
      }
    ]
  }
}

output roleDefinitionId string = deployRoleDefinition.id
