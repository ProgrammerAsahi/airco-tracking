@description('Short lowercase prefix used in resource names.')
@minLength(3)
@maxLength(12)
param prefix string = 'aircontrack'

@description('Azure region for compute and storage.')
param location string = 'westeurope'

@description('Data residency used by Azure Communication Services.')
param communicationDataLocation string = 'Europe'

@description('Stable suffix for globally unique resources. Preserve the deployed value when upgrading an existing environment.')
@minLength(8)
@maxLength(8)
param resourceToken string = take(uniqueString(subscription().id, resourceGroup().id, prefix), 8)

@description('Create unchanged web-identity RBAC assignments. Set false when upgrading an environment whose equivalent assignments have legacy random IDs.')
param manageSharedIdentityRbac bool = true

@description('Create least-privilege Key Vault assignments at individual secret scopes. Enable only after all referenced secrets exist.')
param manageSecretScopedKeyVaultRbac bool = false

@description('Optional already-verified customer-managed Email Communication domain ID to preserve alongside the Azure-managed fallback.')
param customEmailDomainId string = ''

@secure()
@description('Optional operations mailbox for Azure Monitor Service Bus alerts. Leave empty to create dashboard-visible alerts without email actions.')
param operationsAlertEmail string = ''

@description('Create Standard Service Bus entities with 16 partitions. This creation-time setting requires entity recreation to change.')
param enableServiceBusPartitioning bool = true

@description('Monthly cost budget in EUR for the resource group. Actual spend crossing 80% or 100% notifies the operations action group.')
@minValue(1)
param monthlyBudgetAmountEur int = 50

@description('First day of the month from which the cost budget measures spend. Defaults to the deployment month; earlier costs are not counted.')
param budgetStartDate string = '${utcNow('yyyy-MM')}-01'

var token = resourceToken
var acrName = 'aircotracker${token}'
var storageName = 'aircostate${token}'
// Preserve the deployed web identity name for a zero-downtime migration, but
// do not share it with scanner or retention workloads.
var webIdentityName = '${prefix}-identity'
var scannerIdentityName = '${prefix}-scanner'
var retentionIdentityName = '${prefix}-retention'
var webRetentionIdentityName = '${prefix}-web-retention'
var environmentName = '${prefix}-env'
var logName = '${prefix}-logs'
var keyVaultName = 'aircokv${token}'
var emailServiceName = '${prefix}-email-${token}'
var communicationServiceName = '${prefix}-acs-${token}'
var serviceBusNamespaceName = 'aircosb${token}'
var publisherIdentityName = '${prefix}-alert-publisher'
var fanoutIdentityName = '${prefix}-alert-fanout'
var emailIdentityName = '${prefix}-alert-email'
var deliveryReportIdentityName = '${prefix}-alert-delivery-report'
var deliveryEventsSystemTopicName = '${prefix}-acs-email-events'

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: webIdentityName
  location: location
}

resource scannerIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: scannerIdentityName
  location: location
}

resource retentionIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: retentionIdentityName
  location: location
}

resource webRetentionIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: webRetentionIdentityName
  location: location
}

resource publisherIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: publisherIdentityName
  location: location
}

resource fanoutIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: fanoutIdentityName
  location: location
}

resource emailIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: emailIdentityName
  location: location
}

resource deliveryReportIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: deliveryReportIdentityName
  location: location
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    allowSharedKeyAccess: false
    allowBlobPublicAccess: false
    defaultToOAuthAuthentication: true
    minimumTlsVersion: 'TLS1_2'
    publicNetworkAccess: 'Enabled'
    supportsHttpsTrafficOnly: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource stateContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'airco-tracker'
  properties: {
    publicAccess: 'None'
  }
}

resource emailEventDeadLetterContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'acs-email-event-deadletters'
  properties: {
    publicAccess: 'None'
  }
}

// Event Grid dead letters contain the provider's original recipient field.
// Bound that exceptional PII retention even if incident handling is delayed.
resource storageLifecyclePolicy 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    policy: {
      rules: [
        {
          name: 'delete-acs-email-event-deadletters-after-7-days'
          enabled: true
          type: 'Lifecycle'
          definition: {
            actions: {
              baseBlob: {
                delete: {
                  daysAfterModificationGreaterThan: 7
                }
              }
            }
            filters: {
              blobTypes: [ 'blockBlob' ]
              prefixMatch: [ '${emailEventDeadLetterContainer.name}/' ]
            }
          }
        }
      ]
    }
  }
}

resource tableService 'Microsoft.Storage/storageAccounts/tableServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource alertOutboxTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: 'alertoutbox'
}

resource alertOutboxPendingTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: 'alertoutboxpending'
}

resource alertRecipientsTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: 'alertrecipients'
}

resource alertDeliveriesTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: 'alertdeliveries'
}

resource alertDeliveryIndexTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: 'alertdeliveryindex'
}

resource alertSuppressionsTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: 'alertsuppression'
}

resource emailRateLimitTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: 'emailratelimit'
}

resource authUsersTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: 'users'
}

resource authCodesTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: 'authcodes'
}

resource authSessionsTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: 'authsessions'
}

