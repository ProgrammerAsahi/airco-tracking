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

@description('Create legacy non-table RBAC assignments for the original shared runtime identity. Set false when upgrading an environment whose assignments were created manually.')
param manageSharedIdentityRbac bool = true

@description('Optional already-verified customer-managed Email Communication domain ID to preserve alongside the Azure-managed fallback.')
param customEmailDomainId string = ''

@secure()
@description('Optional operations mailbox for Azure Monitor Service Bus alerts. Leave empty to create dashboard-visible alerts without email actions.')
param operationsAlertEmail string = ''

@description('Create Standard Service Bus entities with 16 partitions. This creation-time setting requires entity recreation to change.')
param enableServiceBusPartitioning bool = true

var token = resourceToken
var acrName = 'aircotracker${token}'
var storageName = 'aircostate${token}'
var identityName = '${prefix}-identity'
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
  name: identityName
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
    softDeleteRetentionInDays: 7
    enablePurgeProtection: true
    tenantId: tenant().tenantId
  }
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
var keyVaultSecretsUserRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
var communicationOwnerRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '09976791-48a7-449e-bb21-39d1a415f350')
var tableDataContributorRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3')
var tableDataReaderRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '76199698-9eea-4c19-bc75-cec21354c6b6')
var serviceBusSenderRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39')
var serviceBusReceiverRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0')

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (manageSharedIdentityRbac) {
  name: guid(registry.id, identity.id, acrPullRole)
  scope: registry
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRole
  }
}

resource blobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (manageSharedIdentityRbac) {
  name: guid(storage.id, identity.id, blobContributorRole)
  scope: storage
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: blobContributorRole
  }
}

// The web app and scanner intentionally share one legacy runtime identity,
// but they do not need account-wide Table Data Contributor. Keep each data
// plane grant at the concrete table resource so a future table is private by
// default and a compromised web process cannot enumerate unrelated tables.
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

resource sharedAlertOutboxTableContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(alertOutboxTable.id, identity.id, tableDataContributorRole)
  scope: alertOutboxTable
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataContributorRole
  }
}

resource sharedAlertDeliveriesTableContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(alertDeliveriesTable.id, identity.id, tableDataContributorRole)
  scope: alertDeliveriesTable
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataContributorRole
  }
}

resource sharedAlertDeliveryIndexTableContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(alertDeliveryIndexTable.id, identity.id, tableDataContributorRole)
  scope: alertDeliveryIndexTable
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tableDataContributorRole
  }
}

resource sharedAlertSuppressionsTableContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(alertSuppressionsTable.id, identity.id, tableDataContributorRole)
  scope: alertSuppressionsTable
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

resource vaultReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (manageSharedIdentityRbac) {
  name: guid(vault.id, identity.id, keyVaultSecretsUserRole)
  scope: vault
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRole
  }
}

resource communicationAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (manageSharedIdentityRbac) {
  name: guid(communicationService.id, identity.id, communicationOwnerRole)
  scope: communicationService
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: communicationOwnerRole
  }
}

resource emailCommunicationAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(communicationService.id, emailIdentity.id, communicationOwnerRole)
  scope: communicationService
  properties: {
    principalId: emailIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: communicationOwnerRole
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

resource emailKeyVaultSecretsReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, emailIdentity.id, keyVaultSecretsUserRole)
  scope: vault
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
output identityName string = identity.name
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
