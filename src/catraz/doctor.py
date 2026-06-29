"""Findings + Checks."""
import json
import os
import shutil
import subprocess
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
SECRETS = [
    ("gitlab_read_token", "GitLab READ token (scopes: read_api, read_repository)", "GitLab READ token"),
    ("gitlab_write_token", "GitLab WRITE token (scopes: api — service account / Developer)", "GitLab WRITE token"),
]

OK, WARN, BAD = "ok", "warn", "bad"

DOCTOR_SECTIONS = ["docker", "compose", "env", "tokens", "policy", "claude", "net", "auth", "base"]
# Sections that gate the trust boundary — `up` always runs these, no opt-out.
SECURITY_SECTIONS = ["docker", "compose", "env", "policy", "auth"]


class Findings:
    def __init__(self) -> None:
        self.items: list[tuple[str, str, str, str | None]] = []

    def add(self, level: str, section: str, msg: str, hint: str | None = None) -> None:
        self.items.append((level, section, msg, hint))

    def ok(self, sec: str, msg: str) -> None: self.add(OK, sec, msg)
    def warn(self, sec: str, msg: str, hint: str | None = None) -> None: self.add(WARN, sec, msg, hint)
    def bad(self, sec: str, msg: str, hint: str | None = None) -> None: self.add(BAD, sec, msg, hint)


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
    r = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        f.bad("docker", "Docker daemon not reachable", "start Docker (`systemctl start docker`)")
    else:
        ver = _parse_version(r.stdout.strip())
        if ver is None or ver < MIN_ENGINE:
            f.bad("docker", f"Docker Engine {r.stdout.strip()!r} < {MIN_ENGINE[0]}.{MIN_ENGINE[1]} required",
                  f"upgrade Docker Engine to ≥ {MIN_ENGINE[0]}.{MIN_ENGINE[1]}")
        else:
            f.ok("docker", f"Docker Engine {r.stdout.strip()} (≥ {MIN_ENGINE[0]}.{MIN_ENGINE[1]} ✔)")
    r = subprocess.run(["docker", "compose", "version", "--short"], capture_output=True, text=True)
    if r.returncode != 0:
        f.bad("docker", "Compose v2 missing", "install the `docker compose` plugin")
    else:
        ver = _parse_version(r.stdout.strip())
        if ver is None or ver < MIN_COMPOSE:
            f.bad("docker", f"Compose {r.stdout.strip()!r} < {MIN_COMPOSE[0]}.{MIN_COMPOSE[1]} required",
                  f"upgrade Docker Compose plugin to ≥ {MIN_COMPOSE[0]}.{MIN_COMPOSE[1]}")
        else:
            f.ok("docker", f"Compose {r.stdout.strip()} (≥ {MIN_COMPOSE[0]}.{MIN_COMPOSE[1]} ✔)")


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
        f.bad("compose", "gitlab-warden missing from the compose config",
              "the warden is the trust boundary — it must always be a service")
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
            f.bad("env", f"{d.name}/ missing", "run `catraz init` or `catraz doctor --fix`")
            continue
        if dev_uid.isdigit():
            owner = d.stat().st_uid
            if owner != int(dev_uid):
                f.bad(
                    "env", f"{d.name}/ owned by uid {owner}, DEV_UID={dev_uid}",
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
        f.warn("tokens", "GITLAB_URL unset — defaulting to https://gitlab.com",
               "set GITLAB_URL in .catraz/.env for self-hosted GitLab")
    else:
        f.ok("tokens", f"GitLab endpoint: {url}")


def check_tokens(root: Path, env: dict[str, str], f: Findings) -> None:
    mode = _gitlab_mode(env)
    secrets_dir = root / ".catraz" / "secrets"

    def _read_token(filename: str) -> str:
        p = secrets_dir / filename
        try:
            return p.read_text(encoding="utf-8").strip() if p.exists() else ""
        except OSError:
            return ""

    if mode == "off":
        f.ok("tokens", "GitLab off — tokens not required")
        return

    if mode == "read-only":
        read_t = _read_token("gitlab_read_token")
        write_t = _read_token("gitlab_write_token")
        if not read_t:
            f.bad("tokens", "gitlab_read_token is empty", "run `catraz init`")
            return
        if write_t:
            f.warn("tokens", "write token set but GITLAB_MODE=read-only — it will be ignored")
        f.ok("tokens", "read token is set")
        _probe_gitlab_tokens(root, env, f, [("read", "gitlab_read_token")])
        return

    # read-write: current behaviour (both required, probe both)
    missing = []
    for filename, _, desc in SECRETS:
        val = _read_token(filename)
        if not val:
            f.bad("tokens", f"{filename} is empty", "run `catraz init`")
            missing.append(filename)
    if missing:
        return
    f.ok("tokens", "both GitLab tokens are set")
    _probe_gitlab_tokens(root, env, f, [("read", "gitlab_read_token"), ("write", "gitlab_write_token")])


def _gitlab_get(base: str, path: str, token: str, timeout: int = 5) -> dict[str, Any]:
    url = base.rstrip("/") + path
    req = urllib.request.Request(url, headers={"PRIVATE-TOKEN": token})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return cast(dict[str, Any], json.loads(resp.read().decode()))


def _probe_gitlab_tokens(root: Path, env: dict[str, str], f: Findings, tokens: list[tuple[str, str]] | None = None) -> None:
    """Best-effort online probe (P1 roast fix): catch expired/swapped/wrong-scope
    tokens. Degrades silently to 'set/not set' when the host can't reach GitLab.

    tokens: list of (label, filename) pairs to probe. Defaults to both tokens.
    """
    base = env.get("GITLAB_URL", "https://gitlab.com")
    secrets_dir = root / ".catraz" / "secrets"

    def _read_secret(filename: str) -> str:
        p = secrets_dir / filename
        try:
            return p.read_text(encoding="utf-8").strip() if p.exists() else ""
        except OSError:
            return ""

    if tokens is None:
        tokens = [("read", "gitlab_read_token"), ("write", "gitlab_write_token")]

    token_values = [(label, _read_secret(filename)) for label, filename in tokens]

    # Warn if read and write are identical (only meaningful when both are probed).
    probed_labels = {label: val for label, val in token_values}
    read_t = probed_labels.get("read", "")
    write_t = probed_labels.get("write", "")
    if read_t and write_t and read_t == write_t:
        f.warn("tokens", "READ and WRITE token are identical — likely a paste mistake")

    for label, token in token_values:
        try:
            me = _gitlab_get(base, "/api/v4/personal_access_tokens/self", token)
        except urllib.error.HTTPError as e:
            if e.code == 401:  # GitLab's unambiguous "this token is invalid/expired"
                f.bad("tokens", f"{label} token rejected by {base} (401)",
                      "rotate the token — it's invalid or expired")
                continue
            # 403/407/5xx etc. can be the proxy or a scope quirk → don't over-claim.
            f.warn("tokens", f"{label} token not probed (HTTP {e.code}) — online check skipped (likely because you chose a fine-grained scope)")
            return
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
            f.warn("tokens", f"{label} token not probed ({type(e).__name__}) — offline, check skipped")
            return  # host is offline/blocked; don't spam the other token
        scopes = me.get("scopes", [])
        active = me.get("active", True)
        if not active:
            f.bad("tokens", f"{label} token is inactive/revoked", "rotate the token")
            continue
        f.ok("tokens", f"{label} token valid (scopes: {', '.join(scopes) or '∅'})")
        if label == "read" and "api" in scopes:
            f.warn("tokens", "READ token carries the write 'api' scope — too broad (R6)",
                   "issue a read-only token (read_api, read_repository)")
        if label == "write" and "api" not in scopes:
            f.bad("tokens", "WRITE token lacks the 'api' scope — pushes will fail",
                  "issue a token with the 'api' scope")


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
        f.warn("policy", f"allowed_projects empty (source: {source})",
               "stack still starts (offline work OK); every GitLab op is denied "
               "until you add a project")
        return
    bad = []
    for p in resolved:
        reason = validate_project(p)
        if reason:
            bad.append(f"{p!r} ({reason})")
    if bad:
        f.bad("policy", "invalid allowed_projects: " + "; ".join(bad),
              "each entry must be a full project path, no wildcards/leaf/group-prefix")
    else:
        f.ok("policy", f"{len(resolved)} allowed project(s) [{source}]")


def check_claude(root: Path, env: dict[str, str], f: Findings) -> None:
    from catraz.paths import claude_home
    home = claude_home(root)
    creds = home / ".credentials.json"
    # The specific trap entrypoint.py hard-codes: Docker auto-created it as root.
    if home.exists() and home.stat().st_uid == 0 and os.getuid() != 0:
        f.bad("claude", f"{home} owned by root (Docker auto-created it)",
              f"sudo rm -rf {home} && mkdir -p {home} && catraz sync")
        return
    if not creds.exists():
        f.bad("claude", f"no sandbox credential in {home}", "run `catraz sync`")
    else:
        f.ok("claude", "Claude sandbox credential present")


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
        f.bad("auth", "AUTH_MODE must be subscription|api_key", "set it in .catraz/.env"); return
    cred = paths.claude_home(root) / ".credentials.json"
    # api_key: key is in .catraz/secrets/anthropic_api_key (compose secret); bare env var is fallback.
    api_key = _read_secret_file(root, "anthropic_api_key") or env.get("ANTHROPIC_API_KEY", "")
    if mode == "subscription":
        if api_key: f.bad("auth", "subscription mode but ANTHROPIC_API_KEY set", "unset it")
        if not cred.exists(): f.bad("auth", "no .credentials.json", "run `catraz sync`")
        else:
            f.ok("auth", "subscription credential present")
            f.warn("auth", "subscription token refreshes are not persisted across restarts "
                   "— re-run `catraz sync` if auth breaks")
    else:
        if not api_key: f.bad("auth", "api_key mode but ANTHROPIC_API_KEY empty",
                               "set it in .catraz/secrets/anthropic_api_key or .catraz/.env")
        if cred.exists(): f.bad("auth", "api_key mode but .credentials.json present (ambiguous)",
                                f"remove {paths.claude_home(root) / '.credentials.json'}")
        if api_key and not cred.exists(): f.ok("auth", "api_key set")


def check_base(root: Path, env: dict[str, str], f: Findings) -> None:
    if not which("docker"):
        f.warn("base", "docker missing — base not checked"); return
    try:
        base = image.resolve_base(root)
    except CliError as e:
        f.bad("base", str(e)); return
    contract = subprocess.run(
        ["docker", "run", "--rm", base, "sh", "-c", "command -v apt-get && python3 --version"],
        capture_output=True, text=True)
    if contract.returncode != 0:
        f.bad("base", "base lacks apt-get or python3", "base contract: Debian/Ubuntu + python3")
    else:
        f.ok("base", f"base contract ok ({base})")
    setuid = subprocess.run(["docker", "run", "--rm", base, "find", "/", "-perm", "/6000",
                             "-type", "f"], capture_output=True, text=True)
    extra = [ln for ln in setuid.stdout.split() if ln]
    if extra:
        # These are distro-shipped setuid/setgid binaries (passwd, su, mount, …). They are
        # rendered inert by the agent's `no-new-privileges` security_opt, which is enforced
        # non-bypassably by compose.assert_invariants on every up/run — so this is informational,
        # not a warning. If that invariant were dropped, up/run would fail loudly, not here.
        f.ok("base", f"{len(extra)} setuid/setgid binaries in base — neutralized by no-new-privileges")


def run_doctor(root: Path, only: list[str] | None = None, fix: bool = False) -> Findings:
    env: dict[str, str] = load_env(root / ".catraz" / ".env")
    f = Findings()
    sections = only or DOCTOR_SECTIONS
    if fix:
        _doctor_fix(root, env)
    if "docker" in sections: check_docker(f)
    if "compose" in sections: check_compose(root, env, f)
    if "env" in sections: check_env(root, env, f)
    if "tokens" in sections: check_gitlab(env, f)
    if "tokens" in sections: check_tokens(root, env, f)
    if "policy" in sections: check_policy(root, env, f)
    if "claude" in sections: check_claude(root, env, f)
    if "net" in sections: check_net(root, f)
    if "auth" in sections: check_auth(root, env, f)
    if "base" in sections: check_base(root, env, f)
    return f


def _doctor_fix(root: Path, env: dict[str, str]) -> None:
    """Repair only the safe things: missing dirs + chown. Never secrets/policy."""
    dev_uid = env.get("DEV_UID", "")
    cat = root / ".catraz"
    # secrets/ and secrets/claude must be created first at 0700 BEFORE the generic loop,
    # because mkdir(parents=True) in the loop would create secrets/ at the umask default
    # (0755) and a later chmod on an already-existing dir is a no-op for mode.
    secrets_dir = cat / "secrets"
    secrets_dir.mkdir(mode=0o700, exist_ok=True)
    secrets_dir.chmod(0o700)
    claude_secrets = cat / "secrets" / "claude"
    claude_secrets.mkdir(mode=0o700, parents=True, exist_ok=True)
    claude_secrets.chmod(0o700)
    for d in ["config", "state/warden/db", "state/warden/run", "logs/warden", "logs/squid"]:
        (cat / d).mkdir(parents=True, exist_ok=True)
    mode = env.get("AUTH_MODE") or "subscription"
    secret_files = [f for f, _, _ in SECRETS]
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