resource i18nTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: 'i18n'
}

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logName
  location: location
  properties: {
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
    retentionInDays: 30
    sku: {
      name: 'PerGB2018'
    }
  }
}

resource containerEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    enableRbacAuthorization: true
    enableSoftDelete: true
    publicNetworkAccess: 'Enabled'
    sku: {
      family: 'A'
      name: 'standard'
    }
    // Soft-delete retention is fixed at vault creation time and Azure
    // rejects later changes ("has been set already and it can't be
    // modified"), so this value must stay at the creation-time 7 days.
    // Purge protection below already blocks permanent deletion inside it.
    softDeleteRetentionInDays: 7
    enablePurgeProtection: true
    tenantId: tenant().tenantId
  }
}

// Secret values are deliberately provisioned out of band. Declaring the
// metadata as existing lets RBAC bind each workload only to the names it
// consumes instead of granting read access to the whole vault.
resource unsubscribeSigningKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: 'unsubscribe-signing-key'
}

resource withdrawalSigningKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: 'withdrawal-signing-key'
}

resource authCodeHmacPepperSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: 'auth-code-hmac-pepper'
}

resource awinPublisherApiTokenSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: 'awin-publisher-api-token'
}

resource aliexpressAppKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: 'aliexpress-app-key'
}

resource aliexpressAppSecretSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: 'aliexpress-app-secret'
}

resource emailService 'Microsoft.Communication/emailServices@2025-09-01' = {
  name: emailServiceName
  location: 'global'
  properties: {
    dataLocation: communicationDataLocation
  }
}

resource emailDomain 'Microsoft.Communication/emailServices/domains@2025-09-01' = {
  parent: emailService
  name: 'AzureManagedDomain'
  location: 'global'
  properties: {
    domainManagement: 'AzureManaged'
    userEngagementTracking: 'Disabled'
  }
}

resource communicationService 'Microsoft.Communication/communicationServices@2025-09-01' = {
  name: communicationServiceName
  location: 'global'
  properties: {
    dataLocation: communicationDataLocation
    disableLocalAuth: true
    linkedDomains: empty(customEmailDomainId)
      ? [ emailDomain.id ]
      : [ emailDomain.id, customEmailDomainId ]
    publicNetworkAccess: 'Enabled'
  }
}

resource deliveryEventsSystemTopic 'Microsoft.EventGrid/systemTopics@2022-06-15' = {
  name: deliveryEventsSystemTopicName
  location: 'global'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    source: communicationService.id
    topicType: 'Microsoft.Communication.CommunicationServices'
  }
}

resource serviceBus 'Microsoft.ServiceBus/namespaces@2024-01-01' = {
  name: serviceBusNamespaceName
  location: location
  sku: {
    name: 'Standard'
    tier: 'Standard'
    capacity: 0
  }
  properties: {
    disableLocalAuth: true
    minimumTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
    zoneRedundant: false
  }
}

resource stockEventsTopic 'Microsoft.ServiceBus/namespaces/topics@2024-01-01' = {
  parent: serviceBus
  name: 'stock-events'
  properties: {
    defaultMessageTimeToLive: 'P1D'
    duplicateDetectionHistoryTimeWindow: 'P7D'
    enableBatchedOperations: true
    enableExpress: false
    // Standard partitioning creates 16 broker/storage partitions. The alert
    // domain does not require global ordering, so this avoids a single-broker
    // throughput ceiling and keeps enqueueing available during broker work.
    enablePartitioning: enableServiceBusPartitioning
    maxSizeInMegabytes: 1024
    requiresDuplicateDetection: true
    status: 'Active'
    supportOrdering: false
  }
}

resource emailFanoutSubscription 'Microsoft.ServiceBus/namespaces/topics/subscriptions@2024-01-01' = {
  parent: stockEventsTopic
  name: 'email-fanout'
  properties: {
    deadLetteringOnFilterEvaluationExceptions: true
    deadLetteringOnMessageExpiration: true
    defaultMessageTimeToLive: 'P1D'
    enableBatchedOperations: true
    lockDuration: 'PT5M'
    maxDeliveryCount: 8
    status: 'Active'
  }
}

resource fanoutJobsQueue 'Microsoft.ServiceBus/namespaces/queues@2024-01-01' = {
  parent: serviceBus
  name: 'email-fanout-jobs'
  properties: {
    deadLetteringOnMessageExpiration: true
    defaultMessageTimeToLive: 'P1D'
    duplicateDetectionHistoryTimeWindow: 'P7D'
    enableBatchedOperations: true
    enableExpress: false
    enablePartitioning: enableServiceBusPartitioning
    lockDuration: 'PT5M'
    maxDeliveryCount: 8
    maxSizeInMegabytes: 1024
    requiresDuplicateDetection: true
    requiresSession: false
    status: 'Active'
  }
}

resource emailJobsQueue 'Microsoft.ServiceBus/namespaces/queues@2024-01-01' = {
  parent: serviceBus
  name: 'email-jobs'
  properties: {
    deadLetteringOnMessageExpiration: true
    defaultMessageTimeToLive: 'PT6H'
    duplicateDetectionHistoryTimeWindow: 'P7D'
    enableBatchedOperations: true
    enableExpress: false
    enablePartitioning: enableServiceBusPartitioning
    lockDuration: 'PT5M'
    maxDeliveryCount: 8
    maxSizeInMegabytes: 1024
    requiresDuplicateDetection: true
    requiresSession: false
    status: 'Active'
  }
}

