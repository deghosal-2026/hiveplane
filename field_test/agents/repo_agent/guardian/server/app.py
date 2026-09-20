"""FastAPI dashboard server for Guardian metrics."""

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader
from prometheus_client import generate_latest

from guardian.audit.logger import AuditLogger
from guardian.metrics.collector import MetricsCollector

from guardian.server.mcp_api import router as mcp_router

templates_dir = os.path.join(os.path.dirname(__file__), "templates")
env = Environment(loader=FileSystemLoader(templates_dir))

app = FastAPI(title="AI Code Guardian Dashboard")
app.include_router(mcp_router)


def _get_collector(audit_dir: str = "~/.guardian/audit") -> MetricsCollector:
    logger = AuditLogger(repo="_dashboard_", audit_dir=audit_dir)
    return MetricsCollector(logger)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, audit_dir: str = "~/.guardian/audit"):
    stats = _get_collector(audit_dir)
    template = env.get_template("dashboard.html")
    html = template.render(request=request, stats=stats)
    return HTMLResponse(html)


@app.get("/api/stats")
async def api_stats(audit_dir: str = "~/.guardian/audit"):
    stats = _get_collector(audit_dir)
    return stats.to_dict()


@app.get("/metrics")
async def metrics():
    return generate_latest()
