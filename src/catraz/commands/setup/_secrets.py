from pathlib import Path


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
