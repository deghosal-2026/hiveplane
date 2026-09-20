# Managing Incidents

*Google SRE Book - Chapter 14*

## Anatomy of Unmanaged Incidents

Unmanaged incidents share common failure patterns:

- **Sharp focus on the technical problem**: Everyone tunnels on debugging, ignoring communication and coordination.
- **Poor communication**: Stakeholders are left in the dark. Duplicate work happens.
- **Freelancing**: Team members take independent actions without coordination, potentially making the situation worse.

Structured incident management prevents these problems.

## Incident Command System

Google's incident management model is based on the Incident Command System (ICS), which provides:

- **Recursive separation of responsibilities**: As the incident grows, roles can be split and delegated to maintain clarity.

## Roles

Clear role definition prevents confusion and ensures accountability.

- **Incident Commander (IC)**: Coordinates the response, delegates tasks, and manages communication. The IC does not debug — they orchestrate.
- **Operations Lead**: Focuses on the technical response. Applies fixes, runs commands, and investigates.
- **Communication Lead**: Handles external and internal status updates, stakeholder communication, and post-incident documentation.
- **Planning Lead**: Tracks resources, manages the incident timeline, and anticipates future needs (e.g., "we will need more database capacity in 30 minutes").

## Recognized Command Post (War Room)

A dedicated space — physical or virtual — where the incident team coordinates. The war room:

- Brings the team into one communication channel
- Reduces latency in information sharing
- Creates a clear boundary between responders and the rest of the organization

## Live Incident State Document

A shared document that tracks:

- What happened (timeline)
- What is being done (active tasks)
- Who is doing what (role assignments)
- Current system status
- Decisions made and why

This document is the single source of truth during the incident.

## Clear Handoff at End of Shift

When a shift change happens during a long-running incident:

- The outgoing IC briefs the incoming IC on status, open tasks, and unresolved issues.
- The live document is reviewed and updated.
- The handoff is explicit and acknowledged.

## When to Declare an Incident

Not every problem needs full incident management. Declare an incident when:

- A second team is needed to resolve the issue
- The issue is customer-visible
- The problem has not been solved after 1 hour of investigation

## Best Practices

- **Prioritize**: Stop the bleeding. Restore service. Then investigate root cause.
- **Prepare**: Runbooks, playbooks, and practiced procedures reduce chaos.
- **Trust**: Delegate tasks and trust your teammates to execute.
- **Introspect**: During the incident, periodically ask: "Are we still effective? Do we need help?"
- **Consider alternatives**: Question your assumptions. What are we missing? What else could this be?
- **Practice**: Run incident response drills and tabletop exercises to build muscle memory.
