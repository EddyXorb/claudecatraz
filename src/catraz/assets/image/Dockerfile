# catraz builds the Agent runtime (Node + Claude Code) on top of this via apt-get.
# THE BASE MUST THEREFORE BE DEBIAN/UBUNTU-BASED (apt available). Other distros
# (Alpine/musl, RHEL/dnf) will fail the claude-layer build.
# Change the FROM or append your own RUN/COPY lines.
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y git python3 && rm -rf /var/lib/apt/lists/*
