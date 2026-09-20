# Account Takeover (User or Admin)

**Trigger:** User reports inability to log in, suspicious activity from their account, impossible-travel alert, password change they didn't make, MFA device they don't recognize.

## Immediate (first 15 minutes)

1. Force sign-out of all active sessions for the account (in Okta/Entra ID/Google Workspace: revoke sessions and OAuth tokens).
2. Reset password and require new MFA enrollment from a verified channel (in-person or via known phone number, not email if email may also be compromised).
3. If admin account: temporarily downgrade privileges until investigation completes. Do not delete, you need the audit trail.
4. Notify the user out-of-band and confirm their identity.

## Investigation

- Pull authentication logs for the past 90 days. Look for: new device fingerprints, unusual geolocations, failed-then-successful auth (credential stuffing success), MFA bypasses (push fatigue, SIM swap indicators).
- Check email rules: attackers commonly add inbox rules to auto-delete or forward security notifications. In M365: `Get-InboxRule -Mailbox <user>`. In Google: check filters and forwarding addresses.
- Check OAuth grants: any apps the user (or attacker) authorized recently. Revoke unfamiliar ones.
- Check delegated permissions / mailbox delegation.
- For admin accounts: review all administrative actions in the last 90 days against the user's normal pattern.

## Containment

- Revoke all OAuth tokens and app passwords.
- Remove suspicious mailbox rules and forwarding.
- If financial fraud suspected (wire transfer requests, vendor payment changes), freeze related transactions and notify the bank.
- Check if the same credentials were used elsewhere (password reuse). Force reset for any reused-password accounts.

## Eradication

- Confirm the attacker no longer has any persistence: tokens, app passwords, mailbox rules, alternate MFA factors, recovery email/phone.
- Review and restore any settings the attacker changed (display name, signature, away message used for fraud).

## Recovery

- Re-enable the account with new credentials and fresh MFA.
- User completes a checklist: review sent items for fraudulent emails, notify recipients of any compromise, scan endpoint for infostealer.

## Post-incident

- Root cause: phishing? Credential stuffing? Infostealer on endpoint? SIM swap? Each has a different prevention path.
- Move to phishing-resistant MFA (FIDO2/WebAuthn) for admin accounts at minimum.
- Implement Conditional Access: block legacy auth, require compliant device, geo-restrict where appropriate.
- Add an authentication anomaly detection rule for the user's role.

## Reference data to capture

- Initial access timestamp, attacker IPs/devices
- Actions taken by attacker (emails sent, files accessed, settings changed)
- Whether other accounts were used as pivots
- Financial impact (if any)
