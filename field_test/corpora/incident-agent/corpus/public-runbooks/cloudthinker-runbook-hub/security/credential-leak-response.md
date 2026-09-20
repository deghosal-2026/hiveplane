# Credential / Secret Leak Response

## Metadata

| Field | Value |
|-------|-------|
| **Severity** | Critical |
| **Service** | All services (company-wide) |
| **Owner** | @security-team |
| **Last Reviewed** | 2025-11-30 |
| **Alert** | `SecretLeakDetected` |
| **Tags** | `security`, `credentials`, `incident-response`, `compliance` |

## Summary

A credential, secret, or API key has been detected in a public or semi-public location (GitHub commit, Slack message, log file, etc.). This runbook provides a time-critical response procedure to rotate compromised credentials, assess blast radius, and prevent lateral movement. **Target: all compromised credentials rotated within 15 minutes of detection.**

## Impact

- **Security**: Unauthorized access to production systems, data exfiltration, privilege escalation
- **Compliance**: Potential GDPR/SOC2 violation; mandatory breach notification if customer data accessed
- **Financial**: Unauthorized AWS resource usage (cryptomining); fraudulent API calls to Stripe
- **Reputational**: Customer trust erosion; public disclosure requirements if PII involved
- **Legal**: Regulatory fines; contractual liability to customers

## Prerequisites

- AWS CLI v2 with IAM admin permissions (or `SecurityIncidentResponse` role)
- Access to GitHub organization admin panel
- Access to Stripe dashboard (admin)
- Access to your secrets management (AWS Secrets Manager / Kubernetes Secrets)
- Access to CloudTrail logs and your monitoring platform
- `kubectl` configured for production cluster
- GitGuardian or GitHub Secret Scanning dashboard access
- **Incident Commander paged** before starting (for Critical severity)

## Triage & Diagnosis

### Step 1: Identify What Was Leaked

```bash
# Check the alert source for details
# GitGuardian: https://dashboard.gitguardian.com/workspace/incidents
# GitHub Secret Scanning: https://github.com/orgs/<your-org>/security/secret-scanning

# Determine the type of credential:
# - AWS IAM Access Key (starts with AKIA...)
# - Database password (PostgreSQL RDS)
# - Redis AUTH token
# - Stripe API key (sk_live_...)
# - Anthropic API key (sk-ant-...)
# - Slack Bot Token (xoxb-...)
# - Internal service JWT signing key
# - Qdrant API key
```

### Step 2: Determine Exposure Scope

```bash
# When was the secret first committed?
cd ${REPO_PATH}
git log --all --oneline -S "${LEAKED_SECRET_PREFIX}" --format="%H %ai %an"

# Was the commit pushed to a public branch?
git branch -a --contains ${COMMIT_HASH}

# How long has the secret been exposed?
FIRST_EXPOSED=$(git log --all --oneline -S "${LEAKED_SECRET_PREFIX}" --format="%ai" | tail -1)
echo "First exposed: ${FIRST_EXPOSED}"
echo "Current time: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Check if the repo is public or private
gh repo view --json visibility -q '.visibility'
```

### Step 3: Check Monitoring for Anomalous Activity

```
Monitoring Dashboard: "Security - API Access Patterns"
  -> https://<your-monitoring-url>/dashboard/security-api-access

Monitoring Alert: "Secret Leak Detected"
  -> https://<your-monitoring-url>/monitors/manage?q=SecretLeakDetected

Look for:
  - Unusual API call patterns from unknown IPs
  - Spikes in AWS API calls (especially IAM, S3, EC2)
  - Unauthorized database connections
  - Abnormal data transfer volumes
```

### Step 4: Check CloudTrail for Unauthorized AWS Activity

