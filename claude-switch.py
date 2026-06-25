#!/usr/bin/env python3
"""Switches between two Claude Code accounts using directory junctions (Windows) or symlinks (POSIX)."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

HOME = Path.home()
CLAUDE_DIR = HOME / ".claude"
ACCOUNT_DIRS = {1: HOME / ".claude1", 2: HOME / ".claude2"}


def is_link(path: Path) -> bool:
    if IS_WINDOWS:
        try:
            attrs = os.stat(path, follow_symlinks=False).st_file_attributes
            return bool(attrs & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
        except OSError:
            return False
    return path.is_symlink()


def read_link_target(path: Path) -> Path:
    raw = os.readlink(path)
    if IS_WINDOWS and raw.startswith("\\\\?\\"):
        raw = raw[4:]
    return Path(raw)


def create_link(link: Path, target: Path) -> None:
    if IS_WINDOWS:
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True
        )
        if result.returncode != 0:
            msg = result.stderr.decode(errors="replace").strip()
            sys.exit(f"Junction-Fehler: {msg}")
    else:
        os.symlink(target, link)


def remove_link(path: Path) -> None:
    if IS_WINDOWS:
        os.rmdir(str(path))  # entfernt nur den Junction-Eintrag, nicht den Inhalt
    else:
        path.unlink()


def get_active_account() -> int:
    if not is_link(CLAUDE_DIR):
        return 0
    try:
        target = read_link_target(CLAUDE_DIR)
        for num, d in ACCOUNT_DIRS.items():
            if target == d or target.name == d.name:
                return num
    except OSError:
        pass
    return 0


def cmd_init() -> None:
    if is_link(CLAUDE_DIR):
        print(f"Bereits initialisiert. Aktiver Account: {get_active_account()}")
        return
    if not CLAUDE_DIR.is_dir():
        sys.exit(f"Fehler: {CLAUDE_DIR} existiert nicht.")

    print(f"Verschiebe {CLAUDE_DIR}  ->  {ACCOUNT_DIRS[1]} ...")
    CLAUDE_DIR.rename(ACCOUNT_DIRS[1])
    ACCOUNT_DIRS[2].mkdir()
    create_link(CLAUDE_DIR, ACCOUNT_DIRS[1])
    print(
        f"Fertig. Account 1 ({ACCOUNT_DIRS[1]}) ist aktiv.\n\n"
        "Naechste Schritte fuer Account 2:\n"
        "  claude-switch 2   # wechselt zu Account 2\n"
        "  claude            # dort einloggen\n"
        "  claude-switch 1   # zurueck zu Account 1"
    )


def cmd_status() -> None:
    active = get_active_account()
    if active == 0:
        print("Nicht initialisiert. Fuehre 'claude-switch --init' aus.")
    else:
        print(f"Aktiver Account: {active}  ({ACCOUNT_DIRS[active]})")


def cmd_switch(account: int) -> None:
    current = get_active_account()
    if current == 0:
        sys.exit("Nicht initialisiert. Fuehre erst 'claude-switch --init' aus.")

    target = account if account else (2 if current == 1 else 1)
    if target == current:
        print(f"Bereits auf Account {current}")
        return

    remove_link(CLAUDE_DIR)
    create_link(CLAUDE_DIR, ACCOUNT_DIRS[target])
    print(f"Gewechselt zu Account {target}  ({ACCOUNT_DIRS[target]})")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="claude-switch",
        description="Wechselt zwischen zwei Claude Code Accounts"
    )
    parser.add_argument("account", nargs="?", type=int, choices=[1, 2],
                        help="Account 1 oder 2. Ohne Angabe: Toggle.")
    parser.add_argument("--init",   action="store_true", help="Einmalige Initialisierung")
    parser.add_argument("--status", action="store_true", help="Zeigt aktiven Account")
    args = parser.parse_args()

    if args.init:
        cmd_init()
    elif args.status:
        cmd_status()
    else:
        cmd_switch(args.account or 0)


if __name__ == "__main__":
    main()
