@description('Full image reference in Azure Container Registry.')
param containerImage string

@maxLength(32)
param scannerJobName string = 'airco-tracker-job'
@maxLength(32)
param publisherJobName string = 'airco-alert-publisher-job'
@maxLength(32)
param reconcilerJobName string = 'airco-alert-reconciler-job'
@maxLength(32)
param cleanupJobName string = 'airco-alert-retention-job'
@maxLength(32)
param deliveryDlqCleanupJobName string = 'airco-delivery-dlq-cleanup'
@maxLength(32)
param coordinatorAppName string = 'airco-alert-fanout-coordinator'
@maxLength(32)
param fanoutAppName string = 'airco-alert-fanout-worker'
@maxLength(32)
param emailAppName string = 'airco-alert-email-worker'
@maxLength(32)
param deliveryReportAppName string = 'airco-alert-delivery-worker'
param containerEnvironmentName string
param acrName string
param scannerIdentityName string
param retentionIdentityName string
param publisherIdentityName string
param fanoutIdentityName string
param emailIdentityName string
param deliveryReportIdentityName string
param storageAccountName string
param serviceBusNamespaceName string
param communicationServiceName string
param keyVaultUrl string
param emailFrom string
param emailReplyTo string = 'support@airco-tracker.eu'
param appBaseUrl string = 'https://airco-tracker.eu'
@allowed([
  'zh'
  'nl'
  'en'
  'fr'
])
param emailLang string = 'zh'

param scannerCronExpression string = '*/10 * * * *'
param publisherCronExpression string = '* * * * *'
param reconcilerCronExpression string = '17 3 * * *'
param cleanupCronExpression string = '17 2 * * *'
param deliveryDlqCleanupCronExpression string = '43 2 * * *'
param countries string = 'nl,fr'
param minBtu string = '7000'
param maxPriceEur string = '1500'
param recipientShardCount string = '32'
param recipientPageSize string = '250'
@description('Minimum seconds between ACS sends across all email-worker replicas.')
param emailMinSendIntervalSeconds string = '13'
@allowed([
  'local'
  'azure_table'
])
@description('Rate-limit coordination backend. Multiple replicas require azure_table.')
param emailRateLimitBackend string = 'azure_table'
@description('Email worker replica ceiling. Raise only after ACS sender quota is increased.')
@minValue(1)
@maxValue(100)
param emailMaxReplicas int = 1

resource containerEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: containerEnvironmentName
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrName
}

resource scannerIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: scannerIdentityName
}

resource retentionIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: retentionIdentityName
}

resource publisherIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: publisherIdentityName
}

resource fanoutIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: fanoutIdentityName
}

resource emailIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: emailIdentityName
}

resource deliveryReportIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: deliveryReportIdentityName
}

var storageUrl = 'https://${storageAccountName}.blob.${environment().suffixes.storage}'
var serviceBusNamespace = '${serviceBusNamespaceName}.servicebus.windows.net'
// Fail closed: a process-local limiter can never coordinate more than one
// replica, even if a caller accidentally supplies a larger replica ceiling.
var safeEmailMaxReplicas = emailRateLimitBackend == 'azure_table' ? emailMaxReplicas : 1
var pipelineNamesEnv = [
  { name: 'APP_ENV', value: 'azure' }
  { name: 'ALERT_DISPATCH_BACKEND', value: 'service_bus' }
  { name: 'SERVICE_BUS_NAMESPACE', value: serviceBusNamespace }
  { name: 'STOCK_EVENTS_TOPIC', value: 'stock-events' }
  { name: 'STOCK_EVENTS_SUBSCRIPTION', value: 'email-fanout' }
  { name: 'FANOUT_JOBS_QUEUE', value: 'email-fanout-jobs' }
  { name: 'EMAIL_JOBS_QUEUE', value: 'email-jobs' }
  { name: 'ACS_DELIVERY_EVENTS_QUEUE', value: 'acs-email-delivery-events' }
  { name: 'AZURE_STORAGE_ACCOUNT_URL', value: storageUrl }
  { name: 'AUTH_USERS_TABLE', value: 'users' }
  { name: 'ALERT_OUTBOX_TABLE', value: 'alertoutbox' }
  { name: 'ALERT_OUTBOX_PENDING_TABLE', value: 'alertoutboxpending' }
  { name: 'ALERT_RECIPIENTS_TABLE', value: 'alertrecipients' }
  { name: 'ALERT_DELIVERIES_TABLE', value: 'alertdeliveries' }
  { name: 'ALERT_DELIVERY_INDEX_TABLE', value: 'alertdeliveryindex' }
  { name: 'ALERT_SUPPRESSIONS_TABLE', value: 'alertsuppression' }
  { name: 'ALERT_RECIPIENT_SHARDS', value: recipientShardCount }
  { name: 'ALERT_RECIPIENT_PAGE_SIZE', value: recipientPageSize }
  { name: 'ALERT_EVENT_MAX_AGE_SECONDS', value: '21600' }
  { name: 'ALERT_OUTBOX_RETENTION_DAYS', value: '30' }
  { name: 'ALERT_DELIVERY_RETENTION_DAYS', value: '90' }
]

