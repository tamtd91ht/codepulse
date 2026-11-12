FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

# The review worker calls the Claude Code CLI (LLM_PROVIDER=claude_code).
# Pinned so a CLI upgrade never silently changes review results; bump it with an eval run.
# git: the command worker clones the repo a .claude command runs in (app/commands).
ARG CLAUDE_CODE_VERSION=2.1.308
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm git ca-certificates \
    && npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && npm cache clean --force \
    && apt-get purge -y npm \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Kiểm tra tĩnh (docs/specs/07): Gitleaks and Semgrep, pinned. Semgrep lives in its own venv (its
# pinned dependencies never meet CodePulse's; a wrapper puts only that venv on its PATH);
# Gitleaks is the release binary checked against the sha256 pinned here. The Semgrep rules
# (registry ruleset p/default) are fetched once at build time into the image: a review needs no
# network for them. Leave STATIC_TOOLS empty to build without the tools (the review then notes
# "chưa cài trên worker" and goes on).
ARG STATIC_TOOLS=1
ARG SEMGREP_VERSION=1.85.0
ARG SEMGREP_RULESET=p/default
ARG GITLEAKS_VERSION=8.21.2
ARG GITLEAKS_SHA256_AMD64=5bc41815076e6ed6ef8fbecc9d9b75bcae31f39029ceb55da08086315316e3ba
ARG GITLEAKS_SHA256_ARM64=654c935542c89f565aabe7bf7c6c500830f116c114f0aeb509d2460c1ac2e6da
RUN if [ -n "$STATIC_TOOLS" ]; then \
        set -eux; \
        python -m venv /opt/semgrep \
        && /opt/semgrep/bin/pip install --no-cache-dir "semgrep==${SEMGREP_VERSION}" "setuptools<75" \
        && printf '#!/bin/sh\nPATH="/opt/semgrep/bin:$PATH" exec /opt/semgrep/bin/semgrep "$@"\n' > /usr/local/bin/semgrep \
        && chmod 755 /usr/local/bin/semgrep \
        && mkdir -p /opt/semgrep/rules \
        && python -c "import urllib.request as u; u.urlretrieve('https://semgrep.dev/c/${SEMGREP_RULESET}', '/opt/semgrep/rules/default.yml')" \
        && grep -q '^rules:' /opt/semgrep/rules/default.yml \
        && arch="$(dpkg --print-architecture)" \
        && case "$arch" in \
             amd64) ga=x64; sum="$GITLEAKS_SHA256_AMD64" ;; \
             arm64) ga=arm64; sum="$GITLEAKS_SHA256_ARM64" ;; \
             *) echo "no gitleaks for $arch"; exit 1 ;; \
           esac \
        && file="gitleaks_${GITLEAKS_VERSION}_linux_${ga}.tar.gz" \
        && python -c "import urllib.request as u; u.urlretrieve('https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/$file', '/tmp/$file')" \
        && echo "$sum  /tmp/$file" | sha256sum -c - \
        && tar -xzf "/tmp/$file" -C /usr/local/bin gitleaks \
        && rm -f "/tmp/$file" \
        && gitleaks version && semgrep --version; \
    fi
# Reviews use the rules fetched above (a registry name here would download them on every run).
ENV SEMGREP_CONFIG=/opt/semgrep/rules/default.yml

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

COPY . .

# The command runs' and the avatars' (AVATAR_DIR) folders: a volume mounted there starts owned
# by codepulse (app/commands, app/avatars.py).
RUN useradd --create-home codepulse \
    && mkdir -p /tmp/codepulse-runs /run/codepulse-gate /srv/data/avatars \
    && chown codepulse /tmp/codepulse-runs /srv/data/avatars && chmod 755 /run/codepulse-gate
USER codepulse

CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
