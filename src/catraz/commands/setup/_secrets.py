from pathlib import Path

from catraz.policy import normalize_host


def _ensure_secret(secrets_dir: Path, filename: str) -> None:
    """Ensure secret file exists at 0600. Never overwrites non-empty existing content."""
    p = secrets_dir / filename
    if not p.exists():
        p.write_text("")
        p.chmod(0o600)


def _write_secret_value(secrets_dir: Path, filename: str, value: str) -> None:
    """Write value to secrets_dir/filename at 0600. Creates the file if missing."""
    p = secrets_dir / filename
    p.write_text(value)
    p.chmod(0o600)


def _read_grouped_token(secrets_dir: Path, filename: str, host: str) -> str:
    """Current token for *host* in a grouped `<host> <token>` file, or ''.
    Host matching follows the warden's normalised, first-whitespace-split rule."""
    p = secrets_dir / filename
    try:
        text = p.read_text(encoding="utf-8") if p.exists() else ""
    except OSError:
        return ""
    key = normalize_host(host)
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split(None, 1)
        if len(parts) == 2 and normalize_host(parts[0]) == key:
            return parts[1]
    return ""


def _upsert_grouped_token(secrets_dir: Path, filename: str, host: str, token: str) -> None:
    """Insert or replace the `<host> <token>` line for *host* in a grouped token
    file at 0600, preserving other hosts, comments, and blank lines. Mirrors the
    warden's parse rule so what init writes is exactly what the warden reads."""
    p = secrets_dir / filename
    try:
        text = p.read_text(encoding="utf-8") if p.exists() else ""
    except OSError:
        text = ""
    key = normalize_host(host)
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        parts = stripped.split(None, 1)
        is_hostline = bool(parts) and not stripped.startswith("#") and normalize_host(parts[0]) == key
        if not is_hostline:
            kept.append(line)
    kept.append(f"{key} {token}")
    p.write_text("\n".join(kept) + "\n")
    p.chmod(0o600)
