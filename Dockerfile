FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

# System deps:
#   git           — repo operations / worktrees
#   curl, gnupg   — install Node.js + claude CLI
#   ca-certificates — TLS for npm/Node
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ca-certificates \
        curl \
        gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI (uses host OAuth from /home/agent/.claude — see compose volume)
RUN npm install -g @anthropic-ai/claude-code

# uv must match the version that generated the lockfiles (this project's and
# any target repo the agent edits). 0.5.11 wrote an OLDER lockfile format and
# silently rewrote new-format locks (stripping `revision`/`upload-time`) on any
# `uv` command, producing thousands of lines of noise in agent MRs. 0.11.x is
# the host/repo version and emits the same format the locks already use.
#
# Installed from PyPI rather than `COPY --from=ghcr.io/astral-sh/uv` because the
# colima docker daemon pulls ghcr layers extremely slowly here; the daemon's
# PyPI path is fast (it is what `uv sync` below uses).
RUN pip install --no-cache-dir uv==0.11.7

RUN groupadd --gid 1000 agent && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash agent

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src ./src
COPY migrations ./migrations
COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
RUN uv sync --frozen --no-dev

RUN mkdir -p /app/worktrees /app/repos /home/agent/.claude \
    && chown -R agent:agent /app /opt/venv /home/agent

USER agent

ENV PATH="/opt/venv/bin:${PATH}"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
