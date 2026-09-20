# incident-response-runbooks

Ten runbooks for common security incidents. Each one follows the same skeleton so on-call responders aren't reading a different document structure for every page.

## Skeleton

Every runbook has these sections, in this order:

1. **Trigger**, the alert, ticket, or report that brings you to this page
2. **Immediate**, what to do in the first 15 minutes
3. **Investigation**, queries, log sources, indicators of scope
4. **Containment**, stopping the bleeding without destroying evidence
5. **Eradication**, removing attacker access and persistence
6. **Recovery**, bringing services back, restoring data
7. **Post-incident**, write-up, lessons, control changes
8. **Reference data**, the specific commands, IAM actions, log queries used in the steps above

## Contents

| # | Runbook | When to use |
|---|---------|-------------|
| 01 | [Leaked AWS keys](runbooks/01-leaked-aws-keys.md) | Access key found in a public repo, paste site, or external report |
| 02 | [Public S3 bucket](runbooks/02-public-s3-bucket.md) | Bucket exposed to the internet, contents potentially indexed |
| 03 | [Ransomware](runbooks/03-ransomware.md) | Encryption activity on endpoints or file servers |
| 04 | [Account takeover](runbooks/04-account-takeover.md) | User reports they didn't perform actions logged under their account |
| 05 | [Supply-chain compromise](runbooks/05-supply-chain-compromise.md) | Malicious package, compromised vendor, or build-system tampering |
| 06 | [Insider threat](runbooks/06-insider-threat.md) | Employee or contractor exfiltrating or sabotaging |
| 07 | [DDoS](runbooks/07-ddos.md) | Volumetric or application-layer attack against public endpoints |
| 08 | [Data exfiltration](runbooks/08-data-exfiltration.md) | Anomalous egress, bulk export, or unauthorized data access |
| 09 | [Malware on endpoint](runbooks/09-malware-on-endpoint.md) | EDR alert, suspicious process, or user-reported infection |
| 10 | [Third-party breach](runbooks/10-third-party-breach.md) | Vendor notifies you of their breach affecting your data |

## Intended use

Print these. Or pin them in the on-call runbook system. Or paste them into the team wiki. They are not theoretical; every step is something you actually do, with the actual command or console path to do it.

If your environment differs from the assumptions (AWS-centric, mid-size org, mature logging), fork the runbook and adjust. The skeleton is the value; the AWS-specific commands are the example.

## Contributing

If you run into a runbook that's wrong, misordered, or missing a critical step, open an issue with the specifics. Patches that add cloud providers (Azure, GCP) under each runbook are welcome.

## Related repositories

Part of a 10-repo security audit set.

Browser-based audit tools:
- [iam-policy-analyzer](https://github.com/0xelitesystem/iam-policy-analyzer)
- [terraform-security-linter](https://github.com/0xelitesystem/terraform-security-linter)
- [kubernetes-manifest-security-scanner](https://github.com/0xelitesystem/kubernetes-manifest-security-scanner)
- [session-cookie-auditor](https://github.com/0xelitesystem/session-cookie-auditor)
- [regex-redos-checker](https://github.com/0xelitesystem/regex-redos-checker)

Reference collections:
- [ai-llm-security-audit](https://github.com/0xelitesystem/ai-llm-security-audit)
- [api-security-audit-checklist](https://github.com/0xelitesystem/api-security-audit-checklist)
- [secrets-leak-response-runbook](https://github.com/0xelitesystem/secrets-leak-response-runbook)
- [threat-modeling-worksheets](https://github.com/0xelitesystem/threat-modeling-worksheets)

## License

MIT. See [LICENSE](LICENSE).