```bash
# Search CloudTrail for usage of the compromised access key
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=AccessKeyId,AttributeValue=${COMPROMISED_ACCESS_KEY_ID} \
  --start-time $(date -u -v-24H +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --region us-east-1 \
  --query 'Events[].{Time:EventTime,Name:EventName,Source:EventSource,IP:sourceIPAddress}'

# Check for any new IAM users or roles created (privilege escalation)
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=CreateUser \
  --start-time $(date -u -v-24H +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%SZ) \
  --region us-east-1

aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=CreateRole \
  --start-time $(date -u -v-24H +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%SZ) \
  --region us-east-1

# Check for new access keys created
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=CreateAccessKey \
  --start-time $(date -u -v-24H +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%SZ) \
  --region us-east-1
```

## Mitigation Steps

**CRITICAL: Start rotation immediately. Do not wait for full triage to complete.**

### Scenario A: AWS IAM Access Key Leaked

**Target: Deactivate within 5 minutes, rotate within 15 minutes.**

1. **IMMEDIATELY** deactivate the compromised key:
   ```bash
   # Identify the IAM user or role
   aws iam list-access-keys --user-name ${IAM_USER_NAME} --region us-east-1

   # Deactivate the compromised key
   aws iam update-access-key \
     --user-name ${IAM_USER_NAME} \
     --access-key-id ${COMPROMISED_ACCESS_KEY_ID} \
     --status Inactive \
     --region us-east-1

   echo "Key ${COMPROMISED_ACCESS_KEY_ID} deactivated at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
   ```

2. Create a new access key:
   ```bash
   # Generate new access key
   NEW_KEY=$(aws iam create-access-key --user-name ${IAM_USER_NAME} --region us-east-1)
   NEW_ACCESS_KEY_ID=$(echo ${NEW_KEY} | jq -r '.AccessKey.AccessKeyId')
   NEW_SECRET_KEY=$(echo ${NEW_KEY} | jq -r '.AccessKey.SecretAccessKey')

   # STORE THIS SECURELY - do not log or echo in shared terminals
   # WARNING: Avoid echoing secrets to the terminal. Use `kubectl create secret`
   # directly or pipe from a secure source.
   ```

3. Update the secret in AWS Secrets Manager:
   ```bash
   aws secretsmanager update-secret \
     --secret-id your-app/production/aws-credentials \
     --secret-string '{"access_key_id":"'${NEW_ACCESS_KEY_ID}'","secret_access_key":"'${NEW_SECRET_KEY}'"}' \
     --region us-east-1
   ```

4. Update Kubernetes secrets and restart affected services:
   ```bash
   # Update the K8s secret
   kubectl create secret generic aws-credentials -n production \
     --from-literal=AWS_ACCESS_KEY_ID=${NEW_ACCESS_KEY_ID} \
     --from-literal=AWS_SECRET_ACCESS_KEY=${NEW_SECRET_KEY} \
     --dry-run=client -o yaml | kubectl apply -f -

   # Restart services that use AWS credentials
   kubectl rollout restart deployment/service-a -n production
   kubectl rollout restart deployment/service-b -n production
   kubectl rollout restart deployment/service-c -n production
   ```

5. Delete the compromised key after services are stable:
   ```bash
   # Wait for all deployments to be ready
   kubectl rollout status deployment/service-a -n production --timeout=300s
   kubectl rollout status deployment/service-b -n production --timeout=300s

   # Delete the old key
   aws iam delete-access-key \
     --user-name ${IAM_USER_NAME} \
     --access-key-id ${COMPROMISED_ACCESS_KEY_ID} \
     --region us-east-1
   ```

6. Check for persistence mechanisms:
   ```bash
   # List all access keys for the user (should only be the new one)
   aws iam list-access-keys --user-name ${IAM_USER_NAME} --region us-east-1

   # Check for new IAM policies attached during compromise window
   aws iam list-attached-user-policies --user-name ${IAM_USER_NAME} --region us-east-1
   aws iam list-user-policies --user-name ${IAM_USER_NAME} --region us-east-1

   # Check for unauthorized EC2 instances (cryptomining)
   for REGION in us-east-1 us-west-2 eu-west-1 ap-southeast-1; do
     echo "=== ${REGION} ==="
     aws ec2 describe-instances --region ${REGION} \
       --query 'Reservations[].Instances[?LaunchTime>=`'$(date -u -v-24H +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%S)'`].[InstanceId,InstanceType,LaunchTime,State.Name]' \
       --output table
   done

   # Check for unauthorized Lambda functions
   aws lambda list-functions --region us-east-1 \
     --query 'Functions[?LastModified>=`'$(date -u -v-24H +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%S)'`].[FunctionName,LastModified]'
   ```

