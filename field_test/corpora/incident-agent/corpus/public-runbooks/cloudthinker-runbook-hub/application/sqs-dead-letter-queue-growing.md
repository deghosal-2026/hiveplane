# SQS Dead Letter Queue Growing

| Field | Value |
|-------|-------|
| **Severity** | High |
| **Tags** | `aws`, `sqs`, `dead-letter-queue`, `messaging`, `async` |
| **Alert** | `SQSDLQMessagesVisible` |
| **Owner** | @your-team |
| **Last Reviewed** | 2026-02-11 |

## Summary

Messages are accumulating in SQS dead letter queues (DLQ), indicating consumer failures. When messages fail to process after the maximum receive attempts (typically 3-5), they are automatically moved to the DLQ. This signals systematic issues with message consumers, downstream dependencies, or message payload quality that require investigation and remediation.

## Impact

- **Data Loss Risk**: Messages stuck in DLQ are not being processed, leading to incomplete business workflows
- **Operational Debt**: Accumulated DLQ messages require manual intervention to redrive or purge
- **Customer Impact**: Delayed notifications, order processing, or async operations
- **Monitoring Noise**: Increased error rates and failed processing metrics

## Prerequisites

- AWS CLI access with SQS permissions (`sqs:GetQueueAttributes`, `sqs:ReceiveMessage`, `sqs:StartMessageMoveTask`)
- kubectl access to inspect consumer pods (if running on Kubernetes)
- CloudWatch Logs Insights access for consumer application logs
- Knowledge of the message schema and expected processing flow

## Triage & Diagnosis

### Step 1: Check DLQ Message Count

```bash
# Get current DLQ message count
aws sqs get-queue-attributes \
  --queue-url ${DLQ_URL} \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible

# Example output:
# {
#   "Attributes": {
#     "ApproximateNumberOfMessages": "1247",
#     "ApproximateNumberOfMessagesNotVisible": "0"
#   }
# }
```

### Step 2: Check Source Queue Metrics

```bash
# Get source queue attributes
aws sqs get-queue-attributes \
  --queue-url ${SOURCE_QUEUE_URL} \
  --attribute-names All

# Key metrics to review:
# - ApproximateNumberOfMessages (backlog)
# - ApproximateNumberOfMessagesNotVisible (in-flight)
# - ApproximateAgeOfOldestMessage (seconds)
```

### Step 3: Sample DLQ Messages to Inspect Error Patterns

```bash
# Receive sample messages from DLQ (without deleting)
aws sqs receive-message \
  --queue-url ${DLQ_URL} \
  --max-number-of-messages 10 \
  --attribute-names All \
  --message-attribute-names All

# Save to file for analysis
aws sqs receive-message \
  --queue-url ${DLQ_URL} \
  --max-number-of-messages 10 \
  --attribute-names All > /tmp/dlq-sample.json

# Inspect message patterns
cat /tmp/dlq-sample.json | jq '.Messages[] | {MessageId, Body, Attributes}'
```

### Step 4: Check Consumer Logs for Error Patterns

```bash
# For Kubernetes-based consumers (Celery workers)
kubectl logs -l app=celery-worker --tail=500 | grep -i error

# Check for common error patterns
kubectl logs -l app=celery-worker --tail=1000 | grep -E "(Exception|Error|Failed|Timeout)"

# For Lambda consumers
aws logs tail /aws/lambda/${CONSUMER_FUNCTION_NAME} \
  --since 1h \
  --filter-pattern "ERROR"
```

### Step 5: Check Consumer Pod Health

```bash
# Check pod restart counts and status
kubectl get pods -l app=celery-worker -o wide

# Check for OOMKilled or CrashLoopBackOff
kubectl get pods -l app=celery-worker -o json | \
  jq '.items[] | {name: .metadata.name, restarts: .status.containerStatuses[0].restartCount, state: .status.containerStatuses[0].state}'

# Check pod resource usage
kubectl top pods -l app=celery-worker
```

