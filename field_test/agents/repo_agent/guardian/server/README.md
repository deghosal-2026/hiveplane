# server — Dashboard Server

Local FastAPI dashboard for Guardian metrics.

- `app.py` — FastAPI app with /dashboard (HTML), /api/stats (JSON), /metrics (Prometheus)
- `templates/dashboard.html` — Jinja2 dashboard template

Also generates static HTML for GitHub Pages deployment (`guardian generate-pages`).
