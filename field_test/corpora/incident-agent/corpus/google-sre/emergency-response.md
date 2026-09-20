# Emergency Response

*Google SRE Book - Chapter 13*

## Core Principles

When an emergency strikes, the most important rule is:

- **Don't panic**: Panic impairs judgment. Take a breath, assess the situation, and act deliberately.
- **Pull in more people when overwhelmed**: If you feel overloaded, escalate immediately. No one expects a single person to handle a major incident alone.

## Case Study 1: Test-Induced Emergency

A database permissions test was scoped more broadly than intended, causing a production database to become inaccessible.

**Root cause**: A testing script that was meant to validate permissions on a staging environment was accidentally pointed at production, with a wider scope than expected.

**Lesson**: Test infrastructure must have strong isolation from production, and testing tools should be designed to fail safe when misconfigured.

## Case Study 2: Change-Induced Emergency

A configuration push to a fleet of servers triggered a crash-loop bug, causing widespread service degradation.

**Root cause**: A new configuration option had an edge case that caused the server process to crash on startup. The deployment system continued pushing the config to all servers before the crash loop was detected.

**Lessons**: Always canary configuration changes. Deploy to a small subset first and monitor for errors before rolling out fleet-wide.

## Case Study 3: Process-Induced Emergency

An automation system bug sent a command to a fleet of machines that wiped their disks (Diskerase).

**Root cause**: A workflow automation tool had a logic error that caused it to send a destructive command to the wrong set of machines.

**Lessons**: Automation must have safety checks, confirmation steps, and rate limiting. Destructive operations require additional safeguards such as manual approval gates.

## Learnings

From these case studies, several best practices emerge:

- **Always test rollback procedures**: If you cannot roll back a change quickly, you are one bad deploy away from a prolonged outage.
- **Thorough canarying**: Deploy to 1% of instances, then 5%, then 20%. Monitor at each stage and be prepared to abort.
- **Maintain out-of-band communications**: When the primary communication channel (e.g., Slack, chat) goes down, have a backup (phone bridge, IRC, pagers).

## Proactive Practices

- **Keep history of outages**: A timeline of past incidents helps identify patterns and prevents repeat occurrences.
- **Ask "what if" questions**: Run tabletop exercises. What if the database master fails? What if all of a region goes down?
- **Encourage proactive testing**: Chaos engineering, load testing, and failure injection reveal weaknesses before they cause customer-facing incidents.
