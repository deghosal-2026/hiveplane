# SSL/TLS Certificate Expiry

## Metadata

| Field | Value |
|-------|-------|
| **Severity** | High |
| **Service** | service-a, service-b, service-c |
| **Owner** | @your-team |
| **Last Reviewed** | 2026-01-10 |
| **Alert** | `SSLCertificateExpiringSoon` |
| **Tags** | `networking`, `tls`, `certificates`, `cert-manager`, `acm` |

## Summary

An SSL/TLS certificate is approaching expiry or has already expired. This runbook covers diagnosing which certificate is affected and renewing it via cert-manager (for EKS ingress) or AWS ACM (for ALB/CloudFront).

## Impact

- **Expired certificate**: Users see browser security warnings or cannot access your application at all. API clients will reject connections with TLS handshake errors.
- **SEO and trust**: Search engines may de-index pages served with expired certificates.
- **Payment processing**: An expired certificate on payment service endpoints will cause webhook deliveries to fail, blocking payment processing.
- **SLA violation**: TLS-related downtime counts against our 99.9% availability SLA.

## Prerequisites

- `kubectl` configured with access to the `production` EKS cluster
- `openssl` CLI available locally
- AWS CLI with permissions for ACM (`acm:DescribeCertificate`, `acm:RequestCertificate`)
- Access to Route 53 for DNS validation (if renewing via ACM)
- Grafana dashboard: [TLS Certificate Monitoring](https://<your-grafana-url>/d/tls-certs/tls-certificate-monitoring)
- Monitoring alert: **SSLCertificateExpiringSoon**

## Triage & Diagnosis

### Step 1: Identify the affected certificate

Check the monitoring alert details for the domain name and days until expiry.

### Step 2: Check certificate expiry from the live endpoint

```bash
echo | openssl s_client -servername ${DOMAIN} -connect ${DOMAIN}:443 2>/dev/null | openssl x509 -noout -dates -subject -issuer
```

Example domains to check:

```bash
for domain in app.your-domain.com api.your-domain.com payments.your-domain.com; do
  echo "--- ${domain} ---"
  echo | openssl s_client -servername ${domain} -connect ${domain}:443 2>/dev/null | openssl x509 -noout -enddate
done
```

### Step 3: Determine certificate source

Your organization may use one or both certificate management systems:

| Domain Pattern | Certificate Source | Managed By |
|---------------|-------------------|------------|
| `app.your-domain.com` | AWS ACM | ALB / CloudFront |
| `api.your-domain.com` | cert-manager (Let's Encrypt) | EKS Ingress (nginx) |
| `payments.your-domain.com` | AWS ACM | ALB |
| `*.internal.your-domain.com` | cert-manager (Let's Encrypt) | EKS Ingress (nginx) |

### Step 4: For cert-manager certificates, check status in the cluster

```bash
kubectl get certificates -n production
```

```bash
kubectl describe certificate ${CERT_NAME} -n production
```

Look for:
- `Ready` condition: should be `True`
- `Not After`: the expiry date
- `Renewal Time`: when cert-manager plans to renew

Check cert-manager logs for errors:

```bash
kubectl logs -n cert-manager deployment/cert-manager --tail=100 | grep -i "error\|fail\|${CERT_NAME}"
```

### Step 5: For ACM certificates, check in AWS

```bash
aws acm list-certificates --region us-east-1 --query 'CertificateSummaryList[*].[DomainName,CertificateArn,Status]' --output table
```

```bash
aws acm describe-certificate --certificate-arn ${CERT_ARN} --region us-east-1 \
  --query 'Certificate.{Domain:DomainName,Status:Status,NotAfter:NotAfter,RenewalStatus:RenewalSummary.RenewalStatus}'
```

## Mitigation Steps

### Scenario A: cert-manager Certificate Renewal Failed

cert-manager normally auto-renews certificates 30 days before expiry. If it failed:

1. Check the Certificate resource status:

   ```bash
   kubectl get certificate ${CERT_NAME} -n production -o yaml
   ```

2. Check the related CertificateRequest and Order:

   ```bash
   kubectl get certificaterequests -n production | grep ${CERT_NAME}
   kubectl get orders -n production | grep ${CERT_NAME}
   kubectl get challenges -n production | grep ${CERT_NAME}
   ```

3. If a challenge is stuck, check the challenge details:

   ```bash
   kubectl describe challenge ${CHALLENGE_NAME} -n production
   ```

4. Common fix -- delete the stuck order to trigger a new one:

   ```bash
   kubectl delete order ${ORDER_NAME} -n production
   ```

5. Force a renewal by deleting the certificate secret (cert-manager will recreate it):

   ```bash
   kubectl delete secret ${CERT_SECRET_NAME} -n production
   ```

6. Monitor the renewal:

   ```bash
   kubectl get certificate ${CERT_NAME} -n production -w
   ```

   Wait until `READY` becomes `True` (typically 1-3 minutes for HTTP-01, up to 10 minutes for DNS-01).

### Scenario B: ACM Certificate Not Auto-Renewing

ACM certificates with DNS validation auto-renew if the CNAME validation record still exists in Route 53.

1. Check the renewal status:

   ```bash
   aws acm describe-certificate --certificate-arn ${CERT_ARN} --region us-east-1 \
     --query 'Certificate.RenewalSummary'
   ```

2. If `RenewalStatus` is `PENDING_VALIDATION`, check if the DNS validation record exists:

   ```bash
   aws acm describe-certificate --certificate-arn ${CERT_ARN} --region us-east-1 \
     --query 'Certificate.DomainValidationOptions[*].ResourceRecord'
   ```

3. Add the missing CNAME record to Route 53:

   ```bash
   aws route53 change-resource-record-sets --hosted-zone-id ${HOSTED_ZONE_ID} --change-batch "{
     \"Changes\": [{
       \"Action\": \"UPSERT\",
       \"ResourceRecordSet\": {
         \"Name\": \"${VALIDATION_CNAME_NAME}\",
         \"Type\": \"CNAME\",
         \"TTL\": 300,
         \"ResourceRecords\": [{\"Value\": \"${VALIDATION_CNAME_VALUE}\"}]
       }
     }]
   }"
   ```

4. Wait for validation (usually 5-30 minutes):

   ```bash
   aws acm wait certificate-validated --certificate-arn ${CERT_ARN} --region us-east-1
   ```

### Scenario C: Certificate Already Expired (Emergency)

If the certificate has already expired and users are affected:

1. **Immediate**: If using cert-manager, force renewal as described in Scenario A.

2. **Immediate**: If using ACM and renewal is stuck, request a new certificate:

   ```bash
   aws acm request-certificate --domain-name ${DOMAIN} \
     --validation-method DNS \
     --subject-alternative-names "*.${DOMAIN}" \
     --region us-east-1
   ```

3. Update the ALB/CloudFront listener to use the new certificate ARN:

   ```bash
   aws elbv2 modify-listener --listener-arn ${LISTENER_ARN} \
     --certificates CertificateArn=${NEW_CERT_ARN} \
     --region us-east-1
   ```

4. If using nginx-ingress, update the TLS secret reference in the Ingress resource:

   ```bash
   kubectl edit ingress ${INGRESS_NAME} -n production
   ```

   Update the `tls.secretName` field if it changed.

## Verification

Confirm the certificate is valid and not expiring soon:

```bash
echo | openssl s_client -servername ${DOMAIN} -connect ${DOMAIN}:443 2>/dev/null | openssl x509 -noout -dates
```

Expected output should show `notAfter` at least 60 days in the future.

For cert-manager certificates:

```bash
kubectl get certificate ${CERT_NAME} -n production
```

Expected: `READY` is `True`.

For ACM certificates:

```bash
aws acm describe-certificate --certificate-arn ${CERT_ARN} --region us-east-1 \
  --query 'Certificate.{Status:Status,NotAfter:NotAfter}'
```

Expected: `Status` is `ISSUED`.

Verify the monitoring alert **SSLCertificateExpiringSoon** has returned to OK.

## Rollback

If a newly issued certificate causes issues (e.g., wrong domain, broken TLS termination):

- **cert-manager**: Revert the Ingress resource to reference the previous TLS secret, then investigate:

  ```bash
  kubectl rollout undo deployment/nginx-ingress-controller -n ingress-nginx
  ```

- **ACM**: Switch the ALB listener back to the previous certificate ARN:

  ```bash
  aws elbv2 modify-listener --listener-arn ${LISTENER_ARN} \
    --certificates CertificateArn=${PREVIOUS_CERT_ARN} \
    --region us-east-1
  ```

## Escalation

| Condition | Contact |
|-----------|---------|
| Certificate expired and users affected | @incident-commander |
| cert-manager unable to issue certificates | @your-team-lead |
| DNS validation failing for ACM | @your-team-lead + AWS Support |
| Payment endpoint TLS failure | @your-payments-team + @incident-commander |

## Related Runbooks

- [DNS Resolution Failure](../networking/dns-resolution-failure.md)
- [Pod CrashLoopBackOff](../kubernetes/pod-crashloopbackoff.md)
- [API P99 Latency SLA Breach](../application/api-high-latency.md)

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2026-01-10 | @your-team | Added ACM renewal troubleshooting steps |
| 2025-10-05 | @your-team | Added multi-domain check loop command |
| 2025-07-18 | @your-team | Initial version |
