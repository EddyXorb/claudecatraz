"""Findings + Checks."""

import json
import os
import shutil
import subprocess
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, cast

from catraz.envfile import load_env
from catraz.compose import run as compose_run, compose_ps
from catraz.errors import CliError
from catraz.hostfs import host_uid
from catraz import paths
from catraz import image

OK, WARN, BAD = "ok", "warn", "bad"

# Doctor-only mirror of the Warden's action vocabulary + cascade (reimplemented
# since catraz never imports warden's Python). Four ids are compiled-in denials.
READ_ACTIONS: frozenset[str] = frozenset(
    {
        "repo.read",
        "project.read",
        "instance.projects.read",
        "instance.users.read",
        "instance.meta.read",
    }
)
IRREVERSIBLE_ACTIONS: frozenset[str] = frozenset(
    {"repo.branch.delete", "repo.tag.create", "repo.tag.delete", "project.mr.merge"}
)
ALL_ACTIONS: frozenset[str] = (
    READ_ACTIONS
    | IRREVERSIBLE_ACTIONS
    | {
        "repo.branch.create",
        "repo.branch.push",
        "project.mr.create",
        "project.mr.edit",
        "project.mr.close",
        "project.mr.comment",
        "project.ci.trigger",
        "project.issue.create",
        "project.issue.edit",
        "project.issue.close",
        "project.issue.comment",
    }
)
# Built-in default — the twelve ✔ rows: same ids as the Warden's
# guards.git.actions.DEFAULT.
DEFAULT_ACTIONS: tuple[str, ...] = (
    "repo.read",
    "repo.branch.create",
    "repo.branch.push",
    "project.read",
    "project.mr.create",
    "project.mr.edit",
    "project.mr.close",
    "project.mr.comment",
    "project.ci.trigger",
    "instance.projects.read",
    "instance.users.read",
    "instance.meta.read",
)
# Every non-read action is a write — includes the never-class ids (a
# misconfigured explicit list containing one is the Warden's ConfigError to raise).
WRITE_ACTIONS: frozenset[str] = ALL_ACTIONS - READ_ACTIONS
# repo.* ids only — the git transport guard's SUPPORTED set, what a "plain"
# endpoint (no REST guard) can enforce.
_PLAIN_ACTIONS: frozenset[str] = frozenset(a for a in ALL_ACTIONS if a.startswith("repo."))
# A branch push or create — either lets a source branch reach the remote, which
# the mr.create/pipeline.trigger coherence checks below need.
_BRANCH_WRITE_ACTIONS: frozenset[str] = frozenset({"repo.branch.create", "repo.branch.push"})

DOCTOR_SECTIONS = [
    "docker",
    "compose",
    "env",
    "tokens",
    "policy",
    "endpoints",
    "egress",
    "agent",
    "net",
    "mounts",
    "auth",
    "base",
]
# Sections that gate the trust boundary — `up` always runs these, no opt-out.
SECURITY_SECTIONS = ["docker", "compose", "env", "policy", "auth"]


class Findings:
    def __init__(self) -> None:
        self.items: list[tuple[str, str, str, str | None]] = []

    def add(self, level: str, section: str, msg: str, hint: str | None = None) -> None:
        self.items.append((level, section, msg, hint))

    def ok(self, sec: str, msg: str) -> None:
        self.add(OK, sec, msg)

    def warn(self, sec: str, msg: str, hint: str | None = None) -> None:
        self.add(WARN, sec, msg, hint)

    def bad(self, sec: str, msg: str, hint: str | None = None) -> None:
        self.add(BAD, sec, msg, hint)


def which(cmd: str) -> bool:
    return shutil.which(cmd) is not None


MIN_ENGINE = (24, 0)
MIN_COMPOSE = (2, 20)