### Step 6: Check CloudWatch Metrics for Source Queue

```bash
# Get metrics for the last 1 hour
aws cloudwatch get-metric-statistics \
  --namespace AWS/SQS \
  --metric-name NumberOfMessagesSent \
  --dimensions Name=QueueName,Value=${QUEUE_NAME} \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 \
  --statistics Sum

# Check message age
aws cloudwatch get-metric-statistics \
  --namespace AWS/SQS \
  --metric-name ApproximateAgeOfOldestMessage \
  --dimensions Name=QueueName,Value=${QUEUE_NAME} \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 \
  --statistics Maximum

# Check deletion rate (successful processing)
aws cloudwatch get-metric-statistics \
  --namespace AWS/SQS \
  --metric-name NumberOfMessagesDeleted \
  --dimensions Name=QueueName,Value=${QUEUE_NAME} \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 \
  --statistics Sum
```

### Step 7: Check Consumer Scaling

```bash
# For Celery workers on Kubernetes
kubectl get hpa -l app=celery-worker

# Check current replica count vs desired
kubectl get deployment celery-worker -o json | \
  jq '{replicas: .spec.replicas, available: .status.availableReplicas, ready: .status.readyReplicas}'

# For Lambda consumers
aws lambda get-function-concurrency --function-name ${CONSUMER_FUNCTION_NAME}

# Check Lambda concurrent executions
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name ConcurrentExecutions \
  --dimensions Name=FunctionName,Value=${CONSUMER_FUNCTION_NAME} \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 \
  --statistics Maximum
```

## Mitigation Steps

### Scenario A: Consumer Bug / Code Error

**Symptoms**: Consistent error patterns in logs (e.g., `KeyError`, `AttributeError`, schema validation failures)

**Root Cause**: Application code cannot handle message payload or business logic failure

**Resolution**:

1. Identify the failing message pattern from DLQ samples:
   ```bash
   cat /tmp/dlq-sample.json | jq '.Messages[] | .Body' | head -5
   ```

2. Fix the consumer code bug and deploy new version:
   ```bash
   # Deploy fixed version (example for Kubernetes)
   kubectl set image deployment/celery-worker celery-worker=${NEW_IMAGE}:${VERSION}
   kubectl rollout status deployment/celery-worker
   ```

3. Verify the fix with a single test message:
   ```bash
   # Send one message from DLQ to source queue manually
   MESSAGE_BODY=$(aws sqs receive-message --queue-url ${DLQ_URL} --max-number-of-messages 1 | jq -r '.Messages[0].Body')

   aws sqs send-message \
     --queue-url ${SOURCE_QUEUE_URL} \
     --message-body "${MESSAGE_BODY}"
   ```

4. Monitor logs to confirm successful processing:
   ```bash
   kubectl logs -l app=celery-worker --tail=50 -f
   ```

5. Redrive all messages from DLQ back to source queue:
   ```bash
   aws sqs start-message-move-task \
     --source-arn ${DLQ_ARN} \
     --destination-arn ${SOURCE_QUEUE_ARN} \
     --max-number-of-messages-per-second 100

   # Capture task handle for monitoring
   # Save the returned TaskHandle value
   ```

### Scenario B: Downstream Dependency Failure

**Symptoms**: Connection timeouts, database errors, external API failures in logs

**Root Cause**: Consumer cannot reach required dependencies (database, API, S3, etc.)

**Resolution**:

1. Identify the failing dependency from error logs:
   ```bash
   kubectl logs -l app=celery-worker --tail=500 | grep -E "(ConnectionError|Timeout|503|500)"
   ```

2. Fix the dependency issue:
   - Database: Check RDS connectivity, connection pool exhaustion, slow queries
   - API: Check downstream service health, rate limits, authentication tokens
   - Network: Check security groups, NACLs, route tables

