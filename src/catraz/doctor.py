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
from catraz.compose import run as compose_run
from catraz.errors import CliError
from catraz import paths
from catraz import image

# Secrets the wizard collects (filename, human prompt, description). Order matters.
# Secrets live in .catraz/secrets/<filename> (mode 0600), mounted into the warden
# via compose secrets: at /run/secrets/<filename>. Never stored in .env.
#
# Legacy single-target pair — kept (not the new multi-target model doctor's own
# checks now use, see check_tokens/read_tokens/write_tokens below) because
# `assets/compose/docker-compose.yml` still mounts exactly these two files as
# compose secrets for the single-target Warden; that cutover is
# `07-compose-and-agent-routing.md`'s job, not this step's. See "## Status"
# in `06-cli-doctor-init.md`.
SECRETS = [
    (
        "gitlab_read_token",
        "GitLab READ token (scopes: read_api, read_repository)",
        "GitLab READ token",
    ),
    (
        "gitlab_write_token",
        "GitLab WRITE token (classic 'api' scope, or fine-grained + 'User: Read')",
        "GitLab WRITE token",
    ),
]

OK, WARN, BAD = "ok", "warn", "bad"

DOCTOR_SECTIONS = [
    "docker",
    "compose",
    "env",
    "tokens",
    "policy",
    "endpoints",
    "agent",
    "net",
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
    config. It's unconditional now (no profile gate), so its absence means someone
    edited it out; agent depends_on gitlab-warden, so the stack would fail closed."""
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
    write_dirs = [root / ".catraz" / "state", root / ".catraz" / "logs"]
    for d in write_dirs:
        if not d.exists():
            f.bad(
                "env",
                f"{d.name}/ missing",
                "run `catraz init` or `catraz doctor --fix`",
            )
            continue
        if dev_uid.isdigit():
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


def _gitlab_mode(env: dict[str, str]) -> str:
    return (env.get("GITLAB_MODE") or "read-write").strip()


def check_gitlab(env: dict[str, str], f: Findings) -> None:
    mode = _gitlab_mode(env)
    if mode == "off":
        f.ok("tokens", "GitLab disabled (GITLAB_MODE=off)")
        return
    url = (env.get("GITLAB_URL") or "").strip()
    if not url:
        f.warn(
            "tokens",
            "GITLAB_URL unset — defaulting to https://gitlab.com",
            "set GITLAB_URL in .catraz/.env for self-hosted GitLab",
        )
    else:
        f.ok("tokens", f"GitLab endpoint: {url}")


def _parse_grouped_tokens(text: str) -> dict[str, str]:
    """Parse a grouped ``read_tokens``/``write_tokens`` file into ``host -> token``.

    Same splitting rule as the Warden's Step 02 ``_parse_token_file``
    (``warden/warden/core/config_load.py``): split on the first run of
    whitespace, ``#``-comments and blank lines are skipped. Unlike the Warden,
    a malformed line is skipped rather than raising — doctor warns, it never
    aborts (see module docstring / "Nicht tun" in
    ``docs/design/architecture-generalization/08-multi-target/06-cli-doctor-init.md``).
    """
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
    """``host -> token`` map from ``.catraz/secrets/<filename>`` (``read_tokens``
    or ``write_tokens``), or ``{}`` if the file is missing/unreadable."""
    p = root / ".catraz" / "secrets" / filename
    try:
        text = p.read_text(encoding="utf-8") if p.exists() else ""
    except OSError:
        text = ""
    return _parse_grouped_tokens(text)


def _read_git_endpoints(root: Path) -> list[dict[str, str]]:
    """``[[git.endpoint]]`` entries from ``.catraz/config/warden.toml``, as
    ``{"host": ..., "type": ...}`` dicts.

    Host-side and best-effort only: an absent file, unreadable file, invalid
    TOML, or a missing/malformed ``[git.endpoint]`` array all yield ``[]``
    (nothing to cross-check against) rather than raising — the Warden is the
    fail-closed side that aborts startup on a genuinely malformed config
    (see ``01-config-schema.md``); doctor only warns.
    """
    toml_path = root / ".catraz" / "config" / "warden.toml"
    try:
        text = toml_path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    git = data.get("git")
    if not isinstance(git, dict):
        return []
    raw_endpoints = git.get("endpoint")
    if not isinstance(raw_endpoints, list):
        return []
    endpoints: list[dict[str, str]] = []
    for raw in raw_endpoints:
        if not isinstance(raw, dict):
            continue
        host = raw.get("host")
        if not isinstance(host, str) or not host.strip():
            continue
        endpoint_type = raw.get("type")
        endpoints.append(
            {"host": host.strip(), "type": endpoint_type if isinstance(endpoint_type, str) else ""}
        )
    return endpoints


def check_tokens(root: Path, env: dict[str, str], f: Findings) -> None:
    """Cross-check the grouped ``read_tokens``/``write_tokens`` files against
    the ``[[git.endpoint]]`` hosts configured in ``warden.toml`` (§4, §6 of
    ``docs/design/architecture-generalization/08-multi-target.md``).

    This is the host-side, friendly/explaining mirror of the Warden's
    ``Config.access_mode(host)`` (``warden/warden/core/config.py``, Step 02):
    same reasoning, same threshold, so the two sides don't drift — but doctor
    only ever **warns**; the Warden is the side that actually enforces
    (fail-closed, per-endpoint-degrade) an inconsistency. `doctor` never fails
    the run because of a token/endpoint mismatch.
    """
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
            # Least-privilege rationale — identical wording/reasoning to the Warden's
            # Step 02 warning (config_load._warn_closed_endpoints): a write token is
            # never used as a read fallback, so this endpoint is closed until a
            # read-scoped token exists.
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
    endpoints: list[dict[str, str]],
    read_tokens: dict[str, str],
    write_tokens: dict[str, str],
    f: Findings,
) -> None:
    """Best-effort online probe, per configured endpoint token (lifted from the
    old two-fixed-token probe, §6 point 3): catch expired/swapped/wrong-scope
    tokens, one host at a time. Degrades silently to "not probed" when a host
    can't be reached — never a hard failure.

    Only ``type = "gitlab"`` endpoints have a defined REST surface to probe
    (``personal_access_tokens/self``, ``/user``); ``plain`` endpoints are
    git-smart-HTTP-only and are not probed online — the token-presence checks
    in :func:`check_tokens` already cover them.
    """
    for endpoint in endpoints:
        if endpoint.get("type") != "gitlab":
            continue
        host = endpoint["host"]
        base = f"https://{host}"
        read_t = read_tokens.get(host, "")
        write_t = write_tokens.get(host, "")

        if read_t and write_t and read_t == write_t:
            f.warn(
                "tokens", f"{host}: READ and WRITE token are identical — likely a paste mistake"
            )

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
    """The warden resolves its service-account id via `GET /user` with the WRITE
    token, and needs that id to enforce MR ownership (R3: comment / edit / close
    only your own MRs). Fine-grained PATs omit the **User: Read** permission by
    default, so `GET /user` 403s, the warden's service_account_id stays null, and
    every ownership-gated write is denied R3 — while MR *creation* still works
    (it only checks the branch prefix). That asymmetry is a silent runtime trap;
    probe it explicitly so it surfaces at setup. Degrades quietly when offline."""
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
    """Fast pre-check of allowed_projects. Authoritative validation stays the
    warden reconcile — this just turns the obvious traps loud before start."""
    mode = _gitlab_mode(env)
    if mode == "off":
        f.ok("policy", "GitLab off — allowlist not required")
        return
    from catraz.policy import _resolve_allowed_projects, validate_project

    resolved, source = _resolve_allowed_projects(root, env)
    if not resolved:
        f.warn(
            "policy",
            f"allowed_projects empty (source: {source})",
            "stack still starts (offline work OK); every GitLab op is denied "
            "until you add a project",
        )
        return
    bad = []
    for p in resolved:
        reason = validate_project(p)
        if reason:
            bad.append(f"{p!r} ({reason})")
    if bad:
        f.bad(
            "policy",
            "invalid allowed_projects: " + "; ".join(bad),
            "each entry must be a full project path, no wildcards/leaf/group-prefix",
        )
    else:
        f.ok("policy", f"{len(resolved)} allowed project(s) [{source}]")


def check_endpoints(root: Path, env: dict[str, str], f: Findings) -> None:
    """Effective endpoint-catalog table (§04.3): default set + activations.

    A thin section — the actual report is fetched from the running warden's
    read-only ``/policy`` admin route and formatted by ``catraz.endpoints``;
    doctor.py only wires it into the Findings list, so this module doesn't
    grow with the catalog (see ``catraz.endpoints.fetch_policy_report``).
    """
    from catraz.admin_client import AdminUnreachable
    from catraz.endpoints import fetch_policy_report

    mode = _gitlab_mode(env)
    if mode == "off":
        f.ok("endpoints", "GitLab off — endpoint catalog not applicable")
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
    rows = report["catalog"]
    active = [r for r in rows if r["active"]]
    inactive = [r for r in rows if not r["active"]]
    active_desc = ", ".join(
        f"{r['id']}[{r['enabled_via']}]" if r["enabled_via"] != "default" else r["id"]
        for r in active
    )
    f.ok("endpoints", f"{len(active)} active: {active_desc or '(none)'}")
    if inactive:
        f.ok(
            "endpoints",
            f"{len(inactive)} in catalog but not enabled: {', '.join(r['id'] for r in inactive)}",
        )


def check_agent(root: Path, env: dict[str, str], f: Findings) -> None:
    """§05.3/§05.6 — active agent profile + credential-mode consistency.

    `credentials.mode = "sync"` keeps the pre-Schritt-7 check (sandbox seed
    present, not root-owned — the trap `entrypoint.py` hard-coded: Docker
    auto-creates a bind target as root when the source file is missing).
    `credentials.mode = "persistent"` (claude's default, §05.6) instead
    checks the per-repo state dir itself: present, mode 0700.
    """
    from catraz.agents import load_manifest, resolve_agent_profile
    from catraz.errors import CliError as _CliError
    from catraz.paths import agent_state_dir, claude_home

    try:
        profile = resolve_agent_profile(root)
        manifest = load_manifest(profile)
    except _CliError as e:
        f.bad("agent", str(e))
        return
    f.ok("agent", f"profile: {profile} (command: {manifest.command})")

    if manifest.credentials_mode == "persistent":
        state_dir = agent_state_dir(root, profile)
        if not state_dir.is_dir():
            f.bad(
                "agent",
                f"{state_dir} missing",
                "run `catraz init` or `catraz doctor --fix`",
            )
            return
        mode = state_dir.stat().st_mode & 0o777
        if mode != 0o700:
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
    if home.exists() and home.stat().st_uid == 0 and os.getuid() != 0:
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
    # Admin/audit moved from TCP (172.31.0.2:9090) to a per-project unix socket
    # under .catraz/state/warden/run/. The socket file only exists while the stack runs.
    sock = root / ".catraz" / "state" / "warden" / "run" / "admin.sock"
    if sock.exists():
        f.ok("net", "admin socket present (stack up)")
    else:
        f.ok("net", "admin socket absent (stack down — start with `catraz run`)")


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
    cred = paths.claude_home(root) / ".credentials.json"
    # api_key: key is in .catraz/secrets/anthropic_api_key (compose secret); bare env var is fallback.
    api_key = _read_secret_file(root, "anthropic_api_key") or env.get("ANTHROPIC_API_KEY", "")
    if mode == "subscription":
        if api_key:
            f.bad("auth", "subscription mode but ANTHROPIC_API_KEY set", "unset it")
        if not cred.exists():
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
        # These are distro-shipped setuid/setgid binaries (passwd, su, mount, …). They are
        # rendered inert by the agent's `no-new-privileges` security_opt, which is enforced
        # non-bypassably by compose.assert_invariants on every up/run — so this is informational,
        # not a warning. If that invariant were dropped, up/run would fail loudly, not here.
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
        check_gitlab(env, f)
    if "tokens" in sections:
        check_tokens(root, env, f)
    if "policy" in sections:
        check_policy(root, env, f)
    if "endpoints" in sections:
        check_endpoints(root, env, f)
    if "agent" in sections:
        check_agent(root, env, f)
    if "net" in sections:
        check_net(root, f)
    if "auth" in sections:
        check_auth(root, env, f)
    if "base" in sections:
        check_base(root, env, f)
    return f


def _doctor_fix(root: Path, env: dict[str, str]) -> None:
    """Repair only the safe things: missing dirs + chown. Never secrets/policy."""
    dev_uid = env.get("DEV_UID", "")
    cat = root / ".catraz"
    # .catraz/ itself first — on a fresh init it does not exist yet, and the 0700 secrets
    # dirs below use mode= (not parents=) so they cannot create it implicitly.
    cat.mkdir(parents=True, exist_ok=True)
    # secrets/ and secrets/claude must be created at 0700 BEFORE the generic loop, because
    # mkdir(parents=True) in the loop would create secrets/ at the umask default (0755) and
    # a later chmod on an already-existing dir is a no-op for mode.
    secrets_dir = cat / "secrets"
    secrets_dir.mkdir(mode=0o700, exist_ok=True)
    secrets_dir.chmod(0o700)
    claude_secrets = cat / "secrets" / "claude"
    claude_secrets.mkdir(mode=0o700, parents=True, exist_ok=True)
    claude_secrets.chmod(0o700)
    # §05.3/§05.6: the active agent profile's persistent-state + debug-log dirs.
    # Best-effort default ("claude") if AGENT_PROFILE is unset/unresolvable —
    # `check_agent` is the authoritative validator, this is just dir plumbing.
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
    # `read_tokens`/`write_tokens` (§4.1, §6): the grouped, multi-endpoint token
    # files `doctor`/the Warden read `host -> token` from. Scaffolded unconditionally
    # (like the legacy per-token SECRETS below) so `catraz init` always leaves a
    # parseable, empty pair behind, mode 0600, ready for `<host> <token>` lines.
    secret_files = ["read_tokens", "write_tokens"] + [f for f, _, _ in SECRETS]
    if mode == "api_key":
        secret_files.append("anthropic_api_key")
    for filename in secret_files:
        p = secrets_dir / filename
        if not p.exists():
            p.write_text("")
            p.chmod(0o600)
    if dev_uid.isdigit():
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