resource scannerJob 'Microsoft.App/jobs@2025-01-01' = {
  name: scannerJobName
  location: resourceGroup().location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${scannerIdentity.id}': {}
    }
  }
  properties: {
    environmentId: containerEnvironment.id
    configuration: {
      registries: [
        { identity: scannerIdentity.id, server: registry.properties.loginServer }
      ]
      // A killed scanner retains its distributed lease until expiry. Retrying
      // immediately would only observe that lease and could falsely turn a
      // failed execution into a successful no-op.
      replicaRetryLimit: 0
      replicaTimeout: 300
      scheduleTriggerConfig: {
        cronExpression: scannerCronExpression
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
          command: [ 'airco-tracker' ]
          args: [ 'check' ]
          env: concat(pipelineNamesEnv, [
            { name: 'AZURE_CLIENT_ID', value: scannerIdentity.properties.clientId }
            { name: 'STATE_BACKEND', value: 'azure_blob' }
            { name: 'COUNTRIES', value: countries }
            { name: 'AZURE_STORAGE_CONTAINER', value: 'airco-tracker' }
            { name: 'AZURE_STORAGE_BLOB', value: 'state.json' }
            { name: 'AZURE_INVENTORY_BLOB', value: 'inventory.json' }
            { name: 'MIN_BTU', value: minBtu }
            { name: 'MAX_PRICE_EUR', value: maxPriceEur }
            { name: 'ALERT_ON_FIRST_SEEN', value: 'true' }
            { name: 'REQUEST_TIMEOUT_SECONDS', value: '25' }
            { name: 'SCANNER_LEASE_SECONDS', value: '480' }
            { name: 'STATE_COMPACT_AFTER_DAYS', value: '90' }
            { name: 'STATE_TOMBSTONE_RETENTION_DAYS', value: '365' }
            { name: 'AZURE_KEY_VAULT_URL', value: keyVaultUrl }
            { name: 'KEY_VAULT_SECRET_MAP', value: 'AWIN_PUBLISHER_API_TOKEN=awin-publisher-api-token,ALIEXPRESS_APP_KEY=aliexpress-app-key,ALIEXPRESS_APP_SECRET=aliexpress-app-secret' }
          ])
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
        }
      ]
    }
  }
}

resource publisherJob 'Microsoft.App/jobs@2025-01-01' = {
  name: publisherJobName
  location: resourceGroup().location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${publisherIdentity.id}': {}
    }
  }
  properties: {
    environmentId: containerEnvironment.id
    configuration: {
      registries: [
        { identity: publisherIdentity.id, server: registry.properties.loginServer }
      ]
      replicaRetryLimit: 3
      replicaTimeout: 180
      scheduleTriggerConfig: {
        cronExpression: publisherCronExpression
        parallelism: 1
        replicaCompletionCount: 1
      }
      triggerType: 'Schedule'
    }
    template: {
      containers: [
        {
          name: 'outbox-publisher'
          image: containerImage
          command: [ 'airco-tracker' ]
          args: [ 'publish-outbox', '--limit', '100' ]
          env: concat(pipelineNamesEnv, [
            { name: 'AZURE_CLIENT_ID', value: publisherIdentity.properties.clientId }
          ])
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
        }
      ]
    }
  }
}

