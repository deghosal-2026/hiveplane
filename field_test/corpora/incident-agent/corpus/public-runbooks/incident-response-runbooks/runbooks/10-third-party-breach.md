# Third-Party / Vendor Breach

**Trigger:** Vendor notifies you of their breach, news report of a SaaS provider compromise, your data appears in a leak attributed to a vendor.

## Immediate (first 15 minutes)

1. Confirm the breach with the vendor directly through a verified channel, not via email link in the notification (which itself could be a phishing pretext).
2. Determine what data of yours they held: scope, sensitivity, volume.
3. Determine if your authentication to their service is at risk (SSO tokens, API keys, shared secrets).
4. Identify your contractual rights: SLA breach, indemnification, audit rights.

## Investigation

- Read the vendor's incident notice carefully. What do they confirm vs what's still under investigation? Vendor disclosures are often incomplete on day one.
- What technical details do they share? IOCs, attacker techniques, dwell time, data classes accessed.
- Did the breach involve your specific tenant / dataset? Many SaaS breaches are scoped to specific customers, confirm yours is or isn't affected.
- For credential-related breaches (password manager, identity provider): assume credentials stored or transited through the vendor are exposed.

## Containment

- Rotate all credentials shared with or stored at the vendor.
- For OAuth/SSO integrations: revoke and re-establish, with attention to refresh tokens.
- For API integrations: rotate keys, audit recent API activity for anomalies.
- If the vendor's infrastructure is compromised, treat any data flowing to/from them as suspect during the incident window.

## Eradication

- This is largely the vendor's responsibility. Track their progress via status page and direct communication.
- On your side: ensure no attacker persistence has crossed from vendor to your environment (e.g., malicious OAuth grant, attacker-set inbox rule).

## Recovery

- Resume normal vendor usage only after vendor confirms remediation.
- Update integration to use any new security controls the vendor implements.

## Post-incident

- Reassess the vendor: was this a one-time event or systemic? Their post-mortem should answer this. If they don't publish one, that's a signal.
- Review your vendor inventory: how much data do they hold, do you have a backup, can you switch providers if needed?
- Update your TPRM (third-party risk management) process based on lessons learned.
- Notify your own customers if your contractual or regulatory obligations require it (subprocessor breach often does under GDPR).
- Cyber insurance carrier notification.

## Reference data to capture

- Vendor name, service, and your data scope held there
- Vendor's incident timeline (their disclosure, breach window, your data exposure)
- Credentials and integrations rotated
- Customer / regulator notifications you made
- Contractual recourse pursued

## Considerations for future

- Encrypt data at rest with customer-managed keys where the vendor supports it (BYOK / HYOK).
- Minimize data shared with vendors, send only what's necessary, redact PII when possible.
- Negotiate breach notification timelines into contracts (often 24-72h).
- Run periodic vendor security reviews proportionate to data sensitivity.
