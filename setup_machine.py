"""
setup_machine.py — get the app running on another computer.

    python setup_machine.py

config.yaml is the one file that cannot be shared between machines: it holds absolute paths, and
those paths contain a username. That is why it is gitignored, and why a fresh clone has everything
except the thing it needs to start. This writes one for the machine it is run on.

It finds OneDrive, looks for the collection workbook and the app folder inside it, and reports what
it could not find rather than guessing. Nothing is overwritten: if config.yaml already exists it
says so and stops.

The app folder is the important one. Both halves meet there and OneDrive does the syncing, which is
how a second computer sees everything the first one has scored.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

TEMPLATE = "config.example.yaml"
TARGET = "config.yaml"
MASTER_NAME = "Whiskey Collection.xlsx"
ROLLUP_NAME = "Whiskey Tastings.xlsx"
APP_FOLDER_TAIL = Path("Apps") / "Whiskey Tasting Book"


def onedrive_roots():
    """Every OneDrive this account has. Personal and work sign-ins each get their own, and the
    environment names them differently, so look at all of them rather than assuming one."""
    seen = []
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        value = os.environ.get(var)
        if value and Path(value).is_dir() and Path(value) not in seen:
            seen.append(Path(value))
    home = Path.home()
    for guess in home.glob("OneDrive*"):
        if guess.is_dir() and guess not in seen:
            seen.append(guess)
    return seen


def find_file(roots, name, limit=6):
    """Search each OneDrive for a file by name, shallowest match first — the collection workbook
    is not always in the same folder on two machines, and the document folder may be localised."""
    for root in roots:
        matches = []
        for depth in range(limit):
            pattern = "/".join(["*"] * depth + [name]) if depth else name
            matches.extend(p for p in root.glob(pattern) if p.is_file())
            if matches:
                return sorted(matches, key=lambda p: len(p.parts))[0]
    return None


def find_app_folder(roots):
    for root in roots:
        candidate = root / APP_FOLDER_TAIL
        if candidate.is_dir():
            return candidate
    return None


def as_yaml_path(p):
    """Forward slashes read the same on every platform and avoid backslash escapes."""
    return str(p).replace("\\", "/")


def main():
    here = Path(__file__).resolve().parent
    target = here / TARGET
    if target.exists():
        print(f"{TARGET} already exists here — leaving it alone.")
        print("Delete it first if you want this to build a new one.")
        return 0

    template = here / TEMPLATE
    if not template.exists():
        print(f"{TEMPLATE} is missing; this needs to be run inside the project folder.")
        return 1

    roots = onedrive_roots()
    if not roots:
        print("No OneDrive folder found on this machine.")
        print("Sign in to OneDrive first, let it sync, then run this again.")
        return 1

    print("OneDrive:")
    for r in roots:
        print(f"  {r}")

    master = find_file(roots, MASTER_NAME)
    rollup = find_file(roots, ROLLUP_NAME)
    app_folder = find_app_folder(roots)

    print()
    print("Found:")
    print(f"  collection workbook : {master or 'NOT FOUND'}")
    print(f"  readable workbook   : {rollup or 'not yet — it is generated, so this is fine'}")
    print(f"  app folder          : {app_folder or 'NOT FOUND'}")

    if not app_folder:
        # Without this the two halves have nowhere to meet, and every score would stay on one
        # machine. Better to stop than to write a config that silently starts a second journal.
        print()
        print("The app folder is the one that matters: it is where your scores live and how this")
        print("machine sees what the other has done. Expected it at:")
        for r in roots:
            print(f"  {r / APP_FOLDER_TAIL}")
        print("Wait for OneDrive to finish syncing, then run this again.")
        return 1

    if not master:
        print()
        print("Note: the collection workbook was not found. The app will still start, and every")
        print("score you have already taken will be there, but Refresh cannot reread the")
        print("collection and new bottles cannot be approved on this machine.")

    text = template.read_text(encoding="utf-8")
    replacements = {
        "master_workbook": as_yaml_path(master) if master else None,
        "rollup_workbook": as_yaml_path(rollup) if rollup else as_yaml_path(
            (master.parent if master else app_folder) / ROLLUP_NAME),
        "app_folder": as_yaml_path(app_folder),
    }
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        key = stripped.split(":", 1)[0].strip() if ":" in stripped else ""
        if key in replacements and replacements[key] and not stripped.startswith("#"):
            indent = line[: len(line) - len(line.lstrip())]
            out.append(f'{indent}{key}: "{replacements[key]}"')
        else:
            out.append(line)
    target.write_text("\n".join(out) + "\n", encoding="utf-8")

    print()
    print(f"Wrote {target}")
    print()
    print("Next:")
    print("  pip install -r requirements.txt")
    print("  python -m unittest discover -p \"test_*.py\"")
    print("  build_exe.bat            (then: python make_shortcut.py)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
