FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

RUN groupadd --gid 1000 agent && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash agent

WORKDIR /app

COPY pyproject.toml ./
RUN uv sync --no-install-project --no-dev

COPY src ./src
RUN uv sync --no-dev

RUN mkdir -p /app/data /app/worktrees /app/repos && chown -R agent:agent /app /opt/venv

USER agent

ENV PATH="/opt/venv/bin:${PATH}"

CMD ["python", "-m", "ai_agent.main"]
