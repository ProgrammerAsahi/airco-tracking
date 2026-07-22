# Airco Tracker — email delivery, consent, and reputation

<p align="center">
  <a href="./EMAIL_DELIVERY.zh.md"><img alt="简体中文" src="https://img.shields.io/badge/EMAIL_DELIVERY-简体中文-d73a49"></a>
  <a href="./EMAIL_DELIVERY.md"><img alt="English" src="https://img.shields.io/badge/EMAIL_DELIVERY-English-0969da"></a>
</p>

This document is the operational, privacy, and deliverability baseline for authentication and stock-alert email. It complements [ALERT_PIPELINE.md](./ALERT_PIPELINE.md). Do not treat a locally prepared control as operational until the handoff records its production deployment and verification.

## Current production status (2026-07-22)

- The customer-managed `airco-tracker.eu` ACS sender domain is linked and selected explicitly in both applications. Domain ownership, SPF, DKIM, and DKIM2 are verified; DMARC remains deliberately at `p=none` while reputation is observed.
- Real authentication-email canaries reached both Gmail and Outlook inboxes. Original-message headers confirmed the branded sender domain, aligned SPF/DKIM, DMARC pass, and `Reply-To: support@airco-tracker.eu`. These canaries verify the production ACS sender path without exposing verification codes or recipient addresses in operational records.
- The deployed stock-alert path uses topic `stock-events`, subscription `email-fanout`, and exactly three Service Bus queues: `email-fanout-jobs`, `email-jobs`, and the PII-bounded final-report queue `acs-email-delivery-events`. Active and dead-letter counts returned to zero after production checks.
- No active, entitled alert recipient existed during the latest release verification, so the system correctly produced no stock-alert delivery. The full subscriber-targeted canary remains deferred until a real opted-in entitlement exists; direct ACS delivery, queue health, and fail-closed recipient reconciliation were verified independently.
- The higher-quota request has been submitted and remains **Open/pending**. Its private case identifier stays outside this public repository. Production must stay at one email worker and a global 13-second minimum interval until Azure approves the request.

## Mail identities and inbound routing

- Outbound identity: `Airco Tracker <DoNotReply@airco-tracker.eu>` through the verified customer-managed Azure Communication Services (ACS) domain.
- Reply address: `support@airco-tracker.eu`. Both authentication mail and stock-alert mail set this address as `Reply-To`; the envelope sender remains the ACS sender.
- DMARC aggregate address: `dmarc@airco-tracker.eu`. It is for automated aggregate XML reports, not customer support.
- `support` and `dmarc` are separate Dynadot email-forwarding aliases routed to monitored existing mailboxes. The destination mailboxes are operational secrets/PII and must not be written in Git, Bicep, application settings, logs, or this runbook.
- Dynadot's forwarding MX must be visible publicly before relying on either address. Verify with `dig MX airco-tracker.eu` and real inbound canaries to both aliases. Do not replace the existing web A/CNAME records or the ACS SPF/DKIM records when enabling forwarding.
- Dynadot's free forwarding is receive-only and is currently limited to 500 forwarded messages per domain per day. It is adequate for support and aggregate reports at the initial scale, but a reply sent from the destination mailbox may expose that mailbox unless a separate authenticated branded-mailbox service is configured.

## SPF, DKIM, and DMARC

ACS SPF, DKIM, and DKIM2 must remain verified and aligned with `airco-tracker.eu`. There must be only one SPF TXT policy at the domain apex; add future senders to that policy instead of publishing a second SPF record.

Start DMARC in observation mode:

```text
Host:  _dmarc
Type:  TXT
Value: v=DMARC1; p=none; rua=mailto:dmarc@airco-tracker.eu; pct=100
```

Do not configure `ruf`; forensic reports may contain message or recipient data and are unnecessary for the initial programme. Confirm that aggregate XML reports reach the monitored mailbox and that every legitimate sender is SPF- or DKIM-aligned. Keep `p=none` during the two-to-four-week warm-up. Move deliberately to `quarantine`, then `reject`, only after the report history shows no unidentified legitimate sender and a rollback owner is available.

## Reply-To behavior

`EMAIL_REPLY_TO` in the backend and `AUTH_EMAIL_REPLY_TO` in the web/auth service must resolve to `support@airco-tracker.eu`. ACS receives it through its structured `replyTo` field. A missing or malformed production Reply-To is a release blocker: customers must be able to reach a monitored address without exposing an Azure resource hostname or a personal address in the original message.