resource deliveryEventsQueue 'Microsoft.ServiceBus/namespaces/queues@2024-01-01' = {
  parent: serviceBus
  name: 'acs-email-delivery-events'
  properties: {
    // The provider event body necessarily contains the recipient address.
    // Keep it transient, and do not copy expired events into the DLQ where
    // Service Bus does not enforce TTL.
    deadLetteringOnMessageExpiration: false
    defaultMessageTimeToLive: 'P1D'
    duplicateDetectionHistoryTimeWindow: 'P1D'
    enableBatchedOperations: true
    enableExpress: false
    enablePartitioning: enableServiceBusPartitioning
    lockDuration: 'PT5M'
    maxDeliveryCount: 16
    maxSizeInMegabytes: 1024
    requiresDuplicateDetection: true
    requiresSession: false
    status: 'Active'
  }
}

resource serviceBusDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'airco-servicebus-diagnostics'
  scope: serviceBus
  properties: {
    workspaceId: logs.id
    logs: [
      { category: 'OperationalLogs', enabled: true }
      { category: 'DiagnosticErrorLogs', enabled: true }
    ]
    metrics: [
      { category: 'AllMetrics', enabled: true }
    ]
  }
}

// Send-operation logs provide quota/request diagnostics without copying the
// recipient-level EmailStatusUpdateOperational address into Log Analytics.
// Final recipient statuses are consumed through Event Grid and logged only
// with opaque delivery IDs by the application.
resource communicationEmailDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'airco-email-send-diagnostics'
  scope: communicationService
  properties: {
    workspaceId: logs.id
    logs: [
      { category: 'EmailSendMailOperational', enabled: true }
    ]
  }
}

// Key Vault AuditEvent records every secret read so access to the stored
// third-party credentials stays attributable. AllMetrics follows the Service
// Bus diagnostic pattern above.
resource keyVaultDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'airco-keyvault-diagnostics'
  scope: vault
  properties: {
    workspaceId: logs.id
    logs: [
      { category: 'AuditEvent', enabled: true }
    ]
    metrics: [
      { category: 'AllMetrics', enabled: true }
    ]
  }
}

resource operationsActionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = if (!empty(operationsAlertEmail)) {
  name: '${prefix}-operations-alerts'
  location: 'global'
  properties: {
    groupShortName: 'airco-ops'
    enabled: true
    emailReceivers: [
      {
        name: 'primary-operations-mailbox'
        emailAddress: operationsAlertEmail
        useCommonAlertSchema: true
      }
    ]
  }
}

var serviceBusAlertActions = empty(operationsAlertEmail)
  ? []
  : [
      {
        actionGroupId: operationsActionGroup.id
      }
    ]

var scheduledQueryActionGroupIds = empty(operationsAlertEmail)
  ? []
  : [ operationsActionGroup.id ]

// A monthly cost budget bounds the blast radius of a runaway job or an
// unexpected price change. It exists to notify the operations mailbox, so
// without a configured receiver there is no one to alert and it is skipped
// like the action group itself.
resource monthlyCostBudget 'Microsoft.Consumption/budgets@2023-11-01' = if (!empty(operationsAlertEmail)) {
  name: '${prefix}-monthly-budget'
  properties: {
    category: 'Cost'
    amount: monthlyBudgetAmountEur
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: budgetStartDate
    }
    notifications: {
      actual_GreaterThan_80_Percent: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 80
        thresholdType: 'Actual'
        contactEmails: []
        contactGroups: [
          operationsActionGroup.id
        ]
      }
      actual_GreaterThan_100_Percent: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 100
        thresholdType: 'Actual'
        contactEmails: []
        contactGroups: [
          operationsActionGroup.id
        ]
      }
    }
  }
}

resource serviceBusDeadLetterAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${prefix}-servicebus-deadletter'
  location: 'global'
  properties: {
    description: 'At least one Service Bus message is in a dead-letter queue.'
    severity: 1
    enabled: true
    scopes: [ serviceBus.id ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    autoMitigate: true
    targetResourceType: 'Microsoft.ServiceBus/namespaces'
    targetResourceRegion: location
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'DeadletteredMessagesAboveZero'
          criterionType: 'StaticThresholdCriterion'
          metricNamespace: 'Microsoft.ServiceBus/namespaces'
          metricName: 'DeadletteredMessages'
          timeAggregation: 'Average'
          operator: 'GreaterThan'
          threshold: 0
          skipMetricValidation: false
        }
      ]
    }
    actions: serviceBusAlertActions
  }
}

resource serviceBusBacklogAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${prefix}-servicebus-backlog'
  location: 'global'
  properties: {
    description: 'Service Bus active-message backlog has remained above 1000.'
    severity: 2
    enabled: true
    scopes: [ serviceBus.id ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT15M'
    autoMitigate: true
    targetResourceType: 'Microsoft.ServiceBus/namespaces'
    targetResourceRegion: location
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'ActiveMessagesAboveOneThousand'
          criterionType: 'StaticThresholdCriterion'
          metricNamespace: 'Microsoft.ServiceBus/namespaces'
          metricName: 'ActiveMessages'
          timeAggregation: 'Average'
          operator: 'GreaterThan'
          threshold: 1000
          skipMetricValidation: false
        }
      ]
    }
    actions: serviceBusAlertActions
  }
}