### Scenario B: Database Credentials Leaked (PostgreSQL RDS)

**Target: Rotate within 15 minutes. Coordinate with backend team for connection pool restart.**

1. Change the RDS master password:
   ```bash
   # Generate a new secure password
   NEW_DB_PASSWORD=$(openssl rand -base64 32 | tr -d '=/+' | head -c 40)

   # Update RDS master password
   aws rds modify-db-instance \
     --db-instance-identifier your-app-prod-db \
     --master-user-password "${NEW_DB_PASSWORD}" \
     --apply-immediately \
     --region us-east-1

   echo "RDS password change initiated. Takes 1-2 minutes to apply."
   ```

2. Wait for the modification to complete:
   ```bash
   aws rds wait db-instance-available \
     --db-instance-identifier your-app-prod-db \
     --region us-east-1

   echo "RDS modification complete at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
   ```

3. Update Secrets Manager and Kubernetes:
   ```bash
   # Update Secrets Manager
   aws secretsmanager update-secret \
     --secret-id your-app/production/database \
     --secret-string '{"username":"your_app_user","password":"'${NEW_DB_PASSWORD}'","host":"your-app-prod-db.xxxxxxxxxxxx.us-east-1.rds.amazonaws.com","port":"5432","database":"your_database"}' \
     --region us-east-1

   # Update Kubernetes secret
   kubectl create secret generic database-credentials -n production \
     --from-literal=POSTGRES_PASSWORD=${NEW_DB_PASSWORD} \
     --from-literal=DATABASE_URL="postgresql://your_app_user:${NEW_DB_PASSWORD}@your-app-prod-db.xxxxxxxxxxxx.us-east-1.rds.amazonaws.com:5432/your_database" \
     --dry-run=client -o yaml | kubectl apply -f -

   # Restart all services that connect to PostgreSQL
   kubectl rollout restart deployment/service-a -n production
   kubectl rollout restart deployment/service-b -n production
   kubectl rollout restart deployment/service-c -n production
   ```

4. Revoke any active sessions from the compromised credentials:
   ```bash
   # Connect to RDS and terminate suspicious connections
   PGPASSWORD="${NEW_DB_PASSWORD}" psql \
     -h your-app-prod-db.xxxxxxxxxxxx.us-east-1.rds.amazonaws.com \
     -U your_app_user -d your_database -c "
     SELECT pg_terminate_backend(pid)
     FROM pg_stat_activity
     WHERE usename = 'your_app_user'
       AND pid != pg_backend_pid()
       AND client_addr NOT IN (
         SELECT DISTINCT host(inet_server_addr())
         FROM pg_stat_activity
         WHERE application_name LIKE 'your-app-%'
       );
   "
   ```

5. Audit database access during compromise window:
   ```bash
   # Check RDS audit logs for suspicious queries
   aws rds describe-db-log-files \
     --db-instance-identifier your-app-prod-db \
     --region us-east-1

   aws rds download-db-log-file-portion \
     --db-instance-identifier your-app-prod-db \
     --log-file-name "error/postgresql.log.$(date +%Y-%m-%d)" \
     --output text \
     --region us-east-1 | grep -E "(SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|COPY)" | tail -100
   ```

### Scenario C: Stripe API Key Leaked

**Target: Rotate within 10 minutes. Financial exposure risk.**

1. **IMMEDIATELY** roll the API key in Stripe dashboard:
   ```
   Stripe Dashboard: https://dashboard.stripe.com/apikeys
   1. Click "Roll key" next to the compromised key
   2. Set expiration for the old key to "Immediately"
   3. Copy the new key
   ```

