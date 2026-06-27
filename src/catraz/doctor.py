"""Findings + Checks."""
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from catraz.envfile import load_env
from catraz.compose import run as compose_run

# Secrets the wizard collects (env key → human prompt). Order matters.
SECRETS = [
   # ("ANTHROPIC_API_KEY", "Anthropic API key (dedicated sandbox account, not your primary)"),
    ("GITLAB_READ_TOKEN", "GitLab READ token (scopes: read_api, read_repository)"),
    ("GITLAB_WRITE_TOKEN", "GitLab WRITE token (scopes: api — service account / Developer)"),
]

OK, WARN, BAD = "ok", "warn", "bad"

DOCTOR_SECTIONS = ["docker", "compose", "env", "tokens", "policy", "claude", "net"]
# Sections that gate the trust boundary — `up` always runs these, no opt-out.
SECURITY_SECTIONS = ["docker", "compose", "env", "policy"]


class Findings:
    def __init__(self):
        self.items = []

    def add(self, level, section, msg, hint=None):
        self.items.append((level, section, msg, hint))

    def ok(self, sec, msg): self.add(OK, sec, msg)
    def warn(self, sec, msg, hint=None): self.add(WARN, sec, msg, hint)
    def bad(self, sec, msg, hint=None): self.add(BAD, sec, msg, hint)


def which(cmd):
    return shutil.which(cmd) is not None


def check_docker(f):
    if not which("docker"):
        f.bad("docker", "docker not on PATH", "install Docker + Compose v2")
        return
    r = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if r.returncode != 0:
        f.bad("docker", "Docker daemon not reachable", "start Docker (`systemctl start docker`)")
    else:
        f.ok("docker", "Docker daemon is up")
    r = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True)
    if r.returncode != 0:
        f.bad("docker", "Compose v2 missing", "install the `docker compose` plugin")
    else:
        f.ok("docker", "Compose v2 present")


def check_compose(root, env, f):
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


def check_env(root, env, f):
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


def check_tokens(env, f):
    for key, _ in SECRETS:
        if not env.get(key):
            f.bad("tokens", f"{key} is empty", "run `catraz init`")
    if any(not env.get(k) for k, _ in SECRETS):
        return
    f.ok("tokens", "all three secrets are set")
    _probe_gitlab_tokens(env, f)


def _gitlab_get(base, path, token, timeout=5):
    url = base.rstrip("/") + path
    req = urllib.request.Request(url, headers={"PRIVATE-TOKEN": token})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _probe_gitlab_tokens(env, f):
    """Best-effort online probe (P1 roast fix): catch expired/swapped/wrong-scope
    tokens. Degrades silently to 'set/not set' when the host can't reach GitLab."""
    base = env.get("GITLAB_URL", "https://gitlab.com")
    read_t = env.get("GITLAB_READ_TOKEN", "")
    write_t = env.get("GITLAB_WRITE_TOKEN", "")

    if read_t and write_t and read_t == write_t:
        f.warn("tokens", "READ and WRITE token are identical — likely a paste mistake")

    for label, token in (("read", read_t), ("write", write_t)):
        try:
            me = _gitlab_get(base, "/api/v4/personal_access_tokens/self", token)
        except urllib.error.HTTPError as e:
            if e.code == 401:  # GitLab's unambiguous "this token is invalid/expired"
                f.bad("tokens", f"{label} token rejected by {base} (401)",
                      "rotate the token — it's invalid or expired")
                continue
            # 403/407/5xx etc. can be the proxy or a scope quirk → don't over-claim.
            f.warn("tokens", f"{label} token not probed (HTTP {e.code}) — online check skipped")
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


def check_policy(root, env, f):
    """Fast pre-check of allowed_projects. Authoritative validation stays the
    warden reconcile — this just turns the obvious traps loud before start."""
    from catraz.policy import _resolve_allowed_projects, validate_project
    resolved, source = _resolve_allowed_projects(root, env)
    if not resolved:
        f.bad("policy", f"allowed_projects empty (source: {source})",
              "run `catraz init` — an empty allowlist is fail-closed (warden won't start)")
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


def check_claude(root, env, f):
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


def check_net(root, f):
    # Admin/audit moved from TCP (172.31.0.2:9090) to a per-project unix socket
    # under .catraz/run/warden/. The socket file only exists while the stack runs.
    sock = root / ".catraz" / "run" / "warden" / "admin.sock"
    if sock.exists():
        f.ok("net", "admin socket present (stack up)")
    else:
        f.ok("net", "admin socket absent (stack down — start with `catraz up`)")


def run_doctor(root, only=None, fix=False):
    env = load_env(root / ".catraz" / ".env")
    f = Findings()
    sections = only or DOCTOR_SECTIONS
    if fix:
        _doctor_fix(root, env)
    if "docker" in sections: check_docker(f)
    if "compose" in sections: check_compose(root, env, f)
    if "env" in sections: check_env(root, env, f)
    if "tokens" in sections: check_tokens(env, f)
    if "policy" in sections: check_policy(root, env, f)
    if "claude" in sections: check_claude(root, env, f)
    if "net" in sections: check_net(root, f)
    return f


def _doctor_fix(root, env):
    """Repair only the safe things: missing dirs + chown. Never secrets/policy."""
    dev_uid = env.get("DEV_UID", "")
    cat = root / ".catraz"
    for d in ["config", "state/warden", "logs/warden", "logs/squid", "claude", "run/warden"]:
        (cat / d).mkdir(parents=True, exist_ok=True)
    if dev_uid.isdigit():
        for d in ["state", "logs", "run"]:
            try:
                _chown_r(cat / d, int(dev_uid))
            except PermissionError:
                pass  # surfaced as a finding by check_env; --fix is best-effort


def _chown_r(path, uid):
    os.chown(path, uid, -1)
    for p in path.rglob("*"):
        os.chown(p, uid, -1)


def print_findings(f, out):
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