def _parse_version(text: str) -> tuple[int, int] | None:
    """Extract the first x.y.z tuple from a version string like 'Docker version 24.0.7, ...'."""
    import re

    m = re.search(r"(\d+)\.(\d+)", text)
    if not m:
        return None
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def check_docker(f: Findings) -> None:
    if not which("docker"):
        f.bad("docker", "docker not on PATH", "install Docker + Compose v2")
        return
    r = subprocess.run(
        ["docker", "version", "--format", "{{.Server.Version}}"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        f.bad(
            "docker",
            "Docker daemon not reachable",
            "start Docker (`systemctl start docker`)",
        )
    else:
        ver = _parse_version(r.stdout.strip())
        if ver is None or ver < MIN_ENGINE:
            f.bad(
                "docker",
                f"Docker Engine {r.stdout.strip()!r} < {MIN_ENGINE[0]}.{MIN_ENGINE[1]} required",
                f"upgrade Docker Engine to ≥ {MIN_ENGINE[0]}.{MIN_ENGINE[1]}",
            )
        else:
            f.ok(
                "docker",
                f"Docker Engine {r.stdout.strip()} (≥ {MIN_ENGINE[0]}.{MIN_ENGINE[1]} ✔)",
            )
    r = subprocess.run(["docker", "compose", "version", "--short"], capture_output=True, text=True)
    if r.returncode != 0:
        f.bad("docker", "Compose v2 missing", "install the `docker compose` plugin")
    else:
        ver = _parse_version(r.stdout.strip())
        if ver is None or ver < MIN_COMPOSE:
            f.bad(
                "docker",
                f"Compose {r.stdout.strip()!r} < {MIN_COMPOSE[0]}.{MIN_COMPOSE[1]} required",
                f"upgrade Docker Compose plugin to ≥ {MIN_COMPOSE[0]}.{MIN_COMPOSE[1]}",
            )
        else:
            f.ok(
                "docker",
                f"Compose {r.stdout.strip()} (≥ {MIN_COMPOSE[0]}.{MIN_COMPOSE[1]} ✔)",
            )


def check_compose(root: Path, env: dict[str, str], f: Findings) -> None:
    """Sanity check that the warden — the trust boundary — resolves in the compose
    config: it's unconditional (no profile gate), so its absence means someone
    edited it out; `agent` depends_on `gitlab-warden`, so the stack would fail closed."""
    if not which("docker"):
        f.warn("compose", "cannot confirm services (docker missing)")
        return
    r = compose_run(root, ["config", "--services"], capture=True, check=False)
    services = r.stdout.split() if r and r.returncode == 0 else []
    if not services:
        f.warn("compose", "could not resolve compose services")
    elif "gitlab-warden" not in services:
        f.bad(
            "compose",
            "gitlab-warden missing from the compose config",
            "the warden is the trust boundary — it must always be a service",
        )
    else:
        f.ok("compose", "warden service present")


def check_env(root: Path, env: dict[str, str], f: Findings) -> None:
    envf = root / ".catraz" / ".env"
    if not envf.exists():
        f.bad("env", ".env missing", "run `catraz init`")
        return
    f.ok("env", ".env present")

    dev_uid = env.get("DEV_UID", "")
    posix = host_uid() is not None
    if not posix:
        f.warn(
            "env",
            "no host uid — bind-mount ownership and file modes are synthetic here",
            "secrets/ rests on the filesystem ACL alone; the 0600 modes do not apply",
        )
    write_dirs = [root / ".catraz" / "state", root / ".catraz" / "logs"]
    for d in write_dirs:
        if not d.exists():
            f.bad(
                "env",
                f"{d.name}/ missing",
                "run `catraz init` or `catraz doctor --fix`",
            )
            continue
        if not posix:
            f.ok("env", f"{d.name}/ present")
        elif dev_uid.isdigit():
            owner = d.stat().st_uid
            if owner != int(dev_uid):
                f.bad(
                    "env",
                    f"{d.name}/ owned by uid {owner}, DEV_UID={dev_uid}",
                    f"run `catraz doctor --fix` (chown {dev_uid}) — the non-root "
                    "service can't write otherwise",
                )
            else:
                f.ok("env", f"{d.name}/ owned by DEV_UID")


def _parse_grouped_tokens(text: str) -> dict[str, str]:
    """Parse a grouped read_tokens/write_tokens file into host -> token.

    Same splitting rule as the Warden's _parse_token_file: split on the first
    run of whitespace; #-comments and blank lines are skipped. A malformed line
    is skipped rather than raising."""
    tokens: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split(None, 1)
        if len(parts) != 2:
            continue
        tokens[parts[0]] = parts[1]
    return tokens


def _read_grouped_token_file(root: Path, filename: str) -> dict[str, str]:
    """host -> token map from .catraz/secrets/<filename>, or {} if missing/unreadable."""
    p = root / ".catraz" / "secrets" / filename
    try:
        text = p.read_text(encoding="utf-8") if p.exists() else ""
    except OSError:
        text = ""
    return _parse_grouped_tokens(text)


def _load_git_table(root: Path) -> dict[str, Any] | None:
    """Parse .catraz/config/warden.toml and return its raw [git] table, or None
    if absent/unreadable/invalid. Best-effort: doctor only warns, never raises —
    the Warden is the fail-closed side for malformed config."""
    toml_path = root / ".catraz" / "config" / "warden.toml"
    try:
        text = toml_path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    git = data.get("git")
    if not isinstance(git, dict):
        return None
    return git


def _parse_actions_list(raw: object) -> tuple[str, ...] | None:
    """Best-effort parse of an actions = [...] TOML value into a tuple of strings,
    or None if absent/malformed. The Warden's loader is the fail-closed side
    that raises ConfigError on a genuinely bad value."""
    if not isinstance(raw, list) or not all(isinstance(a, str) for a in raw):
        return None
    return tuple(raw)


def _read_git_actions_default(root: Path) -> tuple[str, ...] | None:
    """[git].actions (the domain default), or None if absent — caller falls back
    to DEFAULT_ACTIONS, same "missing key != empty list" contract as the
    Warden's Config.git_actions."""
    git = _load_git_table(root)
    if git is None:
        return None
    return _parse_actions_list(git.get("actions"))


def _parse_projects_list(raw: object) -> tuple[str, ...]:
    """Best-effort parse of an allowed_projects = [...] TOML value into a
    tuple of strings; absent or malformed degrades to (), matching the
    Warden's "missing key = empty allowlist, fail-closed" default."""
    if not isinstance(raw, list) or not all(isinstance(p, str) for p in raw):
        return ()
    return tuple(raw)


def _read_git_endpoints(root: Path) -> list[dict[str, Any]]:
    """[[git.endpoint]] entries from .catraz/config/warden.toml as {"host",
    "type", "actions", "allowed_projects"} dicts. Best-effort: malformed/missing
    input yields [] rather than raising — the Warden is the fail-closed side,
    doctor only warns."""
    git = _load_git_table(root)
    if git is None:
        return []
    raw_endpoints = git.get("endpoint")
    if not isinstance(raw_endpoints, list):
        return []
    endpoints: list[dict[str, Any]] = []
    for raw in raw_endpoints:
        if not isinstance(raw, dict):
            continue
        host = raw.get("host")
        if not isinstance(host, str) or not host.strip():
            continue
        endpoint_type = raw.get("type")
        endpoints.append(
            {
                "host": host.strip(),
                "type": endpoint_type if isinstance(endpoint_type, str) else "",
                "actions": _parse_actions_list(raw.get("actions")),
                "allowed_projects": _parse_projects_list(raw.get("allowed_projects")),
            }
        )
    return endpoints


def _actions_valid_for_type(endpoint_type: str) -> frozenset[str]:
    """Mirrors the Warden's per-type derivation: a "plain" endpoint only has the
    repo.* ids; everything else gets the full vocabulary. Doctor is advisory,
    so an unrecognized type falls back to the permissive case rather than raising."""
    if endpoint_type == "plain":
        return _PLAIN_ACTIONS
    return ALL_ACTIONS


def _effective_actions_for_host(
    domain_actions: tuple[str, ...] | None, endpoint: dict[str, Any]
) -> tuple[str, ...]:
    """Same cascade as the Warden's Config.effective_actions: the endpoint's own
    actions if set (returned as-is, unfiltered), else the domain default, else
    the built-in default — with only the inherited value cut down to the
    endpoint's type."""
    override = endpoint.get("actions")
    if override is not None:
        return cast(tuple[str, ...], override)
    inherited = domain_actions if domain_actions is not None else DEFAULT_ACTIONS
    valid_for_type = _actions_valid_for_type(endpoint.get("type", ""))
    return tuple(action for action in inherited if action in valid_for_type)


def check_tokens(root: Path, env: dict[str, str], f: Findings) -> None:
    """Cross-check the grouped read_tokens/write_tokens files against the
    [[git.endpoint]] hosts in warden.toml. Mirrors the Warden's
    Config.access_mode reasoning, but doctor only ever warns — the Warden is
    the side that enforces."""
    read_tokens = _read_grouped_token_file(root, "read_tokens")
    write_tokens = _read_grouped_token_file(root, "write_tokens")
    endpoints = _read_git_endpoints(root)
    endpoint_hosts = {e["host"] for e in endpoints}

    if not endpoints and not read_tokens and not write_tokens:
        f.ok("tokens", "no [[git.endpoint]] configured yet — add one to warden.toml")
        return

    # Case: a token for a host that matches no configured endpoint — probably a typo.
    for host in sorted((set(read_tokens) | set(write_tokens)) - endpoint_hosts):
        f.warn(
            "tokens",
            f"token for host {host!r} matches no [[git.endpoint]] in warden.toml "
            "— probably a typo, the Warden ignores it",
        )

    for endpoint in endpoints:
        host = endpoint["host"]
        has_read = host in read_tokens
        has_write = host in write_tokens
        if not has_read and not has_write:
            f.warn("tokens", f"endpoint {host!r} has no token — it will run closed")
            continue
        if has_write and not has_read:
            # Least-privilege: a write token is never used as a read fallback, so
            # this endpoint stays closed until a read-scoped token exists.
            f.warn(
                "tokens",
                f"endpoint {host!r} has a write token but no read token — it will "
                "run closed (least privilege: add a read-scoped token to "
                "read_tokens for this host)",
            )
            continue
        f.ok(
            "tokens",
            f"endpoint {host!r}: {'read-write' if has_write else 'read-only'} token set",
        )

    _probe_gitlab_tokens(endpoints, read_tokens, write_tokens, f)


def _gitlab_get(base: str, path: str, token: str, timeout: int = 5) -> dict[str, Any]:
    url = base.rstrip("/") + path
    req = urllib.request.Request(url, headers={"PRIVATE-TOKEN": token})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return cast(dict[str, Any], json.loads(resp.read().decode()))


def _probe_gitlab_tokens(
    endpoints: list[dict[str, Any]],
    read_tokens: dict[str, str],
    write_tokens: dict[str, str],
    f: Findings,
) -> None:
    """Best-effort online probe per configured endpoint token: catches
    expired/swapped/wrong-scope tokens, degrading silently to "not probed" when
    unreachable. Only type = "gitlab" endpoints have a REST surface to probe;
    "plain" endpoints are covered by check_tokens's presence checks instead."""
    for endpoint in endpoints:
        if endpoint.get("type") != "gitlab":
            continue
        host = endpoint["host"]
        base = f"https://{host}"
        read_t = read_tokens.get(host, "")
        write_t = write_tokens.get(host, "")

        if read_t and write_t and read_t == write_t:
            f.warn("tokens", f"{host}: READ and WRITE token are identical — likely a paste mistake")

        for label, token in (("read", read_t), ("write", write_t)):
            if not token:
                continue
            try:
                me = _gitlab_get(base, "/api/v4/personal_access_tokens/self", token)
            except urllib.error.HTTPError as e:
                if e.code == 401:  # GitLab's unambiguous "this token is invalid/expired"
                    f.bad(
                        "tokens",
                        f"{host}: {label} token rejected (401)",
                        "rotate the token — it's invalid or expired",
                    )
                else:
                    # 403/407/5xx etc. can be the proxy or a scope quirk → don't over-claim.
                    f.warn(
                        "tokens",
                        f"{host}: {label} token not probed (HTTP {e.code}) — online check "
                        "skipped (likely because you chose a fine-grained scope)",
                    )
                continue
            except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
                f.warn(
                    "tokens",
                    f"{host}: {label} token not probed ({type(e).__name__}) — offline, "
                    "check skipped",
                )
                continue
            scopes = me.get("scopes", [])
            active = me.get("active", True)
            if not active:
                f.bad("tokens", f"{host}: {label} token is inactive/revoked", "rotate the token")
                continue
            f.ok("tokens", f"{host}: {label} token valid (scopes: {', '.join(scopes) or '∅'})")
            if label == "read" and "api" in scopes:
                f.warn(
                    "tokens",
                    f"{host}: READ token carries the write 'api' scope — too broad (R6)",
                    "issue a read-only token (read_api, read_repository)",
                )
            if label == "write" and "api" not in scopes:
                f.bad(
                    "tokens",
                    f"{host}: WRITE token lacks the 'api' scope — pushes will fail",
                    "issue a token with the 'api' scope",
                )

        if write_t:
            _probe_write_user_read(host, base, write_t, f)


def _probe_write_user_read(host: str, base: str, token: str, f: Findings) -> None:
    """The warden resolves its service-account id via GET /user with the WRITE
    token, needed to enforce MR ownership (R3). Fine-grained PATs often omit
    "User: Read", so GET /user 403s and every ownership-gated write is silently
    denied while MR creation is allowed — probe it explicitly so it surfaces at setup."""
    try:
        me = _gitlab_get(base, "/api/v4/user", token)
    except urllib.error.HTTPError as e:
        if e.code == 403:
            f.bad(
                "tokens",
                f"{host}: WRITE token cannot read its own user (GET /user → 403)",
                "the warden needs this to resolve its service account and enforce MR "
                "ownership (R3) — comments and MR edits will be denied. Grant the token "
                "the 'User: Read' (read_user) permission, or use a classic api-scope PAT",
            )
        elif e.code == 401:
            f.bad(
                "tokens",
                f"{host}: WRITE token rejected on GET /user (401)",
                "rotate the token — it's invalid or expired",
            )
        else:
            f.warn(
                "tokens",
                f"{host}: WRITE token GET /user not probed (HTTP {e.code}) — online check skipped",
            )
        return
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
        f.warn(
            "tokens",
            f"{host}: WRITE token GET /user not probed ({type(e).__name__}) — offline, "
            "check skipped",
        )
        return
    uid = me.get("id")
    if uid is not None:
        f.ok(
            "tokens",
            f"{host}: WRITE token resolves its service account (GET /user → "
            f"@{me.get('username')}, id {uid})",
        )
    else:
        f.warn(
            "tokens",
            f"{host}: WRITE token GET /user returned no id — MR ownership checks (R3) may fail",
        )


def check_policy(root: Path, env: dict[str, str], f: Findings) -> None:
    """Fast per-endpoint pre-check of allowed_projects. Authoritative validation
    stays the warden reconcile — this just turns the obvious traps loud before
    start; each host's allowlist is checked in isolation, never merged with
    another host's."""
    from catraz.policy import validate_project

    endpoints = _read_git_endpoints(root)
    if not endpoints:
        f.ok("policy", "no [[git.endpoint]] configured — allowlist not required")
        return
    for endpoint in endpoints:
        host = endpoint["host"]
        resolved = endpoint["allowed_projects"]
        if not resolved:
            f.warn(
                "policy",
                f"{host}: allowed_projects empty",
                "stack still starts (offline work OK); every GitLab op is "
                "denied on this host until you add a project",
            )
            continue
        bad = []
        for p in resolved:
            reason = validate_project(p)
            if reason:
                bad.append(f"{p!r} ({reason})")
        if bad:
            f.bad(
                "policy",
                f"{host}: invalid allowed_projects: " + "; ".join(bad),
                "each entry must be a full project path, no wildcards/leaf/group-prefix",
            )
        else:
            f.ok("policy", f"{host}: {len(resolved)} allowed project(s)")


def check_egress(root: Path, env: dict[str, str], f: Findings) -> None:
    """The effective Squid egress allowlist with per-domain provenance — shipped
    baseline, an `# agent:<profile>` block, or a manual operator edit. Advisory:
    Squid enforces the file, doctor only surfaces how it was assembled. Distinct
    from the warden project allowlist reported by the `policy` section."""
    from catraz.egress_allowlist import classify_domains

    allowlist = root / ".catraz" / "config" / "allowlist.txt"
    if not allowlist.exists():
        f.bad("egress", "allowlist.txt missing", "run `catraz init`")
        return
    text = allowlist.read_text(encoding="utf-8")
    baseline = (paths.asset_root() / "assets" / "config" / "allowlist.txt").read_text(
        encoding="utf-8"
    )
    entries = classify_domains(text, baseline)
    if not entries:
        f.warn("egress", "no domains allowed — the agent cannot reach any network host")
        return
    for entry in entries:
        f.ok("egress", f"{entry.entry} [{entry.provenance}]")


def check_endpoints(root: Path, env: dict[str, str], f: Findings) -> None:
    """Effective endpoint-catalog table, per host: default set + activations for
    each configured [[git.endpoint]]. Fetched from the running warden's /policy
    admin route, so this section needs the stack up (unlike
    check_action_coherence, which does a static, host-side parse of warden.toml)."""
    from catraz.admin_client import AdminUnreachable
    from catraz.endpoints import fetch_policy_report

    if not _read_git_endpoints(root):
        f.ok("endpoints", "no [[git.endpoint]] configured — endpoint catalog not applicable")
        return
    try:
        report = fetch_policy_report(root)
    except AdminUnreachable as exc:
        f.warn(
            "endpoints",
            f"activation state unknown ({exc})",
            "start the stack (`catraz run` / `catraz up`) to see it",
        )
        return
    hosts = report["hosts"]
    if not hosts:
        f.ok("endpoints", "no hosts configured yet")
        return
    for host, host_report in hosts.items():
        active_ids: list[str] = host_report["actions"]
        denials: list[str] = host_report.get("denials", [])
        default_by_id: dict[str, bool] = {}
        all_ids: set[str] = set()
        for row in host_report["catalog"]:
            for a in row["actions"]:
                all_ids.add(a["id"])
                default_by_id.setdefault(a["id"], a["default"])
        active_desc = ", ".join(
            aid if default_by_id.get(aid, True) else f"{aid}[config]" for aid in active_ids
        )
        f.ok("endpoints", f"{host}: {len(active_ids)} active: {active_desc or '(none)'}")
        inactive = sorted(all_ids - set(active_ids) - set(denials))
        if inactive:
            f.ok(
                "endpoints",
                f"{host}: {len(inactive)} in catalog but not enabled: {', '.join(inactive)}",
            )
        if denials:
            f.ok(
                "endpoints",
                f"{host}: {len(denials)} compiled-in denial(s), never configurable: "
                f"{', '.join(sorted(denials))}",
            )


def check_action_coherence(root: Path, env: dict[str, str], f: Findings) -> None:
    """Cross-checks between a host's effective actions and its tokens/other
    actions — always WARN, never BAD (coherence traps, not security problems).
    Parses warden.toml/token files with the same cascade as the Warden so the
    two never drift; flags write actions with no write_token, and
    mr.create/ci.trigger configured without a branch-write action to source from."""
    endpoints = _read_git_endpoints(root)
    if not endpoints:
        return
    domain_actions = _read_git_actions_default(root)
    write_tokens = _read_grouped_token_file(root, "write_tokens")

    for endpoint in endpoints:
        host = endpoint["host"]
        actions = set(_effective_actions_for_host(domain_actions, endpoint))

        write_actions = sorted(actions & WRITE_ACTIONS)
        if write_actions and host not in write_tokens:
            f.warn(
                "endpoints",
                f"{host}: write action(s) configured ({', '.join(write_actions)}) but no "
                "write_token for this host — the endpoint is effectively read-only",
                f"add a write_token entry for {host!r} to .catraz/secrets/write_tokens, "
                "or drop the write action(s) from its `actions`",
            )

        if "project.mr.create" in actions and not (actions & _BRANCH_WRITE_ACTIONS):
            f.warn(
                "endpoints",
                f"{host}: `project.mr.create` is configured without `repo.branch.create`/"
                "`repo.branch.push` — the MR's source branch can never reach the remote",
                "add `repo.branch.create` or `repo.branch.push` to this host's `actions` "
                "(or drop `project.mr.create`)",
            )

        if "project.ci.trigger" in actions and not (actions & _BRANCH_WRITE_ACTIONS):
            f.warn(
                "endpoints",
                f"{host}: `project.ci.trigger` is configured without `repo.branch.create`/"
                "`repo.branch.push` — there is no branch to trigger a pipeline for",
                "add `repo.branch.create` or `repo.branch.push` to this host's `actions` "
                "(or drop `project.ci.trigger`)",
            )


def check_agent(root: Path, env: dict[str, str], f: Findings) -> None:
    """Active agent profile + credential-mode consistency check: "sync" checks
    the sandbox seed is present and not root-owned (Docker auto-creates a
    root-owned bind target when the source is missing); "persistent" checks
    the state dir at mode 0700. The mode is CLAUDE_CREDENTIALS_MODE from .env
    when valid, else the manifest default."""
    from catraz.agents import (
        CREDENTIALS_MODES,
        effective_credentials_mode,
        load_manifest,
        resolve_agent_profile,
    )
    from catraz.errors import CliError as _CliError
    from catraz.paths import agent_state_dir, claude_home

    try:
        profile = resolve_agent_profile(root)
        manifest = load_manifest(profile)
    except _CliError as e:
        f.bad("agent", str(e))
        return
    f.ok("agent", f"profile: {profile} (command: {manifest.command})")

    raw_mode = env.get("CLAUDE_CREDENTIALS_MODE", "").strip()
    if raw_mode and raw_mode not in CREDENTIALS_MODES:
        f.bad(
            "agent",
            "CLAUDE_CREDENTIALS_MODE must be persistent|sync",
            "set it in .catraz/.env",
        )
    creds_mode = effective_credentials_mode(root, env)
    f.ok("agent", f"credentials mode: {creds_mode}")

    if creds_mode == "persistent":
        state_dir = agent_state_dir(root, profile)
        if not state_dir.is_dir():
            f.bad(
                "agent",
                f"{state_dir} missing",
                "run `catraz init` or `catraz doctor --fix`",
            )
            return
        mode = state_dir.stat().st_mode & 0o777
        if host_uid() is None:
            f.ok("agent", f"{state_dir} present")  # modes are synthetic — check_env warns
        elif mode != 0o700:
            f.bad(
                "agent",
                f"{state_dir} has mode {oct(mode)}, expected 0700",
                f"chmod 0700 {state_dir}",
            )
        else:
            f.ok("agent", f"{state_dir} present (0700)")
        if (state_dir / ".credentials.json").exists():
            f.ok("agent", "persistent credential present")
        else:
            f.ok(
                "agent",
                "no persistent credential yet — `claude login` inside the container",
            )
        return

    home = claude_home(root)
    creds = home / ".credentials.json"
    uid = host_uid()
    # Where st_uid is synthetic (always 0) this says nothing — skip rather than guess.
    if uid is not None and uid != 0 and home.exists() and home.stat().st_uid == 0:
        f.bad(
            "agent",
            f"{home} owned by root (Docker auto-created it)",
            f"sudo rm -rf {home} && mkdir -p {home} && catraz sync",
        )
        return
    if not creds.exists():
        f.bad("agent", f"no sandbox credential in {home}", "run `catraz sync`")
    else:
        f.ok("agent", "sandbox credential present")


def check_net(root: Path, f: Findings) -> None:
    # Admin/audit is reachable over a per-project unix socket under
    # .catraz/state/warden/run/. The socket file only exists while the stack runs.
    sock = root / ".catraz" / "state" / "warden" / "run" / "admin.sock"
    if sock.exists():
        f.ok("net", "admin socket present (stack up)")
    else:
        f.ok("net", "admin socket absent (stack down — start with `catraz run`)")


# Bind mounts a running infra container reads/writes at a fixed path —
# (host path relative to .catraz, container-absolute path) pairs.
MOUNT_TARGETS: dict[str, list[tuple[str, str]]] = {
    "gitlab-warden": [
        ("state/warden/db", "/var/lib/warden"),
        ("logs/warden", "/var/log/warden"),
        ("state/warden/run", "/run/warden"),
        ("config/warden.toml", "/etc/warden/warden.toml"),
    ],
    "forward-proxy": [
        ("logs/squid", "/var/log/squid"),
        ("config/squid.conf", "/etc/squid/squid.conf"),
        ("config/allowlist.txt", "/etc/squid/allowlist.txt"),
    ],
}


def _container_inode(name: str, path: str) -> int | None:
    """Inode of `path` inside container `name`, or None if unreachable."""
    r = subprocess.run(
        ["docker", "exec", name, "stat", "-c", "%i", path],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return None
    try:
        return int(r.stdout.strip())
    except ValueError:
        return None


def check_mounts(root: Path, f: Findings) -> None:
    """A host path deleted and recreated at the same location while its
    container keeps running orphans the bind mount: the container keeps
    seeing the old, unlinked file or directory. Compares each mount's
    host-side inode against the inode the running container sees; a
    mismatch flags the mount as stale."""
    if not which("docker"):
        f.warn("mounts", "cannot verify bind mounts (docker missing)")
        return
    rows = compose_ps(root)
    running = {r.get("Service"): r.get("Name") for r in rows}
    if not running:
        f.ok("mounts", "stack not running — nothing to verify")
        return
    for service, targets in MOUNT_TARGETS.items():
        name = running.get(service)
        if not name:
            continue
        for rel, container_path in targets:
            host_path = root / ".catraz" / rel
            if not host_path.exists():
                continue
            container_inode = _container_inode(name, container_path)
            if container_inode is None:
                f.warn("mounts", f"{service}: could not stat {container_path} in container")
                continue
            if host_path.stat().st_ino != container_inode:
                f.bad(
                    "mounts",
                    f"{service}: {rel} bind mount is stale (host path was "
                    "recreated while the container kept running)",
                    "catraz reload --force",
                )
            else:
                f.ok("mounts", f"{service}: {rel} bind mount intact")


def _read_secret_file(root: Path, filename: str) -> str:
    """Return the stripped contents of .catraz/secrets/<filename>, or '' if missing/empty."""
    p = root / ".catraz" / "secrets" / filename
    try:
        return p.read_text(encoding="utf-8").strip() if p.exists() else ""
    except OSError:
        return ""


def check_auth(root: Path, env: dict[str, str], f: Findings) -> None:
    # Canonical rule (matches auth.auth_mode): absent/empty → subscription; only a
    # present-but-invalid value is an error.
    mode = env.get("AUTH_MODE") or "subscription"
    if mode not in ("subscription", "api_key"):
        f.bad("auth", "AUTH_MODE must be subscription|api_key", "set it in .catraz/.env")
        return
    from catraz.agents import effective_credentials_mode

    cred = paths.claude_home(root) / ".credentials.json"
    # api_key: key is in .catraz/secrets/anthropic_api_key (compose secret); bare env var is fallback.
    api_key = _read_secret_file(root, "anthropic_api_key") or env.get("ANTHROPIC_API_KEY", "")
    if mode == "subscription":
        if api_key:
            f.bad("auth", "subscription mode but ANTHROPIC_API_KEY set", "unset it")
        if effective_credentials_mode(root, env) == "persistent":
            # Persistent home keeps the login inside the container
            # (.catraz/state/<profile>/); the `agent` section validates it and
            # `catraz sync` does not apply.
            f.ok("auth", "subscription — credential managed in the persistent container home")
        elif not cred.exists():
            f.bad("auth", "no .credentials.json", "run `catraz sync`")
        else:
            f.ok("auth", "subscription credential present")
            f.warn(
                "auth",
                "subscription token refreshes are not persisted across restarts "
                "— re-run `catraz sync` if auth breaks",
            )
    else:
        if not api_key:
            f.bad(
                "auth",
                "api_key mode but ANTHROPIC_API_KEY empty",
                "set it in .catraz/secrets/anthropic_api_key or .catraz/.env",
            )
        if cred.exists():
            f.bad(
                "auth",
                "api_key mode but .credentials.json present (ambiguous)",
                f"remove {paths.claude_home(root) / '.credentials.json'}",
            )
        if api_key and not cred.exists():
            f.ok("auth", "api_key set")


def check_base(root: Path, env: dict[str, str], f: Findings) -> None:
    if not which("docker"):
        f.warn("base", "docker missing — base not checked")
        return
    try:
        base = image.resolve_base(root)
    except CliError as e:
        f.bad("base", str(e))
        return
    contract = subprocess.run(
        ["docker", "run", "--rm", base, "sh", "-c", "command -v apt-get"],
        capture_output=True,
        text=True,
    )
    if contract.returncode != 0:
        f.bad("base", "base lacks apt-get", "base contract: Debian/Ubuntu")
    else:
        f.ok("base", f"base contract ok ({base})")
    setuid = subprocess.run(
        ["docker", "run", "--rm", base, "find", "/", "-perm", "/6000", "-type", "f"],
        capture_output=True,
        text=True,
    )
    extra = [ln for ln in setuid.stdout.split() if ln]
    if extra:
        # Distro-shipped setuid/setgid binaries, rendered inert by the agent's
        # no-new-privileges security_opt (enforced by compose.assert_invariants).
        f.ok(
            "base",
            f"{len(extra)} setuid/setgid binaries in base — neutralized by no-new-privileges",
        )


def run_doctor(root: Path, only: list[str] | None = None, fix: bool = False) -> Findings:
    env: dict[str, str] = load_env(root / ".catraz" / ".env")
    f = Findings()
    sections = only or DOCTOR_SECTIONS
    if fix:
        _doctor_fix(root, env)
    if "docker" in sections:
        check_docker(f)
    if "compose" in sections:
        check_compose(root, env, f)
    if "env" in sections:
        check_env(root, env, f)
    if "tokens" in sections:
        check_tokens(root, env, f)
    if "policy" in sections:
        check_policy(root, env, f)
    if "endpoints" in sections:
        check_endpoints(root, env, f)
        check_action_coherence(root, env, f)
    if "egress" in sections:
        check_egress(root, env, f)
    if "agent" in sections:
        check_agent(root, env, f)
    if "net" in sections:
        check_net(root, f)
    if "mounts" in sections:
        check_mounts(root, f)
    if "auth" in sections:
        check_auth(root, env, f)
    if "base" in sections:
        check_base(root, env, f)
    return f


def _wants_ro_credential_seed(root: Path, env: dict[str, str]) -> bool:
    """True when the sync credential seed under secrets/claude is actually used:
    subscription auth with credentials mode "sync". Fail closed (no scaffold) if
    the mode is unresolvable — persistent, the default, needs no seed."""
    if (env.get("AUTH_MODE") or "subscription") != "subscription":
        return False
    from catraz.agents import effective_credentials_mode

    try:
        return effective_credentials_mode(root, env) == "sync"
    except CliError:
        return False


def _doctor_fix(root: Path, env: dict[str, str]) -> None:
    """Repair only the safe things: missing dirs + chown. Never secrets/policy."""
    dev_uid = env.get("DEV_UID", "")
    cat = root / ".catraz"
    # .catraz/ itself first — on a fresh init it does not exist yet, and the 0700 secrets
    # dirs below use mode= (not parents=) so they cannot create it implicitly.
    cat.mkdir(parents=True, exist_ok=True)
    # secrets/ and secrets/claude must be created at 0700 before the generic loop,
    # else mkdir(parents=True) there would create secrets/ at umask default (0755).
    secrets_dir = cat / "secrets"
    secrets_dir.mkdir(mode=0o700, exist_ok=True)
    secrets_dir.chmod(0o700)
    # secrets/claude holds the read-only host-credential seed used only by the
    # sync credential mode; persistent keeps the login in state/<profile>/, so
    # scaffold it only when a sync subscription setup will actually mount it.
    if _wants_ro_credential_seed(root, env):
        claude_secrets = secrets_dir / "claude"
        claude_secrets.mkdir(mode=0o700, parents=True, exist_ok=True)
        claude_secrets.chmod(0o700)
    # The active agent profile's persistent-state + debug-log dirs; best-effort
    # default ("claude") if AGENT_PROFILE is unresolvable — check_agent validates.
    profile = (env.get("AGENT_PROFILE") or "claude").strip() or "claude"
    agent_state = cat / "state" / profile
    agent_state.mkdir(mode=0o700, parents=True, exist_ok=True)
    agent_state.chmod(0o700)
    for d in [
        "config",
        "state/warden/db",
        "state/warden/run",
        "logs/warden",
        "logs/squid",
        f"logs/{profile}",
    ]:
        (cat / d).mkdir(parents=True, exist_ok=True)
    mode = env.get("AUTH_MODE") or "subscription"
    # read_tokens/write_tokens: grouped, host-keyed token files doctor/Warden
    # read host -> token from. Scaffolded so init always leaves a parseable, empty pair.
    secret_files = ["read_tokens", "write_tokens"]
    if mode == "api_key":
        secret_files.append("anthropic_api_key")
    for filename in secret_files:
        p = secrets_dir / filename
        if not p.exists():
            p.write_text("")
            p.chmod(0o600)
    # Without a host uid there is no ownership to repair: the mounts carry
    # synthetic ownership and os.chown does not exist on such a host.
    if dev_uid.isdigit() and host_uid() is not None:
        for d in ["state", "logs"]:
            try:
                _chown_r(cat / d, int(dev_uid))
            except PermissionError:
                pass  # surfaced as a finding by check_env; --fix is best-effort


def _chown_r(path: Path, uid: int) -> None:
    os.chown(path, uid, -1)
    for p in path.rglob("*"):
        os.chown(p, uid, -1)


def print_findings(f: Findings, out: Any) -> tuple[int, int]:
    glyph = {OK: out.green("✔"), WARN: out.yellow("▲"), BAD: out.red("✘")}
    cur = None
    for level, section, msg, hint in f.items:
        if section != cur:
            out.head(f"\n[{section}]")
            cur = section
        print(f"  {glyph[level]} {msg}")
        if hint and level != OK:
            print(f"      {out.dim('↳ ' + hint)}")
    bad = sum(1 for i in f.items if i[0] == BAD)
    warn = sum(1 for i in f.items if i[0] == WARN)
    okc = sum(1 for i in f.items if i[0] == OK)
    print()
    summary = f"{okc} ok · {warn} warning(s) · {bad} problem(s)"
    print(out.green(summary) if bad == 0 else out.red(summary))
    return bad, warn
