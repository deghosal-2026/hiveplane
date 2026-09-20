# Public S3 Bucket Exposure

**Trigger:** Trusted Advisor / Macie / external researcher report, or proactive scan finds bucket with `Block Public Access` off, ACL `public-read`, or policy granting `Principal: *`.

## Immediate (first 15 minutes)

1. Enable account-level Block Public Access if not on: `aws s3control put-public-access-block --account-id <id> --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true`. Be aware this immediately breaks any legitimate public-facing buckets, confirm scope first.
2. For the specific bucket, enable bucket-level BPA: `aws s3api put-public-access-block --bucket <name> --public-access-block-configuration ...`.
3. Remove the offending bucket policy or ACL.
4. If the bucket fronts a CloudFront distribution, configure Origin Access Control instead of public read.

## Investigation

- Enable S3 server access logging or check existing logs. Cross-reference with CloudTrail data events if enabled.
- Identify what objects were accessible. List versions including delete markers, attackers sometimes delete after exfil.
- Look at requester IPs: large download volumes from unfamiliar ASNs, sequential GET patterns, automated `User-Agent` strings.
- Determine sensitivity of exposed objects: PII, secrets, internal documents, source code, backups.

## Containment

- Confirm bucket is now private. Test from an unauthenticated client.
- If objects contained credentials, treat each as a separate incident (rotate per credential type).
- If PII exposed, engage legal/privacy counsel immediately, regulatory clocks may start ticking (GDPR 72h, state breach laws vary).

## Eradication

- Audit other buckets in the account for the same misconfiguration.
- Delete attacker-uploaded objects if the bucket was writable (check for ransomware notes, crypto mining payloads).

## Recovery

- For legitimate public-content buckets, migrate to CloudFront + OAC.
- Re-validate downstream consumers still work with the new access pattern.

## Post-incident

- Add Config rule `s3-bucket-public-read-prohibited` and `s3-bucket-public-write-prohibited`.
- Add SCP denying `s3:PutBucketPolicy` and `s3:PutBucketAcl` that grant public access.
- Require all new buckets to be created via Terraform/CloudFormation modules with BPA on by default.

## Reference data to capture

- Bucket name, region, account, creation date
- How long it was public, who made it public (CloudTrail `PutBucketPolicy` / `PutBucketAcl`)
- Object inventory and sensitivity classification
- Access log analysis: distinct IPs, total bytes egressed
