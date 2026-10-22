FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

# The review worker calls the Claude Code CLI (LLM_PROVIDER=claude_code).
# Pinned so a CLI upgrade never silently changes review results; bump it with an eval run.
ARG CLAUDE_CODE_VERSION=2.1.308
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && npm cache clean --force \
    && apt-get purge -y npm \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

COPY . .

RUN useradd --create-home codepulse
USER codepulse

CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