3. Verify dependency is healthy:
   ```bash
   # Example: Test database connectivity from consumer pod
   kubectl exec -it ${CONSUMER_POD} -- psql -h ${DB_HOST} -U ${DB_USER} -d ${DB_NAME} -c "SELECT 1;"

   # Example: Test external API
   kubectl exec -it ${CONSUMER_POD} -- curl -v ${EXTERNAL_API_URL}/health
   ```

4. Redrive messages from DLQ:
   ```bash
   aws sqs start-message-move-task \
     --source-arn ${DLQ_ARN} \
     --destination-arn ${SOURCE_QUEUE_ARN} \
     --max-number-of-messages-per-second 50
   ```

### Scenario C: Poison Messages (Malformed Data)

**Symptoms**: Specific messages repeatedly fail, validation errors, JSON parse errors

**Root Cause**: Messages with invalid schema, corrupted data, or unexpected format

**Resolution**:

1. Identify poison message patterns:
   ```bash
   # Sample and analyze message bodies
   aws sqs receive-message \
     --queue-url ${DLQ_URL} \
     --max-number-of-messages 10 > /tmp/messages.json

   # Check for common issues
   cat /tmp/messages.json | jq '.Messages[] | .Body | fromjson' || echo "JSON parse errors found"
   ```

2. Option A: Purge specific bad messages (if small number):
   ```bash
   # Receive and delete specific messages
   aws sqs receive-message \
     --queue-url ${DLQ_URL} \
     --max-number-of-messages 10 \
     --visibility-timeout 30 > /tmp/bad-messages.json

   # Review and delete confirmed bad messages
   for RECEIPT_HANDLE in $(cat /tmp/bad-messages.json | jq -r '.Messages[] | .ReceiptHandle'); do
     aws sqs delete-message \
       --queue-url ${DLQ_URL} \
       --receipt-handle "${RECEIPT_HANDLE}"
   done
   ```

3. Option B: Move to quarantine queue for later analysis:
   ```bash
   # Create quarantine queue if not exists
   aws sqs create-queue --queue-name ${QUEUE_NAME}-quarantine

   # Redrive to quarantine instead of source
   aws sqs start-message-move-task \
     --source-arn ${DLQ_ARN} \
     --destination-arn ${QUARANTINE_QUEUE_ARN}
   ```

4. Redrive valid messages (after filtering):
   ```bash
   # Redrive remaining messages from DLQ
   aws sqs start-message-move-task \
     --source-arn ${DLQ_ARN} \
     --destination-arn ${SOURCE_QUEUE_ARN}
   ```

### Scenario D: Consumer Scaling Issue

**Symptoms**: High message backlog in source queue, low consumer error rate, slow processing

**Root Cause**: Insufficient consumer capacity to handle message volume

**Resolution**:

1. Scale up consumers immediately:
   ```bash
   # For Kubernetes deployment
   kubectl scale deployment celery-worker --replicas=10

   # For HPA, adjust max replicas
   kubectl patch hpa celery-worker -p '{"spec":{"maxReplicas":20}}'

   # For Lambda, increase reserved concurrency
   aws lambda put-function-concurrency \
     --function-name ${CONSUMER_FUNCTION_NAME} \
     --reserved-concurrent-executions 100
   ```

2. Monitor scaling progress:
   ```bash
   # Watch pods coming online
   kubectl get pods -l app=celery-worker -w

   # Check queue depth trending down
   watch -n 10 "aws sqs get-queue-attributes \
     --queue-url ${SOURCE_QUEUE_URL} \
     --attribute-names ApproximateNumberOfMessages | \
     jq -r '.Attributes.ApproximateNumberOfMessages'"
   ```

3. Redrive DLQ messages:
   ```bash
   aws sqs start-message-move-task \
     --source-arn ${DLQ_ARN} \
     --destination-arn ${SOURCE_QUEUE_ARN} \
     --max-number-of-messages-per-second 200
   ```

4. Review and adjust autoscaling policies:
   ```bash
   # Edit HPA to scale based on queue depth
   kubectl edit hpa celery-worker

   # Add custom metric for SQS queue depth if not present
   ```

