# syntax=docker/dockerfile:1.7
ARG NODE_IMAGE=node:22.18.0-bookworm-slim@sha256:752ea8a2f758c34002a0461bd9f1cee4f9a3c36d48494586f60ffce1fc708e0e
ARG PYTHON_IMAGE=python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

FROM ${NODE_IMAGE} AS node-runtime

FROM ${PYTHON_IMAGE}

ARG CODEX_VERSION=0.151.0

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        bubblewrap \
        build-essential \
        ca-certificates \
        curl \
        git \
        jq \
        sqlite3 \
        wget \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && npm install --global "@openai/codex@${CODEX_VERSION}" \
    && codex --version

WORKDIR /tmp/project-build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir . pytest==9.1.1 \
    && rm -rf /tmp/project-build

RUN groupadd --gid 1000 automation \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash automation \
    && mkdir -p /workspace /codex-home \
    && chown -R automation:automation /workspace /codex-home

COPY automation/container/entrypoint.sh /usr/local/bin/automation-entrypoint
COPY automation/container/smoke-test.sh /usr/local/bin/automation-smoke-test
RUN chmod 0555 /usr/local/bin/automation-entrypoint /usr/local/bin/automation-smoke-test

USER automation
WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/automation-entrypoint"]
CMD ["bash"]