resource reconcilerJob 'Microsoft.App/jobs@2025-01-01' = {
  name: reconcilerJobName
  location: resourceGroup().location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${fanoutIdentity.id}': {}
    }
  }
  properties: {
    environmentId: containerEnvironment.id
    configuration: {
      registries: [
        { identity: fanoutIdentity.id, server: registry.properties.loginServer }
      ]
      replicaRetryLimit: 2
      replicaTimeout: 300
      scheduleTriggerConfig: {
        cronExpression: reconcilerCronExpression
        parallelism: 1
        replicaCompletionCount: 1
      }
      triggerType: 'Schedule'
    }
    template: {
      containers: [
        {
          name: 'recipient-reconciler'
          image: containerImage
          command: [ 'airco-tracker' ]
          args: [ 'reconcile-alert-recipients' ]
          env: concat(pipelineNamesEnv, [
            { name: 'AZURE_CLIENT_ID', value: fanoutIdentity.properties.clientId }
          ])
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
        }
      ]
    }
  }
}

resource cleanupJob 'Microsoft.App/jobs@2025-01-01' = {
  name: cleanupJobName
  location: resourceGroup().location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${retentionIdentity.id}': {}
    }
  }
  properties: {
    environmentId: containerEnvironment.id
    configuration: {
      registries: [
        { identity: retentionIdentity.id, server: registry.properties.loginServer }
      ]
      replicaRetryLimit: 2
      replicaTimeout: 300
      scheduleTriggerConfig: {
        cronExpression: cleanupCronExpression
        parallelism: 1
        replicaCompletionCount: 1
      }
      triggerType: 'Schedule'
    }
    template: {
      containers: [
        {
          name: 'alert-retention'
          image: containerImage
          command: [ 'airco-tracker' ]
          args: [ 'cleanup-alert-data', '--max-runtime-seconds', '240' ]
          env: concat(pipelineNamesEnv, [
            { name: 'AZURE_CLIENT_ID', value: retentionIdentity.properties.clientId }
          ])
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
        }
      ]
    }
  }
}

resource deliveryDlqCleanupJob 'Microsoft.App/jobs@2025-01-01' = {
  name: deliveryDlqCleanupJobName
  location: resourceGroup().location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${deliveryReportIdentity.id}': {}
    }
  }
  properties: {
    environmentId: containerEnvironment.id
    configuration: {
      registries: [
        { identity: deliveryReportIdentity.id, server: registry.properties.loginServer }
      ]
      replicaRetryLimit: 2
      replicaTimeout: 300
      scheduleTriggerConfig: {
        cronExpression: deliveryDlqCleanupCronExpression
        parallelism: 1
        replicaCompletionCount: 1
      }
      triggerType: 'Schedule'
    }
    template: {
      containers: [
        {
          name: 'delivery-report-dlq-cleanup'
          image: containerImage
          command: [ 'airco-tracker' ]
          args: [ 'purge-delivery-report-dlq', '--limit', '5000' ]
          env: concat(pipelineNamesEnv, [
            { name: 'AZURE_CLIENT_ID', value: deliveryReportIdentity.properties.clientId }
          ])
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
        }
      ]
    }
  }
}

resource coordinatorApp 'Microsoft.App/containerApps@2025-01-01' = {
  name: coordinatorAppName
  location: resourceGroup().location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${fanoutIdentity.id}': {}
    }
  }
  properties: {
    environmentId: containerEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [
        { identity: fanoutIdentity.id, server: registry.properties.loginServer }
      ]
    }
    template: {
      containers: [
        {
          name: 'fanout-coordinator'
          image: containerImage
          command: [ 'airco-tracker' ]
          args: [ 'fanout-coordinator' ]
          env: concat(pipelineNamesEnv, [
            { name: 'AZURE_CLIENT_ID', value: fanoutIdentity.properties.clientId }
          ])
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 4
        pollingInterval: 15
        cooldownPeriod: 300
        rules: [
          {
            name: 'stock-events'
            custom: {
              type: 'azure-servicebus'
              metadata: {
                namespace: serviceBusNamespaceName
                topicName: 'stock-events'
                subscriptionName: 'email-fanout'
                messageCount: '1'
              }
              identity: fanoutIdentity.id
            }
          }
        ]
      }
    }
  }
}

resource fanoutApp 'Microsoft.App/containerApps@2025-01-01' = {
  name: fanoutAppName
  location: resourceGroup().location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${fanoutIdentity.id}': {}
    }
  }
  properties: {
    environmentId: containerEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [
        { identity: fanoutIdentity.id, server: registry.properties.loginServer }
      ]
    }
    template: {
      containers: [
        {
          name: 'fanout-worker'
          image: containerImage
          command: [ 'airco-tracker' ]
          args: [ 'fanout-worker' ]
          env: concat(pipelineNamesEnv, [
            { name: 'AZURE_CLIENT_ID', value: fanoutIdentity.properties.clientId }
          ])
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 16
        pollingInterval: 15
        cooldownPeriod: 300
        rules: [
          {
            name: 'fanout-jobs'
            custom: {
              type: 'azure-servicebus'
              metadata: {
                namespace: serviceBusNamespaceName
                queueName: 'email-fanout-jobs'
                messageCount: '1'
              }
              identity: fanoutIdentity.id
            }
          }
        ]
      }
    }
  }
}

