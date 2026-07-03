ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG NODE_VERSION=22
ARG CLAUDE_CODE_VERSION=latest
ARG DEV_UID=1000
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y python3 git curl ca-certificates gnupg gosu && \
    curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash - && \
    apt-get install -y nodejs && rm -rf /var/lib/apt/lists/* && \
    npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}
# Resolve UID-1000 conflict (Ubuntu's `ubuntu` user), otherwise useradd fails:
RUN (userdel -r ubuntu 2>/dev/null || true) && useradd -m -u ${DEV_UID} -s /bin/bash dev
# Generic entrypoint + shared adapter contract (§05.2) — one copy, agent-agnostic.
COPY container/entrypoint.py /entrypoint.py
COPY container/agent_contract.py /agent_contract.py
COPY container/git_routing.py /git_routing.py
# This agent's adapter + manifest (§05.3) — the ONE profile this image was built
# for. Flattened next to entrypoint.py so `import agent_contract`/a fixed-path
# load of `agent_adapter.py` resolves without any runtime adapter selection
# (§06.2/A2: the build already committed to exactly one adapter).
COPY agents/claude/adapter.py /agent_adapter.py
COPY agents/claude/agent.toml /agent.toml
COPY agents/claude/AGENT.md.tmpl /AGENT.md.tmpl
ENV HOME=/home/dev
WORKDIR /workspace
ENTRYPOINT ["python3", "/entrypoint.py"]
