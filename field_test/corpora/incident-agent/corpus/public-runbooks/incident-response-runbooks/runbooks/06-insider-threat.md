# Insider Threat (Malicious or Negligent)

**Trigger:** DLP alert, anomalous data access pattern, departing employee with elevated access, manager report of policy violation, customer complaint involving employee.

## Immediate (first 15 minutes)

1. Engage HR and legal **before** taking technical action. Insider cases have employment law and evidentiary implications that differ from external attacks.
2. Do not confront the individual until evidence is preserved.
3. Quietly preserve: endpoint disk image, mailbox, file activity logs, badge access logs, VPN logs, code commits.
4. Maintain chain of custody documentation for all evidence collected.

## Investigation

- Build a timeline of unusual activity: bulk downloads to USB or personal cloud (Dropbox, Google Drive personal), printing volume spikes, after-hours access, queries on data outside their job function.
- Compare to peer baseline, what does normal access look like for this role?
- Review communications (with legal approval) for indicators of intent: job-search activity, contact with competitors, complaints about the company.
- For developers: review git commits, especially squashed/force-pushed history, deletion of logs, addition of backdoors or hardcoded credentials.

## Containment

- Coordinate with HR on timing of access removal, usually concurrent with notification meeting.
- Disable accounts: SSO, VPN, email, cloud consoles, source control, badge access.
- Recover company devices.
- Revoke any credentials or API keys the individual could have created.
- Notify relevant teams (without revealing who) to watch for delayed-action sabotage: scheduled tasks, time-bombed logic, dormant backdoors.

## Eradication

- Audit all systems the individual administered for unauthorized changes.
- Review code they authored for backdoors. This may require external review.
- Check for accounts they created (service accounts, "test" users, shared credentials).

## Recovery

- Reassign their work and access to other team members.
- Communicate to affected teams as needed (consistent with HR/legal guidance).

## Post-incident

- Review what data they could access. Apply least privilege more strictly going forward.
- Implement DLP for sensitive data egress channels.
- For high-trust roles: consider enhanced monitoring, separation of duties, mandatory leave.
- Document what was taken, who was notified, and any regulatory or contractual obligations.

## Reference data to capture

- Individual's role, tenure, access scope
- Specific actions of concern with timestamps and evidence
- Data taken (volume, sensitivity, recipients)
- Legal/HR actions and outcomes (kept separate from technical IR record)

## Cautions

- Insider IR is highly sensitive. Limit knowledge of the investigation to a need-to-know group.
- Bias is a real risk: investigate the evidence, not the person. Negligence and malice produce similar artifacts but warrant different responses.
