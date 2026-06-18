#!/usr/bin/env python3
"""
Start a Claude dev-env worker container.

Examples:
  ./start.py --project-dir ~/code/my-service
  ./start.py --project-dir ~/code/my-service --gitlab-url https://gitlab.example.com --gitlab-token glpat-xxx
  ./start.py --project-dir ~/code/my-service --detach
  ./start.py --project-dir ~/code/my-service --build --detach
  ./start.py stop
  ./start.py logs
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

COMPOSE_FILE = Path(__file__).parent / "docker-compose.yml"


def cmd_up(args: argparse.Namespace) -> None:
    project_dir = Path(args.project_dir).resolve()
    claude_home = Path(args.claude_home).resolve()

    if not project_dir.exists():
        sys.exit(f"error: project dir does not exist: {project_dir}")

    claude_home.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["PROJECT_DIR"] = str(project_dir)
    env["CLAUDE_HOME"] = str(claude_home)

    if args.api_key:
        env["ANTHROPIC_API_KEY"] = args.api_key
    if args.gitlab_url:
        env["GITLAB_URL"] = args.gitlab_url
    if args.gitlab_token:
        env["GITLAB_TOKEN"] = args.gitlab_token
    if args.claude_code_version:
        env["CLAUDE_CODE_VERSION"] = args.claude_code_version
    env["DEV_UID"] = str(args.dev_uid)

    compose = ["docker", "compose", "-f", str(COMPOSE_FILE), "up"]
    if args.build:
        compose.append("--build")
    if args.detach:
        compose.append("-d")

    subprocess.run(compose, env=env, check=True)


def cmd_stop(_args: argparse.Namespace) -> None:
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "down"],
        check=True,
    )


def cmd_logs(args: argparse.Namespace) -> None:
    compose = ["docker", "compose", "-f", str(COMPOSE_FILE), "logs"]
    if args.follow:
        compose.append("-f")
    subprocess.run(compose, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manage the Claude dev-env container",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command")

    # --- up ---
    up = sub.add_parser("up", help="Start the container (default command)")
    up.add_argument(
        "--project-dir", "-p",
        required=True,
        help="Project directory to mount at /workspace",
    )
    up.add_argument(
        "--claude-home",
        default=os.environ.get("CLAUDE_HOME", str(Path.home() / ".claude")),
        help="Claude home dir (auth, MCP config) [env: CLAUDE_HOME, default: ~/.claude]",
    )
    up.add_argument(
        "--gitlab-url",
        default=os.environ.get("GITLAB_URL", ""),
        help="GitLab instance URL, e.g. https://gitlab.example.com [env: GITLAB_URL]",
    )
    up.add_argument(
        "--gitlab-token",
        default=os.environ.get("GITLAB_TOKEN", ""),
        help="GitLab PAT or Project Access Token (Developer role) [env: GITLAB_TOKEN]",
    )
    up.add_argument(
        "--api-key",
        default=os.environ.get("ANTHROPIC_API_KEY", ""),
        help="Anthropic API key [env: ANTHROPIC_API_KEY]",
    )
    up.add_argument(
        "--claude-code-version",
        default=os.environ.get("CLAUDE_CODE_VERSION", ""),
        help="@anthropic-ai/claude-code npm version to install [env: CLAUDE_CODE_VERSION]",
    )
    up.add_argument(
        "--dev-uid",
        default=os.environ.get("DEV_UID", str(os.getuid())),
        help="UID for the in-container dev user — must match owner of claude-home [env: DEV_UID, default: current user]",
    )
    up.add_argument("--build", action="store_true", help="Rebuild image before starting")
    up.add_argument("--detach", "-d", action="store_true", help="Run in background")

    # --- stop ---
    sub.add_parser("stop", help="Stop and remove the container")

    # --- logs ---
    logs = sub.add_parser("logs", help="Show container logs")
    logs.add_argument("--follow", "-f", action="store_true", help="Follow log output")

    # default to 'up' so bare invocation with --project-dir works
    if len(sys.argv) > 1 and sys.argv[1] not in ("up", "stop", "logs", "-h", "--help"):
        sys.argv.insert(1, "up")

    args = parser.parse_args()

    if args.command == "up":
        cmd_up(args)
    elif args.command == "stop":
        cmd_stop(args)
    elif args.command == "logs":
        cmd_logs(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
