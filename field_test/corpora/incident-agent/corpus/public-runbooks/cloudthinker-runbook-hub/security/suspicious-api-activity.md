# Suspicious API Activity / Brute Force Detection

## Metadata

| Field | Value |
|-------|-------|
| **Severity** | High |
| **Service** | backend, payment-service |
| **Owner** | @security-team |
| **Last Reviewed** | 2025-12-01 |
| **Alert** | `SuspiciousAPIActivity` / `BruteForceDetected` |
| **Tags** | `security`, `waf`, `brute-force`, `rate-limiting`, `incident-response` |

## Summary

Anomalous API request patterns have been detected, indicating potential brute force attacks, credential stuffing, API scraping, or unauthorized enumeration. This runbook covers identification, containment, and forensic analysis of suspicious API activity targeting CloudThinker's production endpoints.

## Impact

- **Security**: Account takeover via credential stuffing; data scraping of customer information; API abuse leading to LLM cost exposure
- **Availability**: Legitimate users blocked by rate limiting triggered by attack traffic; increased latency from volume
- **Financial**: Unauthorized LLM API consumption (Anthropic/Bedrock) billed to CloudThinker; potential Stripe API abuse
- **Compliance**: Failed login attempts may indicate targeted attack requiring SOC2 incident documentation

## Prerequisites

- Access to Datadog APM and Logs
- Access to AWS WAF console and CLI
- `kubectl` configured for production cluster
- Access to Grafana dashboards
- AWS CLI with WAF and CloudFront permissions
- Access to CloudThinker admin panel for account management
- Familiarity with CloudThinker's authentication flow (JWT + session-based)

## Triage & Diagnosis

### Step 1: Assess the Alert

```
Datadog Monitor: "Suspicious API Activity - Production"
  -> https://app.datadoghq.com/monitors/manage?q=SuspiciousAPIActivity

Datadog Monitor: "Brute Force Detected - Auth Endpoints"
  -> https://app.datadoghq.com/monitors/manage?q=BruteForceDetected

Grafana Dashboard: "API Security / Request Patterns"
  -> https://grafana.internal.cloudthinker.io/d/api-security/api-security-request-patterns

Key alert thresholds:
  - >100 failed auth attempts from single IP in 5 minutes
  - >50 unique usernames attempted from single IP in 10 minutes
  - >1000 requests/minute from single IP to any endpoint
  - >500 4xx responses/minute from single IP
  - Unusual geographic origin for API traffic
```

### Step 2: Identify Attack Pattern

```bash
# Check Datadog logs for suspicious patterns
# -> https://app.datadoghq.com/logs?query=service:backend status:warn @http.status_code:(401 OR 403 OR 429)

# Pull recent auth failure logs from backend pods
kubectl logs -n production -l app=backend --tail=500 --timestamps | \
  grep -E "(401|403|authentication failed|invalid token|rate.limit)" | \
  tail -50

# Count failures by IP in the last hour
kubectl logs -n production -l app=backend --since=1h | \
  grep -E "401|403" | \
  grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' | \
  sort | uniq -c | sort -rn | head -20
```

### Step 3: Analyze WAF Logs

```bash
# Get the WAF Web ACL ARN
WAF_ACL_ARN=$(aws wafv2 list-web-acls --scope REGIONAL --region us-east-1 \
  --query "WebACLs[?Name=='cloudthinker-prod-waf'].ARN" --output text)

# Check WAF metrics for blocked requests
aws cloudwatch get-metric-statistics \
  --namespace "AWS/WAFV2" \
  --metric-name "BlockedRequests" \
  --dimensions Name=WebACL,Value=cloudthinker-prod-waf Name=Region,Value=us-east-1 Name=Rule,Value=ALL \
  --start-time $(date -u -v-1H +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 \
  --statistics Sum \
  --region us-east-1

# Check WAF sampled requests for the attack pattern
aws wafv2 get-sampled-requests \
  --web-acl-arn ${WAF_ACL_ARN} \
  --rule-metric-name RateBasedRule \
  --scope REGIONAL \
  --time-window StartTime=$(date -u -v-1H +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S),EndTime=$(date -u +%Y-%m-%dT%H:%M:%S) \
  --max-items 100 \
  --region us-east-1
```

