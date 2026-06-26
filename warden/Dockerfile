# Warden — single trust boundary (§6.7): small, pinned, non-root, read-only rootfs.
FROM python:3.12-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.11.24 /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY warden ./warden
# Reproducible install from the lockfile, runtime deps only.
RUN uv sync --frozen --no-dev

FROM python:3.12-slim

# Non-root runtime user. DEV_UID passt den Owner an die Host-UID an, damit
# Bind-gemountete State- und Log-Verzeichnisse ohne Docker-Tools lesbar sind (W12).
ARG DEV_UID=10001
RUN apt-get update \
 && apt-get install -y --no-install-recommends gosu \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid ${DEV_UID} --create-home warden

COPY --from=build /app /app
ENV PATH="/app/.venv/bin:$PATH"

# State/log volumes (rootfs stays read-only at runtime; see docker-compose).
# Ownership is fixed at container start by docker-entrypoint.sh (bind-mount override).
RUN mkdir -p /var/lib/warden /var/log/warden

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

WORKDIR /app
EXPOSE 8080 9090

ENTRYPOINT ["docker-entrypoint.sh"]