resource serviceBusThrottleAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${prefix}-servicebus-throttled'
  location: 'global'
  properties: {
    description: 'Service Bus has throttled at least one request.'
    severity: 2
    enabled: true
    scopes: [ serviceBus.id ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    autoMitigate: true
    targetResourceType: 'Microsoft.ServiceBus/namespaces'
    targetResourceRegion: location
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'ThrottledRequestsAboveZero'
          criterionType: 'StaticThresholdCriterion'
          metricNamespace: 'Microsoft.ServiceBus/namespaces'
          metricName: 'ThrottledRequests'
          timeAggregation: 'Total'
          operator: 'GreaterThan'
          threshold: 0
          skipMetricValidation: false
        }
      ]
    }
    actions: serviceBusAlertActions
  }
}

resource serviceBusServerErrorAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${prefix}-servicebus-server-errors'
  location: 'global'
  properties: {
    description: 'Service Bus has returned at least one server error.'
    severity: 1
    enabled: true
    scopes: [ serviceBus.id ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    autoMitigate: true
    targetResourceType: 'Microsoft.ServiceBus/namespaces'
    targetResourceRegion: location
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'ServerErrorsAboveZero'
          criterionType: 'StaticThresholdCriterion'
          metricNamespace: 'Microsoft.ServiceBus/namespaces'
          metricName: 'ServerErrors'
          timeAggregation: 'Total'
          operator: 'GreaterThan'
          threshold: 0
          skipMetricValidation: false
        }
      ]
    }
    actions: serviceBusAlertActions
  }
}

resource eventGridDeadLetterAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${prefix}-email-events-deadletter'
  location: 'global'
  properties: {
    description: 'At least one ACS email delivery report was dead-lettered by Event Grid.'
    severity: 1
    enabled: true
    scopes: [ deliveryEventsSystemTopic.id ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    autoMitigate: true
    targetResourceType: 'Microsoft.EventGrid/systemTopics'
    targetResourceRegion: 'global'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'EmailDeliveryReportDeadLettered'
          criterionType: 'StaticThresholdCriterion'
          metricNamespace: 'Microsoft.EventGrid/systemTopics'
          metricName: 'DeadLetteredCount'
          timeAggregation: 'Total'
          operator: 'GreaterThan'
          threshold: 0
          skipMetricValidation: false
        }
      ]
    }
    actions: serviceBusAlertActions
  }
}

resource eventGridDroppedAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${prefix}-email-events-dropped'
  location: 'global'
  properties: {
    description: 'At least one ACS email delivery report was dropped by Event Grid.'
    severity: 1
    enabled: true
    scopes: [ deliveryEventsSystemTopic.id ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    autoMitigate: true
    targetResourceType: 'Microsoft.EventGrid/systemTopics'
    targetResourceRegion: 'global'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'EmailDeliveryReportDropped'
          criterionType: 'StaticThresholdCriterion'
          metricNamespace: 'Microsoft.EventGrid/systemTopics'
          metricName: 'DroppedEventCount'
          timeAggregation: 'Total'
          operator: 'GreaterThan'
          threshold: 0
          skipMetricValidation: false
        }
      ]
    }
    actions: serviceBusAlertActions
  }
}

resource eventGridDeliveryFailureAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${prefix}-email-events-delivery-failures'
  location: 'global'
  properties: {
    description: 'Event Grid repeatedly failed to hand ACS email reports to Service Bus.'
    severity: 2
    enabled: true
    scopes: [ deliveryEventsSystemTopic.id ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    autoMitigate: true
    targetResourceType: 'Microsoft.EventGrid/systemTopics'
    targetResourceRegion: 'global'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'EmailDeliveryReportDeliveryFailures'
          criterionType: 'StaticThresholdCriterion'
          metricNamespace: 'Microsoft.EventGrid/systemTopics'
          metricName: 'DeliveryAttemptFailCount'
          timeAggregation: 'Total'
          operator: 'GreaterThan'
          threshold: 5
          skipMetricValidation: false
        }
      ]
    }
    actions: serviceBusAlertActions
  }
}