### Step 4: Identify Targeted Endpoints

```bash
# Analyze which endpoints are under attack
kubectl logs -n production -l app=backend --since=1h | \
  grep -E "401|403|429" | \
  grep -oE '"(GET|POST|PUT|DELETE) [^ ]+"' | \
  sort | uniq -c | sort -rn | head -20

# Common attack targets:
# POST /api/v1/auth/login         -> Credential stuffing
# POST /api/v1/auth/register      -> Account creation abuse
# POST /api/v1/auth/reset-password -> Account enumeration
# GET  /api/v1/users/*             -> User data scraping
# POST /api/v1/chat/completions   -> LLM API abuse (cost)
# GET  /api/v1/runbooks/*         -> Content scraping
```

### Step 5: Identify Attacker Infrastructure

```bash
# Extract top attacking IPs
ATTACKING_IPS=$(kubectl logs -n production -l app=backend --since=1h | \
  grep -E "401|403" | \
  grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' | \
  sort | uniq -c | sort -rn | head -10 | awk '{print $2}')

# Lookup IP reputation and geolocation
for IP in ${ATTACKING_IPS}; do
  echo "=== ${IP} ==="
  # Check if it is a known cloud provider / VPN / Tor exit node
  curl -s "https://ipinfo.io/${IP}/json" | jq '{ip, city, region, country, org, timezone}'
  echo ""
done

# Check if IPs are Tor exit nodes
curl -s "https://check.torproject.org/torbulkexitlist" > /tmp/tor-exits.txt
for IP in ${ATTACKING_IPS}; do
  grep -q "${IP}" /tmp/tor-exits.txt && echo "TOR EXIT: ${IP}" || echo "Not Tor: ${IP}"
done

# Check if attack is distributed (many IPs = botnet)
UNIQUE_IP_COUNT=$(kubectl logs -n production -l app=backend --since=1h | \
  grep -E "401|403" | \
  grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' | \
  sort -u | wc -l)
echo "Unique attacking IPs in last hour: ${UNIQUE_IP_COUNT}"
```

## Mitigation Steps

### Scenario A: Single-IP Brute Force on Authentication Endpoints

**Symptoms**: High volume of 401 responses from a single IP targeting `/api/v1/auth/login`.

1. Block the attacking IP immediately via WAF:
   ```bash
   # Get the IP set for blocked IPs
   IP_SET_ID=$(aws wafv2 list-ip-sets --scope REGIONAL --region us-east-1 \
     --query "IPSets[?Name=='cloudthinker-blocked-ips'].Id" --output text)
   IP_SET_LOCK_TOKEN=$(aws wafv2 get-ip-set --scope REGIONAL --region us-east-1 \
     --name cloudthinker-blocked-ips --id ${IP_SET_ID} \
     --query 'LockToken' --output text)

   # Get current addresses
   CURRENT_ADDRESSES=$(aws wafv2 get-ip-set --scope REGIONAL --region us-east-1 \
     --name cloudthinker-blocked-ips --id ${IP_SET_ID} \
     --query 'IPSet.Addresses' --output json)

   # Add the attacking IP (CIDR /32 for single IP)
   UPDATED_ADDRESSES=$(echo ${CURRENT_ADDRESSES} | jq -r '. + ["'${ATTACKING_IP}'/32"] | .[]')

   # --addresses expects space-separated CIDR strings, not a JSON array
   aws wafv2 update-ip-set \
     --scope REGIONAL \
     --region us-east-1 \
     --name cloudthinker-blocked-ips \
     --id ${IP_SET_ID} \
     --lock-token ${IP_SET_LOCK_TOKEN} \
     --addresses ${UPDATED_ADDRESSES}

   echo "Blocked ${ATTACKING_IP} in WAF at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
   ```

2. Verify the block is effective:
   ```bash
   # Check WAF blocked request count for this rule
   aws cloudwatch get-metric-statistics \
     --namespace "AWS/WAFV2" \
     --metric-name "BlockedRequests" \
     --dimensions Name=WebACL,Value=cloudthinker-prod-waf Name=Region,Value=us-east-1 Name=Rule,Value=BlockedIPSet \
     --start-time $(date -u -v-5M +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '5 minutes ago' +%Y-%m-%dT%H:%M:%S) \
     --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
     --period 60 \
     --statistics Sum \
     --region us-east-1
   ```

