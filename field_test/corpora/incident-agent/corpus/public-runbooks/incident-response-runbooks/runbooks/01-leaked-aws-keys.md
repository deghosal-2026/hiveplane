# Leaked AWS Access Keys

**Trigger:** Keys committed to public repo, posted in chat, found in logs, GuardDuty alert, or AWS abuse notice.

## Immediate (first 15 minutes)

1. Identify the key: AKIA prefix = long-lived IAM user, ASIA prefix = STS temporary.
2. For IAM user keys, run `aws iam update-access-key --access-key-id AKIA... --status Inactive --user-name <user>`. Do not delete yet, you need it for forensics.
3. For STS keys, the underlying role's session can be revoked: `aws iam put-role-policy --role-name <role> --policy-name DenyAll --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Deny","Action":"*","Resource":"*","Condition":{"DateLessThan":{"aws:TokenIssueTime":"<now>"}}}]}'`.
4. Rotate any application using the key. Confirm new key works before proceeding.

## Investigation

- Pull CloudTrail for the access key ID for the last 90 days. Filter `userIdentity.accessKeyId = AKIA...`.
- Look for `RunInstances` (crypto mining), `CreateUser` / `CreateAccessKey` (persistence), `PutBucketPolicy` (exfil staging), `GetSecretValue`, `AssumeRole` to other accounts.
- Check unusual regions, attackers often pivot to `ap-east-1`, `me-south-1`, `af-south-1` where defaults are looser.
- Note source IPs. Any ASN outside your normal egress is suspect.

## Containment

- If the key created other IAM users, disable those users' keys and console access.
- If new roles were created, detach all policies and add a `Deny *` inline policy.
- Snapshot any EC2 instances launched by the key before terminating, preserve evidence.
- Check Lambda for new functions, especially with public function URLs or `lambda:InvokeFunctionUrl` open to `*`.

## Eradication

- Delete the leaked access key after CloudTrail extraction is complete.
- Remove any IAM users, roles, policies created by the attacker.
- Terminate attacker-launched compute. Delete attacker-created S3 buckets after confirming no legitimate data.
- Rotate any secrets the key could have read from Secrets Manager / Parameter Store.

## Recovery

- Confirm the legitimate workload is operating with new credentials.
- Re-enable any services that were stopped during containment.

## Post-incident

- Why was the key long-lived? Move to IAM roles for EC2/EKS/Lambda, IAM Identity Center for humans.
- Add `aws iam credential-report` to a weekly review.
- Enable GuardDuty in all regions if not already.
- Add a pre-commit hook (e.g., `gitleaks`, `trufflehog`) for future prevention.
- File the incident: timeline, blast radius, cost, root cause, action items with owners.

## Reference data to capture

- Key ID, user/role, creation date, leak source, leak duration
- All API calls made by the key (CloudTrail export)
- Resources created/modified/deleted
- Estimated cost of attacker activity