## User consent and unsubscribe

Email alerts are a preference independent of the paid pass and realtime-inventory entitlement:

- New users default to `emailAlertsEnabled=true`. For legacy profiles, a missing field is interpreted as enabled to preserve the previously explicit paid-alert behavior; an explicit `false` always suppresses alert fan-out.
- Profile exposes an authenticated enable/pause switch. Pausing alert mail does not revoke the pass and does not remove realtime-inventory access.
- Every non-test stock alert contains a visible unsubscribe link plus RFC 8058 headers: `List-Unsubscribe` and `List-Unsubscribe-Post: List-Unsubscribe=One-Click`.
- The RFC 8058 endpoint accepts only `POST` with `application/x-www-form-urlencoded` and `List-Unsubscribe=One-Click`. It is idempotent, does not require a session cookie, and reveals no account-existence state.
- The browser-facing link presents a confirmation page before using the same pause operation. Users can later re-enable alerts in Profile.
- The capability token contains only a stable UUID and token version, is authenticated with HMAC-SHA-256, and never contains an email address. The signing key lives in Key Vault and is shared by the two repositories through Managed Identity-backed secret references; it must never appear in Git, an image, or a browser bundle.
- Changing an email address or changing the alert preference increments the token version, invalidating older links. Account deletion also makes all links inert.

The sender must not send marketing or unrelated promotional content under this transactional consent. Recipient addresses come only from users who registered, verified their address, hold a non-expired `alerts` or `radar` pass, and have not paused alerts. Purchased, scraped, rented, or third-party lists are prohibited.

## Final delivery and hard-bounce suppression

ACS operation acceptance is not proof of inbox delivery. The final-status path is deliberately independent of the stock-event queues:

```text
email worker → ACS accepts deterministic operation ID
                    │
                    └─ ACS recipient delivery report
                         → Event Grid system topic
                         → acs-email-delivery-events queue
                         → airco-alert-delivery-worker
                              ├─ correlate through alertdeliveryindex
                              ├─ update alertdeliveries final status
                              └─ update alertsuppression for hard failures
```

The ledger first records `accepted`, then one of `delivered`, `expanded`, `bounced`, `provider_suppressed`, `quarantined`, `filtered_spam`, or `provider_failed`. The legacy `sent` state remains no-resend compatible.

The three queues have deliberately separate responsibilities: `email-fanout-jobs` carries recipient-shard work, `email-jobs` carries opaque event/recipient delivery jobs, and `acs-email-delivery-events` carries short-lived provider reports. The `stock-events` entity is a topic, not a fourth queue.

- The email worker binds the deterministic ACS message/operation ID to opaque event, recipient, and delivery IDs before sending, preventing a fast Event Grid report from racing an absent correlation row.
- A recipient-scoped address fingerprint binds the report to the exact verified address without persisting the address in the index or suppression table. An old-address bounce therefore cannot suppress a newly verified address.
- `bounced` and provider `suppressed` are hard-failure evidence and activate system suppression. The email worker checks suppression before and immediately before every send.
- A newer `delivered` report for the same address fingerprint can clear suppression. Soft/transient statuses do not permanently suppress an address.
- Unknown authentication-code reports are ignored because only stock-alert sends have a correlation binding. Invalid or mismatched reports fail closed without logging or dead-lettering their recipient payload.

## PII exception and retention

Normal stock-event Service Bus payloads remain PII-free. The ACS recipient delivery-report schema necessarily contains the destination address, so the dedicated final-delivery path is a narrowly bounded exception:

- `acs-email-delivery-events` is a dedicated queue with a one-day TTL. It is not shared with stock events, fan-out jobs, or email jobs.
- Expired delivery-report messages are not copied to the Service Bus DLQ because DLQ messages have no entity-TTL enforcement.
- If processing reaches `maxDeliveryCount`, a daily privacy cleanup job removes raw delivery-report DLQ messages. Invalid/unbound payloads are completed without copying their body into logs or retry metadata.
- Event Grid delivery dead letters use a private Blob container with a seven-day lifecycle deletion rule. They exist only to diagnose Event Grid-to-Service-Bus failures.
- `alertdeliveryindex`, `alertdeliveries`, and `alertsuppression` store opaque IDs, final status, and pseudonymous fingerprints, not plaintext addresses. Correlation index rows follow the 90-day delivery-metadata retention policy; the daily privacy job removes suppression rows whose canonical account no longer exists or is inactive.
- Do not enable recipient-level ACS `EmailStatusUpdateOperational` logs in Log Analytics. The application consumes those reports and logs only opaque delivery IDs. ACS send-operation diagnostics may be retained for quota/request troubleshooting.