// These application logs contain only opaque delivery IDs and normalized
// provider statuses. They turn the final-report audit into an actionable
// signal without enabling ACS's recipient-bearing status diagnostic stream.
resource overdueFinalReportAlert 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = {
  name: '${prefix}-email-final-report-overdue'
  location: location
  properties: {
    displayName: 'ACS accepted emails missing final reports'
    description: 'The daily privacy/retention audit found accepted mail older than two hours without a final ACS delivery report.'
    severity: 2
    enabled: true
    evaluationFrequency: 'PT15M'
    windowSize: 'PT2H'
    scopes: [ logs.id ]
    targetResourceTypes: [ 'Microsoft.OperationalInsights/workspaces' ]
    criteria: {
      allOf: [
        {
          query: '''
            ContainerAppConsoleLogs_CL
            | where Log_s startswith "ACS final delivery reports overdue:"
          '''
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: scheduledQueryActionGroupIds
    }
    autoMitigate: true
    skipQueryValidation: true
  }
}

resource adverseEmailOutcomeAlert 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = {
  name: '${prefix}-email-adverse-outcomes'
  location: location
  properties: {
    displayName: 'ACS adverse final email outcomes'
    description: 'At least one stock alert was bounced, provider-suppressed, quarantined, filtered as spam, or failed in the last hour.'
    severity: 2
    enabled: true
    evaluationFrequency: 'PT15M'
    windowSize: 'PT1H'
    scopes: [ logs.id ]
    targetResourceTypes: [ 'Microsoft.OperationalInsights/workspaces' ]
    criteria: {
      allOf: [
        {
          query: '''
            ContainerAppConsoleLogs_CL
            | where ContainerAppName_s == "airco-alert-delivery-worker"
            | where Log_s has "Recorded ACS final delivery report"
            | where Log_s has_any (
                "status=bounced",
                "status=provider_suppressed",
                "status=quarantined",
                "status=filtered_spam",
                "status=provider_failed"
              )
          '''
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: scheduledQueryActionGroupIds
    }
    autoMitigate: true
    skipQueryValidation: true
  }
}

var acrPullRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
var blobContributorRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
var blobReaderRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1')
var keyVaultSecretsUserRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
var tableDataContributorRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3')
var tableDataReaderRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '76199698-9eea-4c19-bc75-cec21354c6b6')
var serviceBusSenderRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39')
var serviceBusReceiverRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0')

// Sending email through ACS with a Microsoft Entra ID / Managed Identity
// credential requires only three control-plane actions on the Communication
// Service resource, not the broad built-in Communication and Email Service
// Owner role. Microsoft documents this minimal set for Entra-authenticated
// email credentials:
// https://learn.microsoft.com/azure/communication-services/quickstarts/email/send-email-smtp/smtp-authentication
// The emails:send and operation-status calls authorize against the
// Communication Service (not the Email Service), so the assignments below
// keep that same scope. Earlier revisions assigned the built-in Owner role;
// incremental deployments never delete role assignments, so those legacy
// assignments must be removed manually after this role is verified.
resource acsEmailSenderRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, '${prefix}-acs-email-sender')
  properties: {
    roleName: '${prefix}-acs-email-sender'
    description: 'Least-privilege email send and send-status read through Azure Communication Services with Microsoft Entra ID.'
    type: 'CustomRole'
    assignableScopes: [
      resourceGroup().id
    ]
    permissions: [
      {
        actions: [
          'Microsoft.Communication/CommunicationServices/Read'
          'Microsoft.Communication/CommunicationServices/Write'
          'Microsoft.Communication/EmailServices/write'
        ]
        notActions: []
        dataActions: []
        notDataActions: []
      }
    ]
  }
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (manageSharedIdentityRbac) {
  name: guid(registry.id, identity.id, acrPullRole)
  scope: registry
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRole
  }
}

// The web app serves the inventory snapshot and does not mutate scanner state.
resource webBlobReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(stateContainer.id, identity.id, blobReaderRole)
  scope: stateContainer
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: blobReaderRole
  }
}

// Web write access is limited to authentication/profile state and the alert
// recipient projection maintained transactionally with profile changes.
resource sharedUsersTableContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(authUsersTable.id, identity.id, tableDataContributorRole)
  scope: authUsersTable
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataContributorRole
  }
}

resource sharedAuthCodesTableContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(authCodesTable.id, identity.id, tableDataContributorRole)
  scope: authCodesTable
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataContributorRole
  }
}

resource sharedAuthSessionsTableContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(authSessionsTable.id, identity.id, tableDataContributorRole)
  scope: authSessionsTable
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataContributorRole
  }
}

resource sharedAlertRecipientsTableContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(alertRecipientsTable.id, identity.id, tableDataContributorRole)
  scope: alertRecipientsTable
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataContributorRole
  }
}

resource sharedI18nTableReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(i18nTable.id, identity.id, tableDataReaderRole)
  scope: i18nTable
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataReaderRole
  }
}

resource webUnsubscribeSecretReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (manageSecretScopedKeyVaultRbac) {
  name: guid(unsubscribeSigningKeySecret.id, identity.id, keyVaultSecretsUserRole)
  scope: unsubscribeSigningKeySecret
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRole
  }
}

resource webWithdrawalSecretReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (manageSecretScopedKeyVaultRbac) {
  name: guid(withdrawalSigningKeySecret.id, identity.id, keyVaultSecretsUserRole)
  scope: withdrawalSigningKeySecret
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRole
  }
}

resource webAuthCodePepperSecretReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (manageSecretScopedKeyVaultRbac) {
  name: guid(authCodeHmacPepperSecret.id, identity.id, keyVaultSecretsUserRole)
  scope: authCodeHmacPepperSecret
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRole
  }
}

resource communicationAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (manageSharedIdentityRbac) {
  name: guid(communicationService.id, identity.id, acsEmailSenderRole.id)
  scope: communicationService
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acsEmailSenderRole.id
  }
}