3. Check if any accounts were compromised:
   ```bash
   # Query the database for recent successful logins from the attacking IP
   kubectl exec -n production deploy/backend -- python -c "
   import asyncio
   from app.core.db import get_session
   from sqlalchemy import text

   async def check():
       async with get_session() as session:
           result = await session.execute(text('''
               SELECT user_id, email, created_at, ip_address
               FROM auth_sessions
               WHERE ip_address = :ip
               AND created_at > NOW() - INTERVAL '24 hours'
               ORDER BY created_at DESC
           '''), {'ip': '${ATTACKING_IP}'})
           for row in result.fetchall():
               print(f'COMPROMISED SESSION: user={row.email}, time={row.created_at}')

   asyncio.run(check())
   "
   ```

4. Force logout any sessions created from the attacking IP:
   ```bash
   # Invalidate sessions from the attacking IP
   kubectl exec -n production deploy/backend -- python -c "
   import asyncio, redis.asyncio as redis

   async def revoke():
       r = redis.from_url('redis://redis-master.production.svc.cluster.local:6379/0')
       keys = []
       async for key in r.scan_iter(match='session:*'):
           session_data = await r.get(key)
           if session_data and '${ATTACKING_IP}' in session_data.decode():
               keys.append(key)
       if keys:
           await r.delete(*keys)
           print(f'Revoked {len(keys)} sessions from ${ATTACKING_IP}')
       else:
           print('No active sessions from this IP')
       await r.close()

   asyncio.run(revoke())
   "
   ```

### Scenario B: Distributed Brute Force / Credential Stuffing (Botnet)

**Symptoms**: Moderate volume of 401 responses from hundreds/thousands of unique IPs; rotating user agents; targeting login endpoint with known breached credential lists.

1. Enable aggressive rate limiting on auth endpoints:
   ```bash
   # Add a rate-based rule to WAF (limit to 20 requests per 5 minutes per IP on auth paths)
   WAF_ACL_ID=$(aws wafv2 list-web-acls --scope REGIONAL --region us-east-1 \
     --query "WebACLs[?Name=='cloudthinker-prod-waf'].Id" --output text)
   WAF_ACL_LOCK_TOKEN=$(aws wafv2 get-web-acl --scope REGIONAL --region us-east-1 \
     --name cloudthinker-prod-waf --id ${WAF_ACL_ID} \
     --query 'LockToken' --output text)

   # Get current rules and add rate-based rule
   # Note: This is a complex WAF update - use the AWS console for safety
   echo "ACTION REQUIRED: Add rate-based rule via AWS WAF Console"
   echo "  -> https://console.aws.amazon.com/wafv2/homev2/web-acls/${WAF_ACL_ID}/overview?region=us-east-1"
   echo "  Rule: Rate-based, 20 requests/5min, Scope: URI path starts with /api/v1/auth/"
   ```

2. Enable CAPTCHA challenge on login:
   ```bash
   # If CloudThinker uses WAF CAPTCHA, enable it for auth endpoints
   echo "ACTION REQUIRED: Enable WAF CAPTCHA challenge for auth endpoints"
   echo "  -> https://console.aws.amazon.com/wafv2/homev2/web-acls/${WAF_ACL_ID}/overview?region=us-east-1"
   echo "  Rule: CAPTCHA challenge on POST /api/v1/auth/login when >5 failed attempts"
   ```

3. Block the CIDR ranges of the most active attacking networks:
   ```bash
   # Identify common CIDR ranges from attacking IPs
   for IP in ${ATTACKING_IPS}; do
     whois ${IP} | grep -E "^(CIDR|inetnum|NetRange)" | head -1
   done

   # Block entire ASNs if they are hosting providers used by attackers
   # Add CIDR blocks to the WAF IP set (same process as Scenario A)
   ```

