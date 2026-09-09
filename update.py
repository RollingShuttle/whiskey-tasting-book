"""
update.py — bring this machine up to date.

    python update.py          (or double-click update.bat)

Fetches the latest code, reinstalls the libraries if they changed, and rebuilds the app. Meant to
be the only thing you run: doing it by hand means remembering to quit the app first, and forgetting
that is silent — the rebuild fails or is skipped and you carry on using yesterday's copy with
nothing on screen to say so.

Four things it is careful about:

  * The running app holds its own .exe open, so the build cannot replace it. That is stopped first,
    and only after the pull has actually brought something new.
  * config.yaml, data/ and the workbooks are yours and are not in the repository, so a pull cannot
    touch them. If you have edited any tracked file yourself, the pull is refused rather than
    forced, and it says which file.
  * The libraries are only reinstalled when requirements.txt actually changed.
  * The rebuild is skipped when nothing came down and the app is already there, because it takes
    twenty seconds and would otherwise run every time for nothing.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXE = HERE / "Whiskey Tasting Book.exe"
REQUIREMENTS = HERE / "requirements.txt"


def run(args, **kw):
    """Run a command in the project folder and hand back (ok, output)."""
    try:
        done = subprocess.run(args, cwd=HERE, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", **kw)
    except FileNotFoundError:
        return False, f"{args[0]} is not installed, or not on the PATH."
    return done.returncode == 0, (done.stdout or "") + (done.stderr or "")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def app_is_running():
    if os.name != "nt":
        return False
    ok, out = run(["tasklist", "/FI", "IMAGENAME eq Whiskey Tasting Book.exe"])
    return ok and "Whiskey Tasting Book.exe" in out


def stop_app():
    """The app holds its own executable open, so it has to go before the build can replace it.

    Nothing is lost by this. Submitted cards are already in the journal, and a card still being
    scored was autosaved as a draft within the last few seconds.
    """
    if not app_is_running():
        return True
    print("Closing the running app so it can be replaced...")
    run(["taskkill", "/F", "/IM", "Whiskey Tasting Book.exe"])
    for _ in range(20):
        if not app_is_running():
            return True
        time.sleep(0.25)
    print("  it is still running. Quit it from the notification area and run this again.")
    return False


def check_repo():
    ok, out = run(["git", "rev-parse", "--is-inside-work-tree"])
    if not ok:
        print("This folder is not a git checkout, so there is nothing to update from.")
        print("Re-clone it with:")
        print("  git clone https://github.com/RollingShuttle/whiskey-tasting-book.git")
        return False
    ok, out = run(["git", "status", "--porcelain", "--untracked-files=no"])
    if ok and out.strip():
        print("You have your own changes to files that are tracked here:")
        for line in out.strip().splitlines()[:10]:
            print("  " + line.strip())
        print()
        print("The update was stopped rather than overwrite them. Undo them, or move them aside,")
        print("then run this again. (config.yaml and data/ are not tracked and are never touched.)")
        return False
    return True


def pull():
    """Returns (ok, brought_something_new)."""
    print("Fetching the latest version...")
    ok, head = run(["git", "rev-parse", "HEAD"])
    was = head.strip() if ok else ""
    ok, out = run(["git", "pull", "--ff-only"])
    if not ok:
        print("  could not fetch:")
        print("  " + out.strip().replace("\n", "\n  "))
        print()
        print("If that mentions the network, try again when you are online.")
        return False, False
    ok2, now = run(["git", "rev-parse", "HEAD"])
    changed = ok2 and now.strip() != was
    if changed:
        _, log = run(["git", "log", "--oneline", f"{was}..HEAD"])
        lines = [l for l in log.strip().splitlines() if l.strip()]
        print(f"  {len(lines)} new change{'' if len(lines) == 1 else 's'}:")
        for line in lines[:12]:
            print("    " + line)
        if len(lines) > 12:
            print(f"    ... and {len(lines) - 12} more")
    else:
        print("  already up to date.")
    return True, changed


def install_requirements():
    print("Installing the libraries it needs...")
    ok, out = run([sys.executable, "-m", "pip", "install", "--quiet",
                   "--disable-pip-version-check", "-r", str(REQUIREMENTS)])
    if not ok:
        print("  that failed:")
        print("  " + out.strip().replace("\n", "\n  ")[:800])
    return ok


def rebuild():
    """The app bundles its own copy of the code, so a pull alone changes nothing about it."""
    print("Rebuilding the app (about twenty seconds)...")
    ok, out = run([sys.executable, "-m", "pip", "install", "--quiet",
                   "--disable-pip-version-check", "pyinstaller"])
    if not ok:
        print("  could not install the build tool:")
        print("  " + out.strip()[:600])
        return False
    ok, out = run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onefile",
        "--name", "Whiskey Tasting Book",
        "--icon", str(HERE / "icon.ico"),
        "--add-data", f"{HERE / 'static'};static",
        "--hidden-import", "pystray._win32",
        "--distpath", str(HERE),
        "--workpath", str(HERE / "build"),
        "--specpath", str(HERE / "build"),
        str(HERE / "launch.py"),
    ])
    if not ok:
        print("  the build failed:")
        print("  " + out.strip()[-900:].replace("\n", "\n  "))
        return False
    print(f"  built {EXE.name}")
    return True


def main():
    print("Whiskey Tasting Book — update")
    print("=" * 46)

    if not check_repo():
        return 1

    before = digest(REQUIREMENTS)
    ok, changed = pull()
    if not ok:
        return 1

    if not changed and EXE.exists():
        print()
        print("Nothing to do — this machine is already current.")
        return 0

    if digest(REQUIREMENTS) != before:
        print("requirements.txt changed.")
        if not install_requirements():
            return 1

    if not stop_app():
        return 1
    if not rebuild():
        return 1

    ok, _ = run([sys.executable, str(HERE / "make_shortcut.py")])
    print()
    print("Done. Open it from the Desktop icon.")
    print("Press Refresh once inside, so the phone gets anything new the PC now publishes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