resource emailCommunicationAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(communicationService.id, emailIdentity.id, acsEmailSenderRole.id)
  scope: communicationService
  properties: {
    principalId: emailIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acsEmailSenderRole.id
  }
}

resource scannerAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, scannerIdentity.id, acrPullRole)
  scope: registry
  properties: {
    principalId: scannerIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRole
  }
}

resource retentionAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, retentionIdentity.id, acrPullRole)
  scope: registry
  properties: {
    principalId: retentionIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRole
  }
}

resource webRetentionAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, webRetentionIdentity.id, acrPullRole)
  scope: registry
  properties: {
    principalId: webRetentionIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRole
  }
}

resource scannerBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(stateContainer.id, scannerIdentity.id, blobContributorRole)
  scope: stateContainer
  properties: {
    principalId: scannerIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: blobContributorRole
  }
}

resource scannerAwinSecretReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (manageSecretScopedKeyVaultRbac) {
  name: guid(awinPublisherApiTokenSecret.id, scannerIdentity.id, keyVaultSecretsUserRole)
  scope: awinPublisherApiTokenSecret
  properties: {
    principalId: scannerIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRole
  }
}

resource scannerAliexpressAppKeyReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (manageSecretScopedKeyVaultRbac) {
  name: guid(aliexpressAppKeySecret.id, scannerIdentity.id, keyVaultSecretsUserRole)
  scope: aliexpressAppKeySecret
  properties: {
    principalId: scannerIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRole
  }
}

resource scannerAliexpressAppSecretReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (manageSecretScopedKeyVaultRbac) {
  name: guid(aliexpressAppSecretSecret.id, scannerIdentity.id, keyVaultSecretsUserRole)
  scope: aliexpressAppSecretSecret
  properties: {
    principalId: scannerIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRole
  }
}

resource scannerOutboxContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(alertOutboxTable.id, scannerIdentity.id, tableDataContributorRole)
  scope: alertOutboxTable
  properties: {
    principalId: scannerIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataContributorRole
  }
}

resource scannerPendingOutboxContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(alertOutboxPendingTable.id, scannerIdentity.id, tableDataContributorRole)
  scope: alertOutboxPendingTable
  properties: {
    principalId: scannerIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataContributorRole
  }
}

resource retentionOutboxContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(alertOutboxTable.id, retentionIdentity.id, tableDataContributorRole)
  scope: alertOutboxTable
  properties: {
    principalId: retentionIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataContributorRole
  }
}

resource retentionPendingOutboxContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(alertOutboxPendingTable.id, retentionIdentity.id, tableDataContributorRole)
  scope: alertOutboxPendingTable
  properties: {
    principalId: retentionIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataContributorRole
  }
}

resource retentionDeliveriesContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(alertDeliveriesTable.id, retentionIdentity.id, tableDataContributorRole)
  scope: alertDeliveriesTable
  properties: {
    principalId: retentionIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataContributorRole
  }
}

resource retentionDeliveryIndexContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(alertDeliveryIndexTable.id, retentionIdentity.id, tableDataContributorRole)
  scope: alertDeliveryIndexTable
  properties: {
    principalId: retentionIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataContributorRole
  }
}

resource retentionSuppressionsContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(alertSuppressionsTable.id, retentionIdentity.id, tableDataContributorRole)
  scope: alertSuppressionsTable
  properties: {
    principalId: retentionIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataContributorRole
  }
}

// The web repository's scheduled cleanup job has its own identity. It needs
// delete-capable access only to the three auth tables whose expired rows it
// drains; it inherits neither backend-retention data access nor web ACS/Key
// Vault privileges.
resource webRetentionUsersContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(authUsersTable.id, webRetentionIdentity.id, tableDataContributorRole)
  scope: authUsersTable
  properties: {
    principalId: webRetentionIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataContributorRole
  }
}

resource webRetentionAuthCodesContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(authCodesTable.id, webRetentionIdentity.id, tableDataContributorRole)
  scope: authCodesTable
  properties: {
    principalId: webRetentionIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataContributorRole
  }
}

resource webRetentionAuthSessionsContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(authSessionsTable.id, webRetentionIdentity.id, tableDataContributorRole)
  scope: authSessionsTable
  properties: {
    principalId: webRetentionIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataContributorRole
  }
}

resource retentionRecipientsReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(alertRecipientsTable.id, retentionIdentity.id, tableDataReaderRole)
  scope: alertRecipientsTable
  properties: {
    principalId: retentionIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataReaderRole
  }
}

resource publisherAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, publisherIdentity.id, acrPullRole)
  scope: registry
  properties: {
    principalId: publisherIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRole
  }
}

resource fanoutAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, fanoutIdentity.id, acrPullRole)
  scope: registry
  properties: {
    principalId: fanoutIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRole
  }
}

resource emailAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, emailIdentity.id, acrPullRole)
  scope: registry
  properties: {
    principalId: emailIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRole
  }
}

resource deliveryReportAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, deliveryReportIdentity.id, acrPullRole)
  scope: registry
  properties: {
    principalId: deliveryReportIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRole
  }
}

resource emailUnsubscribeSecretReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (manageSecretScopedKeyVaultRbac) {
  name: guid(unsubscribeSigningKeySecret.id, emailIdentity.id, keyVaultSecretsUserRole)
  scope: unsubscribeSigningKeySecret
  properties: {
    principalId: emailIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRole
  }
}

resource publisherTableAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(alertOutboxTable.id, publisherIdentity.id, tableDataContributorRole)
  scope: alertOutboxTable
  properties: {
    principalId: publisherIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataContributorRole
  }
}

resource publisherPendingTableAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(alertOutboxPendingTable.id, publisherIdentity.id, tableDataContributorRole)
  scope: alertOutboxPendingTable
  properties: {
    principalId: publisherIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataContributorRole
  }
}

// The operator-only pipeline-status command runs through this same managed
// identity and reads anonymized delivery states; it never reads recipients.
resource publisherDeliveryStatusReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(alertDeliveriesTable.id, publisherIdentity.id, tableDataReaderRole)
  scope: alertDeliveriesTable
  properties: {
    principalId: publisherIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataReaderRole
  }
}

resource fanoutTableAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(alertRecipientsTable.id, fanoutIdentity.id, tableDataContributorRole)
  scope: alertRecipientsTable
  properties: {
    principalId: fanoutIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataContributorRole
  }
}

resource emailTableAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(alertDeliveriesTable.id, emailIdentity.id, tableDataContributorRole)
  scope: alertDeliveriesTable
  properties: {
    principalId: emailIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataContributorRole
  }
}

resource emailRateLimitTableAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(emailRateLimitTable.id, emailIdentity.id, tableDataContributorRole)
  scope: emailRateLimitTable
  properties: {
    principalId: emailIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataContributorRole
  }
}

resource emailDeliveryIndexAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(alertDeliveryIndexTable.id, emailIdentity.id, tableDataContributorRole)
  scope: alertDeliveryIndexTable
  properties: {
    principalId: emailIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataContributorRole
  }
}

resource emailSuppressionReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(alertSuppressionsTable.id, emailIdentity.id, tableDataReaderRole)
  scope: alertSuppressionsTable
  properties: {
    principalId: emailIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataReaderRole
  }
}

resource deliveryReportLedgerAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(alertDeliveriesTable.id, deliveryReportIdentity.id, tableDataContributorRole)
  scope: alertDeliveriesTable
  properties: {
    principalId: deliveryReportIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataContributorRole
  }
}

resource deliveryReportIndexReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(alertDeliveryIndexTable.id, deliveryReportIdentity.id, tableDataReaderRole)
  scope: alertDeliveryIndexTable
  properties: {
    principalId: deliveryReportIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataReaderRole
  }
}

resource deliveryReportSuppressionAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(alertSuppressionsTable.id, deliveryReportIdentity.id, tableDataContributorRole)
  scope: alertSuppressionsTable
  properties: {
    principalId: deliveryReportIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataContributorRole
  }
}

resource deliveryReportUsersReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(authUsersTable.id, deliveryReportIdentity.id, tableDataReaderRole)
  scope: authUsersTable
  properties: {
    principalId: deliveryReportIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataReaderRole
  }
}

resource deliveryReportRecipientsReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(alertRecipientsTable.id, deliveryReportIdentity.id, tableDataReaderRole)
  scope: alertRecipientsTable
  properties: {
    principalId: deliveryReportIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataReaderRole
  }
}

resource fanoutUsersReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(authUsersTable.id, fanoutIdentity.id, tableDataReaderRole)
  scope: authUsersTable
  properties: {
    principalId: fanoutIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataReaderRole
  }
}

resource fanoutOutboxReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(alertOutboxTable.id, fanoutIdentity.id, tableDataReaderRole)
  scope: alertOutboxTable
  properties: {
    principalId: fanoutIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataReaderRole
  }
}

resource emailRecipientsReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(alertRecipientsTable.id, emailIdentity.id, tableDataReaderRole)
  scope: alertRecipientsTable
  properties: {
    principalId: emailIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataReaderRole
  }
}

resource emailUsersReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(authUsersTable.id, emailIdentity.id, tableDataReaderRole)
  scope: authUsersTable
  properties: {
    principalId: emailIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataReaderRole
  }
}

resource emailOutboxReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(alertOutboxTable.id, emailIdentity.id, tableDataReaderRole)
  scope: alertOutboxTable
  properties: {
    principalId: emailIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataReaderRole
  }
}

resource emailI18nReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(i18nTable.id, emailIdentity.id, tableDataReaderRole)
  scope: i18nTable
  properties: {
    principalId: emailIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataReaderRole
  }
}

resource publisherTopicSender 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(stockEventsTopic.id, publisherIdentity.id, serviceBusSenderRole)
  scope: stockEventsTopic
  properties: {
    principalId: publisherIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: serviceBusSenderRole
  }
}

resource fanoutSubscriptionReceiver 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(emailFanoutSubscription.id, fanoutIdentity.id, serviceBusReceiverRole)
  scope: emailFanoutSubscription
  properties: {
    principalId: fanoutIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: serviceBusReceiverRole
  }
}

