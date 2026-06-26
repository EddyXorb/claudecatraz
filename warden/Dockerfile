# Warden — single trust boundary (§6.7): small, pinned, non-root, read-only rootfs.
FROM python:3.12-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.10 /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY warden ./warden
# Reproducible install from the lockfile, runtime deps only.
RUN uv sync --frozen --no-dev

FROM python:3.12-slim

# Non-root runtime user.
RUN useradd --system --uid 10001 --create-home warden

COPY --from=build /app /app
ENV PATH="/app/.venv/bin:$PATH"

# State/log volumes (rootfs stays read-only at runtime; see docker-compose).
RUN mkdir -p /var/lib/warden /var/log/warden \
    && chown -R warden:warden /var/lib/warden /var/log/warden

USER warden
WORKDIR /app
EXPOSE 8080 9090

ENTRYPOINT ["python", "-m", "warden"]