4. Notify affected users to change passwords:
   ```bash
   # Identify users whose credentials were attempted (from auth logs)
   kubectl logs -n production -l app=backend --since=6h | \
     grep "authentication failed" | \
     grep -oE '"email":"[^"]+"' | \
     sort -u | head -50 > /tmp/targeted-users.txt

   echo "$(wc -l < /tmp/targeted-users.txt) unique users targeted"
   echo "Send password reset notifications via admin panel or bulk email"
   ```

### Scenario C: API Scraping / Data Exfiltration

**Symptoms**: High volume of successful (200) requests to data endpoints (`/api/v1/users`, `/api/v1/runbooks`); sequential ID enumeration patterns; unusually high response sizes.

1. Identify the scraping pattern:
   ```bash
   # Check for sequential ID access patterns
   kubectl logs -n production -l app=backend --since=1h | \
     grep "200" | \
     grep -E "/api/v1/(users|runbooks|organizations)/[0-9]+" | \
     grep -oE '/[0-9]+' | sort -t/ -k2 -n | head -50

   # Check for unusual data transfer volumes
   kubectl logs -n production -l app=backend --since=1h | \
     grep "${SCRAPER_IP}" | \
     awk '{sum += $NF} END {print "Total bytes transferred: " sum}'
   ```

2. Block the scraper immediately:
   ```bash
   # Block via WAF (same process as Scenario A)
   # Additionally, add a geo-blocking rule if traffic is from unexpected regions

   # Block via Kubernetes NetworkPolicy as an additional layer
   kubectl apply -f - <<EOF
   apiVersion: networking.k8s.io/v1
   kind: NetworkPolicy
   metadata:
     name: block-scraper-ip
     namespace: production
   spec:
     podSelector:
       matchLabels:
         app: backend
     policyTypes:
       - Ingress
     ingress:
       - from:
           - ipBlock:
               cidr: 0.0.0.0/0
               except:
                 - ${SCRAPER_IP}/32
   EOF
   ```

3. Check if any data was exfiltrated:
   ```bash
   # Count total records accessed by the scraper IP
   kubectl logs -n production -l app=backend --since=24h | \
     grep "${SCRAPER_IP}" | \
     grep "200" | \
     wc -l

   # Check if PII endpoints were accessed
   kubectl logs -n production -l app=backend --since=24h | \
     grep "${SCRAPER_IP}" | \
     grep -E "(users|billing|payment|profile)" | \
     head -20
   ```

4. If PII was accessed, escalate to @legal-team:
   ```
   Slack: #security-incidents
   Subject: [SECURITY] Potential data exfiltration detected
   Include: IP address, endpoints accessed, data volume, time range
   ```

### Scenario D: LLM API Abuse (Cost Attack)

**Symptoms**: Spike in Anthropic/Bedrock API usage; requests to `/api/v1/chat/completions` from unauthorized or suspicious tokens; rapid cost increase in LLM provider dashboard.

1. Identify the abusing tokens:
   ```bash
   # Check which API tokens are making LLM requests
   kubectl logs -n production -l app=backend --since=1h | \
     grep "/api/v1/chat/completions" | \
     grep -oE 'Authorization: Bearer [a-zA-Z0-9._-]+' | \
     sort | uniq -c | sort -rn | head -10

   # Check Anthropic usage for cost impact
   echo "Check Anthropic Console: https://console.anthropic.com/settings/usage"
   echo "Check AWS Bedrock costs: https://console.aws.amazon.com/billing/"
   ```

2. Revoke abused API tokens:
   ```bash
   # Invalidate specific user tokens in Redis
   kubectl exec -n production deploy/backend -- python -c "
   import asyncio, redis.asyncio as redis

   async def revoke_token():
       r = redis.from_url('redis://redis-master.production.svc.cluster.local:6379/0')
       await r.delete('api_token:${ABUSED_TOKEN_ID}')
       await r.sadd('revoked_tokens', '${ABUSED_TOKEN_ID}')
       print('Token revoked and added to blocklist')
       await r.close()

   asyncio.run(revoke_token())
   "
   ```

3. Temporarily disable LLM endpoints if cost is escalating rapidly:
   ```bash
   # Scale down worker-high (handles LLM requests) to stop the bleeding
   kubectl scale deployment/worker-high -n production --replicas=0

   # Once abuse is contained, scale back up
   kubectl scale deployment/worker-high -n production --replicas=${DESIRED_REPLICAS}
   ```

