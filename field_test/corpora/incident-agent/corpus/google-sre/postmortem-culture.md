# Postmortem Culture

*Google SRE Book - Chapter 15*

## Postmortem Triggers

Not every incident requires a postmortem, but the following events should always trigger one:

- **User-visible downtime**: Any outage that affects end users.
- **Data loss**: Any permanent loss of customer or system data.
- **On-call intervention**: Any page that required human action beyond a simple acknowledgement.
- **Resolution above a threshold**: Issues that took longer than a defined time to resolve (e.g., >30 minutes).
- **Monitoring failure**: Incidents that monitoring should have caught but did not.

## Blameless Postmortems

The cornerstone of a learning culture: postmortems focus on **systematic causes**, not individual mistakes.

- Root causes are found in processes, tooling, design decisions, and organizational factors.
- "Human error" is never a root cause — it is a symptom of deeper systemic issues.
- The question is not "who made the mistake" but "why were the conditions such that a mistake was possible?"

## Collaborate and Share Knowledge

Postmortems are most valuable when they are visible and collaborative.

- **Review**: Postmortems should be reviewed by peers for accuracy and completeness.
- **Publish**: Make postmortems accessible to the entire organization (with appropriate redaction of sensitive data).
- **Share widely**: Present findings in team meetings, company all-hands, and cross-team reviews.

This prevents the same failures from recurring in different parts of the organization.

## Introducing Postmortem Culture

Adopting a blameless postmortem culture takes deliberate effort.

- **Ease in**: Start with low-stakes incidents. Build trust that postmortems are for learning, not punishment.
- **Reward participation**: Celebrate thorough postmortems and actionable action items. Recognize people who identify systemic risks.
- **Leadership involvement**: Leaders must model blameless behavior. When executives accept responsibility for systemic failures, it sets the tone for the entire organization.

## Ask for Feedback

Postmortem effectiveness should itself be measured. Ask:

- Were the action items completed?
- Did the same type of incident recur?
- Did teams feel safe sharing their perspectives?
- Could the postmortem process be improved?

Continuous improvement applies to the postmortem process itself.