resource emailApp 'Microsoft.App/containerApps@2025-01-01' = {
  name: emailAppName
  location: resourceGroup().location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${emailIdentity.id}': {}
    }
  }
  properties: {
    environmentId: containerEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [
        { identity: emailIdentity.id, server: registry.properties.loginServer }
      ]
    }
    template: {
      containers: [
        {
          name: 'email-worker'
          image: containerImage
          command: [ 'airco-tracker' ]
          args: [ 'email-worker' ]
          env: concat(pipelineNamesEnv, [
            { name: 'AZURE_CLIENT_ID', value: emailIdentity.properties.clientId }
            { name: 'EMAIL_BACKEND', value: 'azure_communication' }
            { name: 'EMAIL_FROM', value: emailFrom }
            { name: 'EMAIL_REPLY_TO', value: emailReplyTo }
            { name: 'EMAIL_LANG', value: emailLang }
            { name: 'APP_BASE_URL', value: appBaseUrl }
            { name: 'AZURE_KEY_VAULT_URL', value: keyVaultUrl }
            { name: 'KEY_VAULT_SECRET_MAP', value: 'EMAIL_UNSUBSCRIBE_SIGNING_KEY=unsubscribe-signing-key' }
            { name: 'ACS_ENDPOINT', value: 'https://${communicationServiceName}.communication.azure.com' }
            { name: 'EMAIL_MIN_SEND_INTERVAL_SECONDS', value: emailMinSendIntervalSeconds }
            { name: 'EMAIL_RATE_LIMIT_BACKEND', value: emailRateLimitBackend }
            { name: 'EMAIL_RATE_LIMIT_TABLE', value: 'emailratelimit' }
          ])
          resources: { cpu: json('0.5'), memory: '1Gi' }
        }
      ]
      scale: {
        minReplicas: 0
        // The distributed Table limiter preserves the configured aggregate
        // send interval. Raise this only after ACS sender quota is increased.
        maxReplicas: safeEmailMaxReplicas
        pollingInterval: 15
        cooldownPeriod: 300
        rules: [
          {
            name: 'email-jobs'
            custom: {
              type: 'azure-servicebus'
              metadata: {
                namespace: serviceBusNamespaceName
                queueName: 'email-jobs'
                messageCount: '10'
              }
              identity: emailIdentity.id
            }
          }
        ]
      }
    }
  }
}

resource deliveryReportApp 'Microsoft.App/containerApps@2025-01-01' = {
  name: deliveryReportAppName
  location: resourceGroup().location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${deliveryReportIdentity.id}': {}
    }
  }
  properties: {
    environmentId: containerEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [
        { identity: deliveryReportIdentity.id, server: registry.properties.loginServer }
      ]
    }
    template: {
      containers: [
        {
          name: 'delivery-report-worker'
          image: containerImage
          command: [ 'airco-tracker' ]
          args: [ 'delivery-report-worker' ]
          env: concat(pipelineNamesEnv, [
            { name: 'AZURE_CLIENT_ID', value: deliveryReportIdentity.properties.clientId }
          ])
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 4
        pollingInterval: 15
        cooldownPeriod: 300
        rules: [
          {
            name: 'delivery-events'
            custom: {
              type: 'azure-servicebus'
              metadata: {
                namespace: serviceBusNamespaceName
                queueName: 'acs-email-delivery-events'
                messageCount: '10'
              }
              identity: deliveryReportIdentity.id
            }
          }
        ]
      }
    }
  }
}

output scannerJobName string = scannerJob.name
output publisherJobName string = publisherJob.name
output reconcilerJobName string = reconcilerJob.name
output cleanupJobName string = cleanupJob.name
output deliveryDlqCleanupJobName string = deliveryDlqCleanupJob.name
output coordinatorAppName string = coordinatorApp.name
output fanoutAppName string = fanoutApp.name
output emailAppName string = emailApp.name
output deliveryReportAppName string = deliveryReportApp.name
