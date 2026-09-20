"""
Native incident-commander connector — built-in mock handlers.

Provides three tools (``incident_query_active``, ``incident_query_history``,
``incident_get_timeline``) backed by in-memory mock data.

These handlers register only when no HTTP-proxy connector in the YAML
config already covers the same tool name (see ``connectors/__init__.py``).
"""

import json
from datetime import datetime, timedelta, timezone

# ── Active incidents (currently open) ──────────────────────────────
ACTIVE_INCIDENTS = [
    {
        "id": "INC-2026-142",
        "title": "Payment service error rate spike",
        "severity": "sev2",
        "service": "payment-service",
        "started_at": "2026-07-20T14:23:00Z",
        "duration_minutes": 45,
        "responder": "alice@team",
        "status": "investigating",
    },
    {
        "id": "INC-2026-148",
        "title": "Auth service latency increase",
        "severity": "sev3",
        "service": "auth-service",
        "started_at": "2026-07-20T16:05:00Z",
        "duration_minutes": 12,
        "responder": "bob@team",
        "status": "monitoring",
    },
    {
        "id": "INC-2026-150",
        "title": "API gateway timeout on /checkout",
        "severity": "sev2",
        "service": "api-gateway",
        "started_at": "2026-07-20T17:30:00Z",
        "duration_minutes": 8,
        "responder": "carol@team",
        "status": "investigating",
    },
]


# ── Historical incidents (resolved) ────────────────────────────────
# Dates are generated relative to today so the mock data never goes stale.
def _make_historical_incidents():
    today = datetime.now(timezone.utc)
    offsets = [23, 21, 19, 16, 13, 11, 9, 6, 4, 2]
    titles = [
        (
            "Database connection pool exhausted",
            "sev1",
            "payment-service",
            "Connection leak in order worker",
        ),
        (
            "Redis cluster failover",
            "sev2",
            "auth-service",
            "Memory pressure on primary node",
        ),
        (
            "Deploy caused 5xx spike",
            "sev1",
            "api-gateway",
            "Missing env var in new release",
        ),
        (
            "Certificate expiry alert",
            "sev3",
            "api-gateway",
            "Auto-renewal cron job failed",
        ),
        (
            "Payment timeout for high-value orders",
            "sev2",
            "payment-service",
            "Third-party provider rate limit hit",
        ),
        (
            "Auth tokens not refreshing",
            "sev2",
            "auth-service",
            "JWT library upgrade changed expiry behavior",
        ),
        ("API gateway memory leak", "sev3", "api-gateway", "Unbounded request logging"),
        (
            "Payment service degraded after deploy",
            "sev2",
            "payment-service",
            "Config map not updated for new region",
        ),
        (
            "Auth DB migration rollback",
            "sev1",
            "auth-service",
            "Migration script had destructive ALTER",
        ),
        (
            "CI pipeline secret rotation failure",
            "sev3",
            "api-gateway",
            "Secret not rotated in all regions",
        ),
    ]
    incidents = []
    for i, (title, sev, svc, cause) in enumerate(titles):
        date = (today - timedelta(days=offsets[i])).strftime("%Y-%m-%d")
        incidents.append(
            {
                "id": f"INC-2026-{101 + i}",
                "title": title,
                "severity": sev,
                "service": svc,
                "date": date,
                "root_cause": cause,
                "resolved": True,
            }
        )
    return incidents


HISTORICAL_INCIDENTS = _make_historical_incidents()

