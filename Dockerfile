FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HIVEPLANE_EXECUTION__ENTRYPOINTS_ROOT=/app

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY examples ./examples
COPY deploy/testdata ./deploy/testdata
COPY field_test/__init__.py ./field_test/__init__.py
COPY field_test/workloads ./field_test/workloads
COPY field_test/shims ./field_test/shims
COPY field_test/corpora/support-agent ./field_test/corpora/support-agent
COPY field_test/corpora/eval-judge ./field_test/corpora/eval-judge
COPY field_test/corpora/regressed-agent ./field_test/corpora/regressed-agent
COPY field_test/agents/proven/exectrace/agent-raw ./field_test/agents/proven/exectrace/agent-raw
COPY field_test/agents/proven/exectrace/agent-eval-graph ./field_test/agents/proven/exectrace/agent-eval-graph

RUN python -m pip install --upgrade pip && python -m pip install ".[langgraph]"

EXPOSE 8000

CMD ["uvicorn", "hiveplane.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
