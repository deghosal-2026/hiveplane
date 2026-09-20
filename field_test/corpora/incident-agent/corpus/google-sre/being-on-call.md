# Being On-Call

*Google SRE Book - Chapter 11*

## Life of an On-Call Engineer

On-call engineers respond to pages when production incidents occur. Response time expectations vary by severity:

- **Critical (SEV1)**: 5 minutes to acknowledge and begin response
- **Less critical (SEV2/SEV3)**: 30 minutes to respond

The on-call role requires focus, rapid context switching, and the ability to make sound decisions under pressure.

## Primary and Secondary Rotations

Teams typically run two simultaneous on-call rotations:

- **Primary**: First responder for all pages. Handles initial triage and mitigation.
- **Secondary**: Backup for the primary. Handles non-urgent tasks, reviews changes, and takes over if the primary is overwhelmed or unavailable.

This separation ensures there is always coverage and reduces the cognitive load on any single person.

## Balanced On-Call

On-call must be sustainable. Google's guidelines:

- **Maximum 25% of time on-call**: Anything more leads to burnout and degraded response quality.
- **Minimum 8 engineers for a 24/7 rotation**: With fewer people, the per-person burden becomes too high.
- Shifts should be limited in duration (typically 12-24 hours) to prevent fatigue.

## Quality Balance

Not all incidents are equal. Measure the quality of on-call by incident load:

- **Maximum 2 incidents per 12-hour shift**: More than this indicates systemic problems.
- Each incident takes approximately 6 hours to fully handle (triage, mitigation, root cause, follow-up).

When incident volume exceeds these thresholds, the team should address the underlying causes rather than just enduring the load.

## Feeling Safe

On-call is stressful. Stress hormones like cortisol and adrenaline impair cognition and decision-making. To create psychological safety:

- **Clear escalation paths**: Engineers should know when and how to escalate without fear.
- **Blameless culture**: Incidents are system failures, not individual failures.
- **Clear procedures**: Runbooks and playbooks reduce uncertainty.
- **Support during incidents**: Primary should never feel alone — secondary and incident commanders provide backup.

## Avoiding Operational Overload

Operational overload happens when engineers are paged too often for non-actionable alerts. Common causes:

- **Misconfigured monitoring**: Alerts that fire without requiring human action.
- **Noisy alerts**: Low-signal alerts that condition engineers to ignore pages.
- **Paging must be actionable**: Every page should require a human response. If an alert does not require action, it should not page.

Teams should regularly review and tune alerting to ensure every page is justified.

## Giving Back the Pager

When a system does not meet operational standards, the on-call team should **give back the pager** — refuse to be on-call for it until the issues are fixed. This forces the team to invest in reliability rather than accepting chronic operational overload as normal.

## Operational Underload

Being on-call too infrequently is also a problem. Engineers need hands-on experience with production systems to maintain proficiency.

- **At least 1-2 on-call shifts per quarter**: Fewer than that and engineers lose familiarity with the system, making them slower and less effective when they do respond.