resource fanoutQueueSender 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(fanoutJobsQueue.id, fanoutIdentity.id, serviceBusSenderRole)
  scope: fanoutJobsQueue
  properties: {
    principalId: fanoutIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: serviceBusSenderRole
  }
}

resource fanoutQueueReceiver 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(fanoutJobsQueue.id, fanoutIdentity.id, serviceBusReceiverRole)
  scope: fanoutJobsQueue
  properties: {
    principalId: fanoutIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: serviceBusReceiverRole
  }
}

resource emailQueueSender 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(emailJobsQueue.id, fanoutIdentity.id, serviceBusSenderRole)
  scope: emailJobsQueue
  properties: {
    principalId: fanoutIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: serviceBusSenderRole
  }
}

resource emailQueueReceiver 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(emailJobsQueue.id, emailIdentity.id, serviceBusReceiverRole)
  scope: emailJobsQueue
  properties: {
    principalId: emailIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: serviceBusReceiverRole
  }
}

resource emailQueueRetrySender 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(emailJobsQueue.id, emailIdentity.id, serviceBusSenderRole)
  scope: emailJobsQueue
  properties: {
    principalId: emailIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: serviceBusSenderRole
  }
}

resource deliveryReportQueueReceiver 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(deliveryEventsQueue.id, deliveryReportIdentity.id, serviceBusReceiverRole)
  scope: deliveryEventsQueue
  properties: {
    principalId: deliveryReportIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: serviceBusReceiverRole
  }
}

resource eventGridDeliveryQueueSender 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(deliveryEventsQueue.id, deliveryEventsSystemTopic.id, serviceBusSenderRole)
  scope: deliveryEventsQueue
  properties: {
    principalId: deliveryEventsSystemTopic.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: serviceBusSenderRole
  }
}

resource eventGridDeadLetterWriter 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  // Event Grid validates this role at storage-account scope when an event
  // subscription with managed-identity dead-lettering is created. A
  // container-scoped assignment is sufficient for Blob data access itself,
  // but fails that control-plane validation.
  name: guid(storage.id, deliveryEventsSystemTopic.id, blobContributorRole)
  scope: storage
  properties: {
    principalId: deliveryEventsSystemTopic.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: blobContributorRole
  }
}

resource deliveryEventsSubscription 'Microsoft.EventGrid/systemTopics/eventSubscriptions@2022-06-15' = {
  parent: deliveryEventsSystemTopic
  name: 'acs-email-delivery-reports'
  properties: {
    deliveryWithResourceIdentity: {
      identity: {
        type: 'SystemAssigned'
      }
      destination: {
        endpointType: 'ServiceBusQueue'
        properties: {
          resourceId: deliveryEventsQueue.id
        }
      }
    }
    deadLetterWithResourceIdentity: {
      identity: {
        type: 'SystemAssigned'
      }
      deadLetterDestination: {
        endpointType: 'StorageBlob'
        properties: {
          resourceId: storage.id
          blobContainerName: emailEventDeadLetterContainer.name
        }
      }
    }
    eventDeliverySchema: 'EventGridSchema'
    filter: {
      includedEventTypes: [
        'Microsoft.Communication.EmailDeliveryReportReceived'
      ]
      isSubjectCaseSensitive: false
    }
    retryPolicy: {
      eventTimeToLiveInMinutes: 1440
      maxDeliveryAttempts: 30
    }
  }
  dependsOn: [
    eventGridDeliveryQueueSender
    eventGridDeadLetterWriter
  ]
}

output acrName string = registry.name
output acrLoginServer string = registry.properties.loginServer
output communicationServiceName string = communicationService.name
output communicationEndpoint string = 'https://${communicationService.name}.communication.azure.com'
output containerEnvironmentName string = containerEnvironment.name
// Backward-compatible alias consumed by the current web deployment.
output identityName string = identity.name
output webIdentityName string = identity.name
output webIdentityId string = identity.id
output webIdentityClientId string = identity.properties.clientId
output scannerIdentityName string = scannerIdentity.name
output scannerIdentityId string = scannerIdentity.id
output scannerIdentityClientId string = scannerIdentity.properties.clientId
output retentionIdentityName string = retentionIdentity.name
output retentionIdentityId string = retentionIdentity.id
output retentionIdentityClientId string = retentionIdentity.properties.clientId
output webRetentionIdentityName string = webRetentionIdentity.name
output webRetentionIdentityId string = webRetentionIdentity.id
output webRetentionIdentityClientId string = webRetentionIdentity.properties.clientId
output publisherIdentityName string = publisherIdentity.name
output fanoutIdentityName string = fanoutIdentity.name
output emailIdentityName string = emailIdentity.name
output deliveryReportIdentityName string = deliveryReportIdentity.name
output keyVaultUrl string = vault.properties.vaultUri
output senderAddress string = 'DoNotReply@${emailDomain.properties.mailFromSenderDomain}'
output storageAccountName string = storage.name
output storageAccountUrl string = 'https://${storage.name}.blob.${environment().suffixes.storage}'
output serviceBusNamespaceName string = serviceBus.name
output serviceBusNamespace string = '${serviceBus.name}.servicebus.windows.net'
output resourceToken string = token