Any debugging export containing a raw Event Grid body is exceptional PII handling: keep it encrypted, access-restricted, outside Git and tickets, and delete it as soon as the incident is resolved and no later than seven days.

## Monitoring and response

Required signals are:

- Event Grid `DeadLetteredCount`, `DroppedEventCount`, and repeated delivery-attempt failures.
- Active count, oldest-message age, and DLQ count for `email-fanout-jobs`, `email-jobs`, and `acs-email-delivery-events`, plus the `stock-events/email-fanout` subscription.
- Acceptance-to-final-status latency; delivered, bounced, provider-suppressed, quarantined, spam-filtered, and provider-failed rates.
- New system suppressions, unmatched/invalid reports, and the scheduled DLQ/privacy-cleanup result.
- ACS send failures, `429`/quota responses, and the continued Gmail/Outlook inbox canaries.
- SPF, DKIM, and DMARC aggregate-report health; forwarding failures at `support` or `dmarc` are incidents.

Foundation alerts cover Event Grid dead-letter, dropped-event, and repeated delivery-failure metrics, in addition to the Service Bus backlog/dead-letter/throttling/server-error alerts. Privacy-safe scheduled queries also alert on accepted deliveries still missing a final report after two hours and on bounced, provider-suppressed, quarantined, spam-filtered, or provider-failed outcomes. Alert receivers must remain monitored without recording their address in documentation. Investigate a delivery-report outage before raising sender concurrency: otherwise hard bounces could continue without entering suppression.

## Initial ACS quota request

The higher-quota request was submitted only after inbound routing, DMARC observation, Reply-To, user opt-out, one-click unsubscribe, final-delivery ingestion, hard-bounce suppression, privacy cleanup, and monitoring were deployed and production-tested. It remains **Open/pending**; submission is not approval and does not authorize a throughput increase. Keep the private case identifier outside this public repository.

Use truthful planning values:

| Field | Initial request |
|---|---|
| Operator | Independent individual operator; no incorporated company |
| Service | Consumer subscription service tracking portable-air-conditioner availability across European retailers, with transactional stock alerts and a realtime inventory dashboard |
| Email type | Transactional, user-requested stock-availability alerts; no unsolicited marketing |
| Recipient source | Direct registration, email verification, paid alert entitlement, and user-controlled alert preference; no purchased/scraped/third-party lists |
| Initial users | Up to 1,000 |
| Requested portal tier | Tier `250`: provider ceiling of 1,000 messages/minute and 3,000 messages/hour |
| Initial application cap | At most 100 messages/minute after approval and gradual warm-up |
| Hourly volume | 3,000 messages/hour |
| Daily volume | 10,000 messages/day |
| Peak period | European daytime, especially hot afternoons and bursty retailer restocks |
| Controls | Stock-cycle de-duplication, a global sender throttle, country-delivery filtering, one-click unsubscribe, final-status monitoring, and hard-bounce suppression |

Warm the new domain gradually for two to four weeks. Do not immediately use the requested ceiling and do not generate synthetic bulk traffic merely to warm the domain. Keep the current one-worker/13-second limit until Azure approves a new quota, then raise `EMAIL_MIN_SEND_INTERVAL_SECONDS` and replica limits in measured steps while monitoring final delivery and complaint signals.

## Production verification checklist

1. `dig MX`, `dig TXT _dmarc`, SPF, DKIM, and DKIM2 return the intended records.
2. Real external mail reaches both forwarding aliases; a customer reply follows `Reply-To` to the monitored support destination.
3. A targeted alert contains the visible link and both RFC 8058 headers; browser confirmation and one-click POST each pause only email alerts.
4. Profile can re-enable email alerts without changing the paid plan or realtime entitlement.
5. A successful ACS report changes `accepted` to `delivered`; a hard-bounce test creates suppression and prevents another send to the same address fingerprint.
6. The dedicated queue and all dead-letter locations return to zero; the seven-day Blob lifecycle and daily DLQ cleanup job are enabled.
7. Event Grid, Service Bus, ACS, and inbox-canary alerts reach the operations receiver.
8. The quota request is submitted and still pending. Keep the support-case ID free of contact PII, and do not raise sender concurrency until Azure records approval and the measured warm-up begins.
