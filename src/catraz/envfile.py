"""load_env / set_env_values / mask."""
import re
from pathlib import Path


def load_env(path):
    """Parse a .env file into {key: value}, stripping inline comments."""
    env = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        # Strip an inline comment only when it follows whitespace (tokens have no " #").
        m = re.search(r"\s#", val)
        if m:
            val = val[: m.start()]
        env[key] = val.strip()
    return env


def set_env_values(path, updates):
    """Set keys in-place, preserving order/comments. Uncomments `# KEY=` forms;
    appends keys that are absent. Writes a clean `KEY=value` (drops inline hints)."""
    lines = path.read_text().splitlines() if path.exists() else []
    remaining = dict(updates)
    out = []
    for line in lines:
        replaced = False
        for key in list(remaining):
            if re.match(rf"^\s*#?\s*{re.escape(key)}=", line):
                out.append(f"{key}={remaining.pop(key)}")
                replaced = True
                break
        if not replaced:
            out.append(line)
    for key, val in remaining.items():
        out.append(f"{key}={val}")
    path.write_text("\n".join(out) + "\n")


def unset_env_keys(path, keys):
    """Remove active KEY=value lines for the given keys from a .env file.

    Lines that are already commented out (``# KEY=…``) are left untouched.
    A no-op when the file does not exist or none of the keys are present as
    active assignments.
    """
    if not path.exists():
        return
    keys_set = set(keys)
    lines = path.read_text().splitlines()
    out = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in keys_set:
                continue  # drop this active assignment
        out.append(line)
    path.write_text("\n".join(out) + "\n")


def mask(val):
    if not val:
        return ""
    return val[:3] + "…" + val[-2:] if len(val) > 6 else "•" * len(val)
