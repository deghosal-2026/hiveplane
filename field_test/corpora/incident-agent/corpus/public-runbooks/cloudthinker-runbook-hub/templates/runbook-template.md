# [Alert/Issue Name]

## Metadata

| Field | Value |
|-------|-------|
| **Severity** | Critical / High / Medium / Low |
| **Difficulty** | Easy / Medium / Hard |
| **Service** | service-name |
| **Owner** | @team-name |
| **Last Reviewed** | YYYY-MM-DD |
| **Alert** | `alert-name-in-monitoring` |
| **Tags** | `domain`, `service`, `category` |

## Summary

One to two sentences describing what this runbook addresses and when it should be used.

## Impact

What happens if this issue is not resolved? Impact on users, revenue, SLAs.

## Prerequisites

- Required access / permissions
- Required tools (`kubectl`, `psql`, `aws`, etc.)
- Required knowledge

## Triage & Diagnosis

Steps to understand the scope and root cause:

1. Check relevant dashboard:
   ```bash
   # command to check metrics
   ```

2. Check logs:
   ```bash
   # command to check logs
   ```

3. Determine severity based on findings.

## Mitigation Steps

### Scenario A: [Root Cause 1]

1. Step one:
   ```bash
   # exact command
   ```

2. Step two:
   ```bash
   # exact command
   ```

### Scenario B: [Root Cause 2]

1. Step one:
   ```bash
   # exact command
   ```

## Verification

How to confirm the issue is resolved:

```bash
# verification command
```

Expected output: describe what success looks like.

## Rollback

If the mitigation causes additional issues:

```bash
# rollback command
```

## Escalation

| Condition | Contact |
|-----------|---------|
| Not resolved in 15 min | @team-lead |
| Customer impact confirmed | @incident-commander |
| Data loss suspected | @vp-engineering |

## Related Runbooks

- [Related Runbook 1](../domain/related-runbook.md)
- [Related Runbook 2](../domain/other-runbook.md)

## Changelog

| Date | Author | Change |
|------|--------|--------|
| YYYY-MM-DD | @author | Initial version |
