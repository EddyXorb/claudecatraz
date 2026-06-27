FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    python3 python3-pip python-is-python3 \
    git curl wget unzip pkg-config \
    cmake gcc-multilib g++-multilib gcovr \
    lsb-release software-properties-common gnupg \
    libssl-dev libclang-dev \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# Python tools — system-wide so any user can call them
ARG UV_VERSION
ARG CONAN_VERSION

ENV CONAN_REVISIONS_ENABLED=1

RUN pip install --break-system-packages uv==${UV_VERSION} conan==${CONAN_VERSION}

# LLVM
ARG CLANG_VERSION

RUN apt-get update && \
    wget https://apt.llvm.org/llvm.sh && chmod +x llvm.sh && ./llvm.sh ${CLANG_VERSION} all && \
    ln -s /usr/bin/clang-${CLANG_VERSION}        /usr/bin/clang && \
    ln -s /usr/bin/clang-format-${CLANG_VERSION} /usr/bin/clang-format && \
    ln -s /usr/bin/clang-tidy-${CLANG_VERSION}   /usr/bin/clang-tidy && \
    rm -f llvm.sh && \
    rm -rf /var/lib/apt/lists/*

# Rust — install to global paths so the non-root dev user can use it
ARG RUST_VERSION

ENV RUSTUP_HOME=/usr/local/rustup
ENV CARGO_HOME=/usr/local/cargo
ENV PATH="/usr/local/cargo/bin:$PATH"

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | \
    sh -s -- -y --default-toolchain ${RUST_VERSION} --no-modify-path && \
    rustup component add rustfmt clippy llvm-tools-preview && \
    curl -L --proto '=https' --tlsv1.2 -sSf \
        https://raw.githubusercontent.com/cargo-bins/cargo-binstall/main/install-from-binstall-release.sh | bash && \
    cargo binstall --no-confirm cargo-llvm-cov cargo-nextest cargo-deny && \
    chmod -R a+rX /usr/local/rustup /usr/local/cargo

# Node.js + Claude Code — global install
ARG NODE_VERSION=22
ARG CLAUDE_CODE_VERSION

RUN apt-get update && \
    curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}

# Non-root user — Claude Code refuses --dangerously-skip-permissions as root.
# DEV_UID should match the host user who owns the bind-mounted claude-home dir.
ARG DEV_UID=1000
RUN userdel -r ubuntu 2>/dev/null || true && \
    useradd -m -u ${DEV_UID} -s /bin/bash dev

COPY src/catraz/assets/container/entrypoint.py /entrypoint.py

# Harness-Doku (Sandbox-Kontext für den Agenten). Single source of truth im Image;
# entrypoint.py injiziert sie beim Start nach ~/.claude/CLAUDE.md (User-Memory), damit
# sie unabhängig vom gemounteten /workspace-Projekt gilt.
COPY src/catraz/assets/AGENT.md /opt/claude-dev-env/AGENT.md

ENV HOME=/home/dev
WORKDIR /workspace
