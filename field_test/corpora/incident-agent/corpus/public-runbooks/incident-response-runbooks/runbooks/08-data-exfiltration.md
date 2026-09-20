# Data Exfiltration

**Trigger:** DLP alert, anomalous egress volume, threat intel report of your data on dark web, customer notification of leak, abnormal database query patterns.

## Immediate (first 15 minutes)

1. Confirm exfil is occurring or has occurred. Review egress logs (NetFlow, VPC Flow Logs, proxy logs) for the suspect period.
2. If active: block the egress destination at the firewall and isolate the source host.
3. Preserve all relevant logs immediately, many have short retention by default.
4. Engage legal and privacy teams. Regulatory notification clocks may already be running.

## Investigation

- Identify what data was taken. Without knowing this, you cannot accurately notify or remediate.
- Reconstruct the path: initial access → privilege escalation → discovery → collection → exfil → cleanup.
- Common exfil channels in priority order to check:
  - DNS tunneling (queries with high entropy subdomains)
  - HTTPS to attacker infrastructure or legitimate file-share services
  - Cloud storage (uploads to attacker-controlled S3/Azure Blob/GCS)
  - Email (forwarding rules, mass attachments)
  - Database direct egress (pg_dump, mysqldump on internet-facing host)
  - Removable media (for insiders)
- Check for staging: attackers often archive and compress data on a host before egress. Look for unusual `.zip`, `.7z`, `.tar.gz` files in temp directories.

## Containment

- Block exfil channel at every layer (firewall, DNS, proxy).
- Disable compromised accounts.
- For database exfil: revoke the credential, disable the user, audit other queries from same source.
- For email exfil: remove forwarding rules, block external recipient, recall messages if possible.

## Eradication

- Remove attacker access (see relevant runbook for the initial access vector).
- Hunt for additional staging locations and clean up.

## Recovery

- Restore normal operations only after attacker is fully evicted.
- Implement enhanced monitoring on the affected data store for 60-90 days.

## Post-incident, regulatory and notification

- Determine notification obligations based on data type and jurisdiction:
  - PII: GDPR (72h to supervisory authority), state breach laws (varies, often 30-60 days), sector-specific (HIPAA, GLBA).
  - Cardholder data: PCI DSS notification to card brands.
  - Health data: HHS / state health authorities.
  - Contractual: customer notification clauses, cyber insurance carrier.
- Prepare communications: regulator filings, customer notices, internal stakeholder briefings, public statement if required.
- Engage outside counsel and a forensics firm, privilege protections matter for litigation that often follows.

## Post-incident, technical

- Implement egress filtering: default-deny outbound, allowlist only required destinations.
- Add DLP for sensitive data classes.
- Encrypt data at rest with keys the attacker couldn't have accessed.
- Tokenize or pseudonymize where possible, exfil of tokens is less harmful than exfil of plaintext.

## Reference data to capture

- Data classification and volume taken (records, GB)
- Exfil channel(s) and destination(s)
- Dwell time before detection
- Notification timeline (regulators, customers, public)
- Estimated cost (response, notification, regulatory fines, litigation reserve)