## Verification

```bash
# Verify attack traffic has stopped
kubectl logs -n production -l app=backend --since=5m | \
  grep -c -E "401|403|429"
# Expected: significantly reduced from attack levels

# Verify WAF is blocking the attacker
aws cloudwatch get-metric-statistics \
  --namespace "AWS/WAFV2" \
  --metric-name "BlockedRequests" \
  --dimensions Name=WebACL,Value=cloudthinker-prod-waf Name=Region,Value=us-east-1 Name=Rule,Value=ALL \
  --start-time $(date -u -v-10M +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '10 minutes ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 60 \
  --statistics Sum \
  --region us-east-1

# Verify legitimate users are not impacted
kubectl logs -n production -l app=backend --since=5m | \
  grep "200" | wc -l
# Expected: normal request volume

# Verify no accounts were compromised
# Check Datadog for new suspicious activity patterns
# -> https://app.datadoghq.com/monitors/manage?q=SuspiciousAPIActivity

# Verify rate limiting is working correctly
curl -s -o /dev/null -w "%{http_code}" \
  -X POST https://api.cloudthinker.io/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"test@test.com","password":"invalid"}' \
  # Expected: 401 first time, 429 after rate limit threshold
```

## Rollback

If mitigation steps cause legitimate traffic to be blocked:

```bash
# Remove IP from WAF blocklist
CURRENT_ADDRESSES=$(aws wafv2 get-ip-set --scope REGIONAL --region us-east-1 \
  --name cloudthinker-blocked-ips --id ${IP_SET_ID} \
  --query 'IPSet.Addresses' --output json)
IP_SET_LOCK_TOKEN=$(aws wafv2 get-ip-set --scope REGIONAL --region us-east-1 \
  --name cloudthinker-blocked-ips --id ${IP_SET_ID} \
  --query 'LockToken' --output text)

UPDATED_ADDRESSES=$(echo ${CURRENT_ADDRESSES} | jq -r '[.[] | select(. != "'${BLOCKED_IP}'/32")] | .[]')

# --addresses expects space-separated CIDR strings, not a JSON array
aws wafv2 update-ip-set \
  --scope REGIONAL \
  --region us-east-1 \
  --name cloudthinker-blocked-ips \
  --id ${IP_SET_ID} \
  --lock-token ${IP_SET_LOCK_TOKEN} \
  --addresses ${UPDATED_ADDRESSES}

# Remove NetworkPolicy if applied
kubectl delete networkpolicy block-scraper-ip -n production

# Restore worker-high if it was scaled down
kubectl scale deployment/worker-high -n production --replicas=${DESIRED_REPLICAS}

# If rate limiting is too aggressive, adjust the WAF rule threshold
echo "Adjust WAF rate limit via console:"
echo "  -> https://console.aws.amazon.com/wafv2/homev2/web-acls/${WAF_ACL_ID}/overview?region=us-east-1"
```

## Escalation

| Condition | Contact |
|-----------|---------|
| Active brute force detected | @security-team-lead |
| Account compromise confirmed | @incident-commander |
| Customer PII accessed | @legal-team + @cpo |
| LLM cost exceeding $1,000/hour | @cto + @finops-team |
| Distributed attack (>1000 IPs) | @security-team + SOC provider |
| Law enforcement coordination needed | @legal-team + @cto |
| Not resolved in 30 minutes | @vp-engineering |

## Related Runbooks

- [Credential / Secret Leak Response](../security/credential-leak-response.md)
- [API P99 Latency SLA Breach](../application/api-high-latency.md)
- [Unexpected Cloud Spend Spike](../cloud-cost/unexpected-spend-spike.md)
- [Redis Memory Pressure](../database/redis-memory-pressure.md)
- [Celery Worker Queue Backlog](../application/celery-worker-queue-backlog.md)

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2025-12-01 | @security-team | Added LLM API abuse scenario |
| 2025-09-15 | @security-team | Added credential stuffing botnet guidance |
| 2025-06-20 | @security-lead | Added WAF IP set management commands |
| 2025-03-10 | @security-team | Initial version |
