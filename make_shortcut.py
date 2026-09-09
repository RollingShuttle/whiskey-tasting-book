"""
make_shortcut.py — put "Whiskey Tasting Book" on the Desktop and in the Start menu.

    python make_shortcut.py

Creates ordinary Windows shortcuts pointing at run.bat, with the app icon, set to start
minimised. Delete them like any other shortcut; nothing else is installed or registered.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "run.bat"
ICON = ROOT / "icon.ico"
NAME = "Whiskey Tasting Book.lnk"


def destinations():
    home = Path(os.environ.get("USERPROFILE", Path.home()))
    yield home / "Desktop" / NAME
    appdata = os.environ.get("APPDATA")
    if appdata:
        yield Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / NAME


def make(dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
        "$s.TargetPath = '%s';"
        "$s.WorkingDirectory = '%s';"
        "$s.IconLocation = '%s';"
        "$s.WindowStyle = 7;"
        "$s.Description = 'Whiskey Tasting Book';"
        "$s.Save()"
    ) % (dest, TARGET, ROOT, ICON)
    done = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                          capture_output=True, text=True)
    if done.returncode != 0:
        print("  could not create %s: %s" % (dest, done.stderr.strip()))
        return False
    print("  created %s" % dest)
    return True


def main():
    if os.name != "nt":
        print("Windows only - on anything else just run: python launch.py")
        return 1
    if not TARGET.exists():
        print("run.bat is missing from %s" % ROOT)
        return 1
    print("Creating shortcuts:")
    made = [make(d) for d in destinations()]
    if any(made):
        print()
        print("Done. Open it from the Desktop or the Start menu.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
