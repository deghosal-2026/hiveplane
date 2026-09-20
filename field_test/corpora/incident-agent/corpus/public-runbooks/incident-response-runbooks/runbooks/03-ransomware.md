# Ransomware on Endpoint or Server

**Trigger:** Ransom note observed, mass file extension changes, EDR alert for known ransomware family, user reports inability to open files.

## Immediate (first 15 minutes)

1. **Isolate, do not power off.** Disconnect network cable / disable Wi-Fi / quarantine via EDR. Powering off destroys memory-resident keys and indicators.
2. Notify incident commander and legal. Do not engage with the attacker yet.
3. Preserve the ransom note (filename, contents, contact channel, wallet address).
4. Identify the ransomware family from note + extension + EDR signature. Check `nomoreransom.org` for known decryptors.

## Investigation

- Acquire a memory dump (`winpmem`, `lime`) and disk image before any remediation.
- Identify patient zero: which host was hit first, when, by what user, via what vector (phishing attachment, RDP brute force, vulnerable VPN appliance, malicious SaaS OAuth grant).
- Map lateral movement: SMB shares accessed, AD authentication logs, scheduled tasks created.
- Identify exfiltration: many modern ransomware families are double-extortion. Check egress logs for large transfers to anonymous file hosts (mega.nz, anonfiles, tor exit nodes) in the days before encryption.

## Containment

- Block known C2 IPs/domains at the firewall and DNS resolver.
- Disable compromised AD accounts. Reset Kerberos `krbtgt` twice (12h apart) if domain controllers were touched.
- Revoke and re-issue affected service account credentials.
- Take backups offline immediately. Do not reconnect them until the environment is clean.

## Eradication

- Rebuild affected hosts from known-good images. Do not "clean", reimage.
- Patch the initial access vector before reconnecting any rebuilt host.
- Hunt for persistence: scheduled tasks, run keys, WMI subscriptions, malicious SSH keys, new local admin accounts.

## Recovery

- Restore from backups verified to predate the intrusion (not just the encryption event, dwell time is often weeks).
- Bring systems back in tiers: identity → core infra → business systems → endpoints.
- Monitor restored systems for reinfection for at least 30 days.

## Post-incident

- Decision: pay or not. Document who made it, on what basis, with legal sign-off. Note that paying does not guarantee decryption and may violate sanctions (OFAC).
- Notify regulators / customers as required by jurisdiction.
- Implement: MFA on all remote access, EDR coverage 100%, network segmentation, immutable backups, tested restore procedure.

## Reference data to capture

- First detection time, encryption start time, full host list
- Ransomware family, ransom amount, payment deadline
- Initial access vector (root cause)
- Data exfiltrated (volume, classification)
- Total downtime, recovery cost
