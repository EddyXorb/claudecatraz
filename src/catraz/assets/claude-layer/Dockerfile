ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG NODE_VERSION=22
ARG CLAUDE_CODE_VERSION=latest
ARG DEV_UID=1000
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y gosu && \
    curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash - && \
    apt-get install -y nodejs && rm -rf /var/lib/apt/lists/* && \
    npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}
# Resolve UID-1000 conflict (Ubuntu's `ubuntu` user), otherwise useradd fails:
RUN (userdel -r ubuntu 2>/dev/null || true) && useradd -m -u ${DEV_UID} -s /bin/bash dev
COPY container/entrypoint.py /entrypoint.py
COPY AGENT.md /opt/claude-dev-env/AGENT.md
ENV HOME=/home/dev
WORKDIR /workspace
ENTRYPOINT ["python3", "/entrypoint.py"]