2. Update the key in Secrets Manager and Kubernetes:
   ```bash
   # Update Secrets Manager
   aws secretsmanager update-secret \
     --secret-id your-app/production/stripe \
     --secret-string '{"secret_key":"'${NEW_STRIPE_SECRET_KEY}'","webhook_secret":"'${STRIPE_WEBHOOK_SECRET}'"}' \
     --region us-east-1

   # Update Kubernetes secret
   kubectl create secret generic stripe-credentials -n production \
     --from-literal=STRIPE_SECRET_KEY=${NEW_STRIPE_SECRET_KEY} \
     --from-literal=STRIPE_WEBHOOK_SECRET=${STRIPE_WEBHOOK_SECRET} \
     --dry-run=client -o yaml | kubectl apply -f -

   # Restart payment service
   kubectl rollout restart deployment/service-c -n production
   kubectl rollout status deployment/service-c -n production --timeout=120s
   ```

3. Check for unauthorized charges or customer data access:
   ```bash
   # Use Stripe CLI to check recent charges from unknown sources
   stripe charges list --limit 50 --created[gte]=$(date -u -v-24H +%s 2>/dev/null || date -u -d '24 hours ago' +%s) 2>/dev/null || \
     echo "Use Stripe Dashboard: https://dashboard.stripe.com/payments?status[]=successful&created[gte]=$(date -u -v-24H +%s 2>/dev/null || date -u -d '24 hours ago' +%s)"

   # Check for new webhook endpoints (attacker persistence)
   stripe webhook_endpoints list 2>/dev/null || \
     echo "Use Stripe Dashboard: https://dashboard.stripe.com/webhooks"
   ```

4. Review Stripe logs for data exfiltration:
   ```
   Stripe Dashboard -> Logs: https://dashboard.stripe.com/logs
   Filter by: last 24 hours, look for:
   - /v1/customers (list/retrieve = data exfiltration)
   - /v1/payment_methods (PCI data access)
   - /v1/charges (unauthorized charges)
   - Unusual IP addresses in log entries
   ```

### Scenario D: Anthropic / LLM Provider API Key Leaked

**Target: Rotate within 15 minutes. Usage-based billing exposure.**

1. Revoke the key immediately via the provider's dashboard:
   ```
   Anthropic Console: https://console.anthropic.com/settings/keys
   1. Find the compromised key
   2. Click "Revoke"
   3. Create a new key
   ```

2. Update the key in infrastructure:
   ```bash
   # Update Secrets Manager
   aws secretsmanager update-secret \
     --secret-id your-app/production/anthropic \
     --secret-string '{"api_key":"'${NEW_ANTHROPIC_API_KEY}'"}' \
     --region us-east-1

   # Update Kubernetes secret
   kubectl create secret generic llm-credentials -n production \
     --from-literal=ANTHROPIC_API_KEY=${NEW_ANTHROPIC_API_KEY} \
     --dry-run=client -o yaml | kubectl apply -f -

   # Restart services that use the LLM API
   kubectl rollout restart deployment/service-a -n production
   kubectl rollout restart deployment/service-b -n production
   ```

3. Check Anthropic usage dashboard for unauthorized consumption:
   ```
   Anthropic Console -> Usage: https://console.anthropic.com/settings/usage
   Look for: unusual spikes, requests from unexpected models, high token counts
   ```

### Scenario E: Redis / ElastiCache AUTH Token Leaked

**Target: Rotate within 15 minutes.**

1. Rotate the Redis AUTH token:
   ```bash
   # Generate a new AUTH token
   NEW_REDIS_AUTH=$(openssl rand -base64 32 | tr -d '=/+' | head -c 40)

   # Update the ElastiCache replication group auth token
   aws elasticache modify-replication-group \
     --replication-group-id your-app-prod-redis \
     --auth-token "${NEW_REDIS_AUTH}" \
     --auth-token-update-strategy ROTATE \
     --apply-immediately \
     --region us-east-1

   echo "Redis AUTH token rotation initiated at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
   ```

