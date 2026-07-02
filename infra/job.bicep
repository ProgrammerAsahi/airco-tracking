@description('Full image reference in Azure Container Registry.')
param containerImage string

param jobName string = 'airco-tracker-job'
param containerEnvironmentName string
param acrName string
param identityName string
param storageAccountName string
param communicationServiceName string
param keyVaultUrl string
param emailFrom string
param emailTo string
@allowed([
  'zh'
  'nl'
  'en'
])
param emailLang string = 'zh'

@description('Five-field UTC cron expression.')
param cronExpression string = '*/10 * * * *'
param minBtu string = '5000'
param maxPriceEur string = '1500'
@allowed([
  'disabled'
  'marketing_api'
])
param bolBackend string = 'disabled'
param keyVaultEnvMap string = ''

resource containerEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: containerEnvironmentName
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrName
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: identityName
}

resource job 'Microsoft.App/jobs@2025-01-01' = {
  name: jobName
  location: resourceGroup().location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    environmentId: containerEnvironment.id
    configuration: {
      registries: [
        {
          identity: identity.id
          server: registry.properties.loginServer
        }
      ]
      replicaRetryLimit: 2
      replicaTimeout: 300
      scheduleTriggerConfig: {
        cronExpression: cronExpression
        parallelism: 1
        replicaCompletionCount: 1
      }
      triggerType: 'Schedule'
    }
    template: {
      containers: [
        {
          name: 'airco-tracker'
          image: containerImage
          command: [
            'airco-tracker'
          ]
          args: [
            'check'
          ]
          env: [
            { name: 'APP_ENV', value: 'azure' }
            { name: 'EMAIL_BACKEND', value: 'azure_communication' }
            { name: 'EMAIL_TO', value: emailTo }
            { name: 'EMAIL_FROM', value: emailFrom }
            { name: 'EMAIL_LANG', value: emailLang }
            { name: 'ACS_ENDPOINT', value: 'https://${communicationServiceName}.communication.azure.com' }
            { name: 'AZURE_KEY_VAULT_URL', value: keyVaultUrl }
            { name: 'STATE_BACKEND', value: 'azure_blob' }
            { name: 'AZURE_STORAGE_ACCOUNT_URL', value: 'https://${storageAccountName}.blob.${environment().suffixes.storage}' }
            { name: 'AZURE_STORAGE_CONTAINER', value: 'airco-tracker' }
            { name: 'AZURE_STORAGE_BLOB', value: 'state.json' }
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
            { name: 'MIN_BTU', value: minBtu }
            { name: 'MAX_PRICE_EUR', value: maxPriceEur }
            { name: 'ALERT_ON_FIRST_SEEN', value: 'true' }
            { name: 'REQUEST_TIMEOUT_SECONDS', value: '25' }
            { name: 'BOL_BACKEND', value: bolBackend }
            { name: 'BOL_SEARCH_TERM', value: 'mobiele airco' }
            { name: 'BOL_MAX_PAGES', value: '5' }
            { name: 'KEY_VAULT_SECRET_MAP', value: keyVaultEnvMap }
          ]
          resources: {
            cpu: any('0.25')
            memory: '0.5Gi'
          }
        }
      ]
    }
  }
}

output jobName string = job.name