# ── Incident timelines (event sequences per incident) ──────────────
INCIDENT_TIMELINES = {
    "INC-2026-142": {
        "events": [
            {
                "timestamp": "2026-07-20T14:23:00Z",
                "type": "detected",
                "description": "Error rate exceeded 5% threshold on payment-service",
                "actor": "prometheus",
            },
            {
                "timestamp": "2026-07-20T14:25:00Z",
                "type": "paged",
                "description": "Primary responder alice@team notified",
                "actor": "pagerduty",
            },
            {
                "timestamp": "2026-07-20T14:30:00Z",
                "type": "investigating",
                "description": "Alice identified recent deploy v2.14.3 as potential cause",
                "actor": "alice@team",
            },
            {
                "timestamp": "2026-07-20T14:45:00Z",
                "type": "action",
                "description": "Rolled back payment-service to v2.14.2",
                "actor": "alice@team",
            },
            {
                "timestamp": "2026-07-20T14:50:00Z",
                "type": "improving",
                "description": "Error rate dropping — 2% and decreasing",
                "actor": "prometheus",
            },
            {
                "timestamp": "2026-07-20T15:05:00Z",
                "type": "action",
                "description": "Root cause identified: null pointer in new order validation",
                "actor": "alice@team",
            },
        ],
        "correlated_deploys": [
            {
                "id": "DEP-2026-891",
                "service": "payment-service",
                "time": "2026-07-20T14:00:00Z",
            },
        ],
    },
    "INC-2026-148": {
        "events": [
            {
                "timestamp": "2026-07-20T16:05:00Z",
                "type": "detected",
                "description": "P99 latency for /auth/token increased from 200ms to 1200ms",
                "actor": "prometheus",
            },
            {
                "timestamp": "2026-07-20T16:08:00Z",
                "type": "paged",
                "description": "Primary responder bob@team notified",
                "actor": "pagerduty",
            },
            {
                "timestamp": "2026-07-20T16:15:00Z",
                "type": "investigating",
                "description": "Bob identified connection pool contention under load",
                "actor": "bob@team",
            },
            {
                "timestamp": "2026-07-20T16:20:00Z",
                "type": "action",
                "description": "Scaled auth-service replicas from 3 to 6",
                "actor": "bob@team",
            },
            {
                "timestamp": "2026-07-20T16:30:00Z",
                "type": "improving",
                "description": "Latency dropping — P99 at 450ms",
                "actor": "prometheus",
            },
        ],
        "correlated_deploys": [],
    },
    "INC-2026-150": {
        "events": [
            {
                "timestamp": "2026-07-20T17:30:00Z",
                "type": "detected",
                "description": "/checkout endpoint returning 504 for 2% of requests",
                "actor": "prometheus",
            },
            {
                "timestamp": "2026-07-20T17:32:00Z",
                "type": "paged",
                "description": "Primary responder carol@team notified",
                "actor": "pagerduty",
            },
            {
                "timestamp": "2026-07-20T17:38:00Z",
                "type": "investigating",
                "description": "Carol identified upstream payment service latency as bottleneck",
                "actor": "carol@team",
            },
        ],
        "correlated_deploys": [],
    },
}


async def handle_query_active(args: dict) -> str:
    """Return active incidents, optionally filtered by service/severity."""
    service = args.get("service")
    severity = args.get("severity")
    results = ACTIVE_INCIDENTS
    if service:
        results = [i for i in results if i["service"] == service]
    if severity:
        results = [i for i in results if i["severity"] == severity]
    return json.dumps({"incidents": results})


async def handle_query_history(args: dict) -> str:
    """Return historical incidents, optionally filtered by service/days."""
    service = args.get("service")
    # days may arrive as int (unit tests) or str (MCP client); normalise to int
    try:
        days = int(args.get("days", 30))
    except (TypeError, ValueError):
        days = 30
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    results = HISTORICAL_INCIDENTS
    if service:
        results = [i for i in results if i["service"] == service]
    if days < 365:
        results = [
            i
            for i in results
            if datetime.strptime(i["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            >= cutoff
        ]
    return json.dumps({"incidents": results})


async def handle_get_timeline(args: dict) -> str:
    """Return the full timeline for a given incident_id."""
    incident_id = args.get("incident_id", "")
    timeline = INCIDENT_TIMELINES.get(incident_id)
    if not timeline:
        return json.dumps({"error": f"incident {incident_id} not found"})
    return json.dumps(timeline)


def register(registry, skip_names: set | None = None):
    """Register native incident-commander handlers.

    Parameters
    ----------
    registry : ToolRegistry
        The registry to populate.
    skip_names : set of str, optional
        Tool names to skip (e.g. because an HTTP proxy connector
        already covers them).
    """
    skip_names = skip_names or set()
    if "incident_query_active" not in skip_names:
        registry.register_handler("incident_query_active", handle_query_active)
    if "incident_query_history" not in skip_names:
        registry.register_handler("incident_query_history", handle_query_history)
    if "incident_get_timeline" not in skip_names:
        registry.register_handler("incident_get_timeline", handle_get_timeline)
