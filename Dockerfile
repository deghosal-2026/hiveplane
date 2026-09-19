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

RUN python -m pip install --upgrade pip && python -m pip install ".[langgraph]"

EXPOSE 8000

CMD ["uvicorn", "hiveplane.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