## Monitor Redrive Progress

```bash
# Check redrive task status
aws sqs list-message-move-tasks --source-arn ${DLQ_ARN}

# Get specific task details
aws sqs list-message-move-tasks \
  --source-arn ${DLQ_ARN} \
  --max-results 10 | \
  jq '.Results[] | {Status, ApproximateNumberOfMessagesMoved, ApproximateNumberOfMessagesToMove}'

# Cancel redrive if needed
aws sqs cancel-message-move-task --task-handle ${TASK_HANDLE}
```

## Purge DLQ (Use with Caution)

**WARNING**: Only purge DLQ if messages are confirmed invalid and data loss is acceptable.

```bash
# Purge all messages from DLQ
aws sqs purge-queue --queue-url ${DLQ_URL}

# Confirm purge
aws sqs get-queue-attributes \
  --queue-url ${DLQ_URL} \
  --attribute-names ApproximateNumberOfMessages
```

## Verification

1. **DLQ message count dropping to zero**:
   ```bash
   aws sqs get-queue-attributes \
     --queue-url ${DLQ_URL} \
     --attribute-names ApproximateNumberOfMessages | \
     jq -r '.Attributes.ApproximateNumberOfMessages'
   ```

2. **Source queue processing normally**:
   ```bash
   # Check that messages are being deleted (processed)
   aws cloudwatch get-metric-statistics \
     --namespace AWS/SQS \
     --metric-name NumberOfMessagesDeleted \
     --dimensions Name=QueueName,Value=${QUEUE_NAME} \
     --start-time $(date -u -d '10 minutes ago' +%Y-%m-%dT%H:%M:%S) \
     --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
     --period 60 \
     --statistics Sum
   ```

3. **Consumer error rate back to zero**:
   ```bash
   # Check application logs for errors
   kubectl logs -l app=celery-worker --since=5m | grep -c ERROR

   # Should return 0 or very low count
   ```

4. **CloudWatch alarm resolved**:
   ```bash
   aws cloudwatch describe-alarms \
     --alarm-names SQSDLQMessagesVisible \
     --state-value OK
   ```

5. **No new messages entering DLQ**:
   ```bash
   # Monitor for 5 minutes
   for i in {1..5}; do
     aws sqs get-queue-attributes \
       --queue-url ${DLQ_URL} \
       --attribute-names ApproximateNumberOfMessages | \
       jq -r '.Attributes.ApproximateNumberOfMessages'
     sleep 60
   done
   ```

## Rollback

If redriven messages are causing failures again:

```bash
# 1. Cancel ongoing redrive task
aws sqs cancel-message-move-task --task-handle ${TASK_HANDLE}

# 2. Verify cancellation
aws sqs list-message-move-tasks --source-arn ${DLQ_ARN} | \
  jq '.Results[] | select(.TaskHandle == "'${TASK_HANDLE}'") | .Status'
# Expected: "CANCELLED"

# 3. Investigate root cause further before retrying
kubectl logs -l app=celery-worker --tail=100

# 4. Consider rolling back consumer deployment
kubectl rollout undo deployment/celery-worker
```

## Escalation

Escalate to @your-team if:

- DLQ continues to grow after mitigation attempts
- Consumer scaling does not reduce message backlog
- Poison messages cannot be identified or filtered
- Downstream dependency issues cannot be resolved within 30 minutes
- Data loss risk is critical (financial transactions, user data)

## Related Runbooks

- [celery-worker-queue-backlog.md](./celery-worker-queue-backlog.md) - Celery queue backlog troubleshooting
- [api-high-latency.md](./api-high-latency.md) - API latency affecting async processing
- [database-connection-pool-exhaustion.md](./database-connection-pool-exhaustion.md) - DB issues causing consumer failures

## Changelog

| Date | Author | Changes |
|------|--------|---------|
| 2026-02-11 | @your-team | Initial version |