2. Update Secrets Manager and Kubernetes:
   ```bash
   aws secretsmanager update-secret \
     --secret-id your-app/production/redis \
     --secret-string '{"auth_token":"'"${NEW_REDIS_AUTH}"'"}' \
     --region us-east-1

   kubectl create secret generic redis-credentials -n production \
     --from-literal=REDIS_PASSWORD=${NEW_REDIS_AUTH} \
     --dry-run=client -o yaml | kubectl apply -f -

   # Restart services that connect to Redis
   kubectl rollout restart deployment/service-a -n production
   kubectl rollout restart deployment/service-b -n production
   kubectl rollout restart deployment/service-c -n production
   ```

### Scenario F: Slack Bot Token Leaked

**Target: Rotate within 15 minutes.**

1. Regenerate the Slack Bot Token:
   ```
   Slack API Dashboard: https://api.slack.com/apps
   1. Select your app
   2. Go to "OAuth & Permissions"
   3. Click "Reinstall to Workspace" (this invalidates the old token)
   4. Copy the new Bot User OAuth Token (xoxb-...)
   ```

2. Update the token in infrastructure:
   ```bash
   aws secretsmanager update-secret \
     --secret-id your-app/production/slack \
     --secret-string '{"bot_token":"'"${NEW_SLACK_BOT_TOKEN}"'"}' \
     --region us-east-1

   kubectl create secret generic slack-credentials -n production \
     --from-literal=SLACK_BOT_TOKEN=${NEW_SLACK_BOT_TOKEN} \
     --dry-run=client -o yaml | kubectl apply -f -

   kubectl rollout restart deployment/your-slack-integration -n production
   kubectl rollout status deployment/your-slack-integration -n production --timeout=120s
   ```

### Scenario G: JWT Signing Key Leaked

**Target: Rotate within 10 minutes. All existing user sessions will be invalidated.**

1. Generate a new JWT signing key:
   ```bash
   NEW_JWT_SECRET=$(openssl rand -base64 64 | tr -d '\n')
   ```

2. Update the key in infrastructure:
   ```bash
   aws secretsmanager update-secret \
     --secret-id your-app/production/jwt \
     --secret-string '{"signing_key":"'"${NEW_JWT_SECRET}"'"}' \
     --region us-east-1

   kubectl create secret generic jwt-credentials -n production \
     --from-literal=JWT_SECRET_KEY=${NEW_JWT_SECRET} \
     --dry-run=client -o yaml | kubectl apply -f -

   # Restart backend and any services that verify JWTs
   kubectl rollout restart deployment/service-a -n production
   kubectl rollout restart deployment/service-b -n production
   kubectl rollout status deployment/service-a -n production --timeout=300s
   ```

   > **NOTE**: Rotating the JWT signing key invalidates ALL existing user sessions.
   > Users will need to log in again. Coordinate with @support-team for communication
   > if this occurs during business hours.

### Post-Rotation: Remove Secret from Git History

After all credentials are rotated, remove the secret from Git history:

```bash
# Install git-filter-repo if not available
pip install git-filter-repo

# Remove the secret from all commits
cd ${REPO_PATH}
git filter-repo --replace-text <(echo "${LEAKED_SECRET}==>REDACTED_CREDENTIAL") --force

# Force push the cleaned history (REQUIRES ADMIN APPROVAL)
git push --force-with-lease origin ${BRANCH_NAME}

# If the repo was public, consider it permanently compromised
# The secret was likely cached by bots within seconds
```

### Post-Rotation: Communication Protocol

1. **Internal notification** (within 30 minutes):
   ```
   Slack: #security-incidents
   Subject: [SECURITY] Credential leak detected and remediated
   Include:
   - Type of credential leaked
   - Where it was found
   - Time of exposure (first committed -> detected)
   - Actions taken
   - Whether any unauthorized access was detected
   - DO NOT include the actual credential value
   ```

