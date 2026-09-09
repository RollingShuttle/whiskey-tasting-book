"""
launch.py — start the app and open it in its own window.

`python app.py` leaves you with a console and an address to type. This does the same job but
behaves like a desktop program: it starts the server, waits until it is actually answering, and
opens it in a browser window with no address bar, no tabs and no bookmarks — just the app. Closing
that window shuts the server down with it, so there is one thing to open and one thing to close.

Three details worth knowing:

  * The server runs in a thread of *this* process rather than a separate one, so it cannot outlive
    the launcher and leave a port held by an invisible program.
  * The window is opened with a dedicated browser profile. Without one, Edge and Chrome hand the
    request to an already-running copy of themselves and exit immediately — which would look like
    the app closing the instant it opened.
  * If the app is already running, this just opens another window at it rather than trying to
    start a second server on a port that is taken.

    python launch.py                 start it and open the window
    python launch.py --no-window     start it and print the address (the old behaviour)
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

import yaml

import app as app_mod

# Chromium-based browsers take --app=, which is what gives a window with no address bar. Edge is
# first because it is on every Windows machine.
BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def find_browser(candidates=None):
    """The first Chromium-based browser we can find, or None to fall back to the default one."""
    for path in candidates if candidates is not None else BROWSERS:
        expanded = os.path.expandvars(path)
        if Path(expanded).exists():
            return expanded
    local = os.environ.get("LOCALAPPDATA")
    if local:
        for rest in (r"Microsoft\Edge\Application\msedge.exe",
                     r"Google\Chrome\Application\chrome.exe"):
            guess = Path(local) / rest
            if guess.exists():
                return str(guess)
    return None


def port_open(host, port, timeout=0.4):
    """True when something is already answering there."""
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
    with socket.socket() as s:
        s.settimeout(timeout)
        return s.connect_ex((probe_host, int(port))) == 0


def wait_for_port(host, port, timeout=25.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_open(host, port):
            return True
        time.sleep(0.15)
    return False


def window_command(browser, url, profile_dir):
    """A clean application window rather than a tab in whatever was already open."""
    return [
        browser,
        f"--app={url}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
    ]


def profile_path():
    """Kept out of the project folder; it is browser scratch, not part of the app."""
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(base) / "WhiskeyTastingBook" / "window"


def serve(application, host, port):
    """Quietens the per-request log lines; the console is minimised and closes with the window,
    so there is nothing useful to read there.

    Werkzeug's "this is a development server" banner stays. Setting WERKZEUG_RUN_MAIN to hide it
    makes Werkzeug believe it is a reloader child and the server then never binds at all — a
    cosmetic tweak is not worth an app that does not start. The warning is accurate and harmless
    here: it serves one person on their own machine."""
    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    application.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Start the Whiskey Tasting Book and open its window")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--port", type=int)
    ap.add_argument("--no-window", action="store_true",
                    help="start the server and print the address instead of opening a window")
    a = ap.parse_args(argv)

    cfg = yaml.safe_load(open(a.config, encoding="utf-8"))
    srv = cfg.get("server", {})
    host = "0.0.0.0" if srv.get("bind_lan") else srv.get("host", "127.0.0.1")
    port = a.port or int(srv.get("port", 8765))
    url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}"

    already = port_open(host, port)
    if already:
        print(f"Already running at {url} — opening another window.")
    else:
        application = app_mod.create_app(a.config)
        threading.Thread(target=serve, args=(application, host, port), daemon=True).start()
        if not wait_for_port(host, port):
            print("The app did not start. Run `python app.py` to see why.", file=sys.stderr)
            return 1
        print(f"Whiskey Tasting Book  →  {url}")

    if a.no_window:
        print("Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return 0

    browser = find_browser()
    if browser:
        profile = profile_path()
        profile.mkdir(parents=True, exist_ok=True)
        # Blocks until the window is closed; the server thread is a daemon, so returning here
        # takes it with us.
        subprocess.run(window_command(browser, url, str(profile)))
    else:
        # No Chromium browser found: open a normal tab and stay up until interrupted, because
        # there is no window to wait on.
        webbrowser.open(url)
        print("Opened in your default browser. Press Ctrl+C here to stop the app.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