2. **If customer data may have been accessed** (within 4 hours):
   ```
   Escalate to: @legal-team, @cpo, @vp-engineering
   Requirements:
   - GDPR: 72-hour notification window to supervisory authority
   - SOC2: Document in incident log
   - Customer notification: per contractual obligations
   ```

## Verification

```bash
# Verify old credentials no longer work
# (Test from outside the cluster to ensure revocation propagated)

# AWS: Attempt to use the old key (should fail)
AWS_ACCESS_KEY_ID=${COMPROMISED_ACCESS_KEY_ID} \
AWS_SECRET_ACCESS_KEY=${COMPROMISED_SECRET_KEY} \
aws sts get-caller-identity 2>&1 | grep -q "InvalidClientTokenId" && \
  echo "PASS: Old AWS key is revoked" || echo "FAIL: Old AWS key still works!"

# Verify services are healthy with new credentials
kubectl get pods -n production -l 'app in (service-a,service-b,service-c)' \
  -o custom-columns=NAME:.metadata.name,STATUS:.status.phase,RESTARTS:.status.containerStatuses[0].restartCount

# Verify no new unauthorized resources were created
aws iam list-users --query 'Users[?CreateDate>=`'$(date -u -v-24H +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%SZ)'`]' --region us-east-1
aws ec2 describe-instances --filters "Name=instance-state-name,Values=running" \
  --query 'Reservations[].Instances[].[InstanceId,InstanceType,LaunchTime,Tags[?Key==`Name`].Value|[0]]' \
  --output table --region us-east-1

# Verify monitoring alert has cleared
# -> https://<your-monitoring-url>/monitors/manage?q=SecretLeakDetected

# Run a scan for remaining secrets in the codebase
# GitGuardian: https://dashboard.gitguardian.com/workspace/incidents
# Or locally:
trufflehog git file://${REPO_PATH} --only-verified
```

Expected: Old credentials are rejected; all services are Running with 0 recent restarts; no unauthorized resources detected.

## Rollback

Credential rotation should not be rolled back. If new credentials cause service failures:

```bash
# Check if the new credentials were stored correctly
kubectl get secret database-credentials -n production -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d
kubectl get secret aws-credentials -n production -o jsonpath='{.data.AWS_ACCESS_KEY_ID}' | base64 -d

# If a service cannot authenticate with new credentials, re-check the secret values
# and ensure they match what was generated during rotation

# Restart the failing service
kubectl rollout restart deployment/${FAILING_SERVICE} -n production

# If RDS password change caused connection storm, scale down and back up
kubectl scale deployment/service-a -n production --replicas=0
sleep 10
kubectl scale deployment/service-a -n production --replicas=${DESIRED_REPLICAS}
```

**NEVER re-enable a compromised credential.** Generate a new one if the rotated credential is also problematic.

## Escalation

| Condition | Contact |
|-----------|---------|
| Any credential leak detected | @security-team-lead (immediate) |
| Customer data potentially accessed | @incident-commander + @legal-team |
| AWS root account key leaked | @cto (immediate) + AWS Support Severity 1 |
| Stripe key leaked, unauthorized charges | @cfo + @your-payment-team-lead |
| Unable to rotate within 15 minutes | @vp-engineering |
| Evidence of lateral movement | @security-team + external IR firm |

## Related Runbooks

- [Suspicious API Activity](../security/suspicious-api-activity.md)
- [PostgreSQL Connection Pool Exhaustion](../database/postgres-connection-pool-exhaustion.md)
- [Unexpected Cloud Spend Spike](../cloud-cost/unexpected-spend-spike.md)
- [DNS Resolution Failure](../networking/dns-resolution-failure.md)

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2025-11-30 | @security-team | Added Anthropic API key rotation scenario |
| 2025-08-15 | @security-team | Added git-filter-repo instructions |
| 2025-05-20 | @security-lead | Added CloudTrail forensics section |
| 2025-02-10 | @security-team | Initial version |
