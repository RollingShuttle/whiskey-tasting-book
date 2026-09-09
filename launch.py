"""
launch.py — start the app and open it in its own window.

`python app.py` leaves you with a console and an address to type. This does the same job but
behaves like a desktop program: it starts the server, waits until it is actually answering, and
opens it in a browser window with no address bar, no tabs and no bookmarks — just the app.

Closing that window does **not** stop it. The app keeps running in the notification area, so the
next window opens instantly and anything the phone uploads still lands while you are not looking
at it. Quit from the tray icon when you actually want it to stop.

Four details worth knowing:

  * The server runs in a thread of *this* process rather than a separate one, so it cannot outlive
    the launcher and leave a port held by an invisible program.
  * The tray icon is not decoration. Once the window is no longer the way out, it is the only way
    to stop the app short of Task Manager — so if a tray cannot be created, the app deliberately
    goes back to closing with its window rather than becoming unstoppable.
  * The browser process cannot be waited on. Edge hands off to a background process and the one
    we started exits within a moment, even with its own profile — waiting on it shut the server
    down while the window was still on screen, showing a dead page. So the *page* tells us when
    it is going, and we keep running until it does.
  * If the app is already running, this just opens another window at it rather than trying to
    start a second server on a port that is taken.

    python launch.py                 start it and open the window
    python launch.py --no-window     start it and print the address (the old behaviour)
    python launch.py --no-tray       stop when the window closes, instead of staying resident
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


def app_dir():
    """Where the app's own files live: beside the executable when packaged, beside this file
    otherwise. config.yaml, data/ and the backups belong to the user, so they stay out here
    rather than inside the bundle, which is temporary and read-only."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def report_problem(message):
    """Packaged there is no console, so a failure has to be written down and shown rather than
    vanishing into a window that never appears."""
    try:
        (app_dir() / "error.log").write_text(message, encoding="utf-8")
    except OSError:
        pass
    if os.name == "nt" and getattr(sys, "frozen", False):
        try:
            import ctypes
            hint = "\n\nThe full text is in error.log next to the app."
            ctypes.windll.user32.MessageBoxW(
                None, message[-1500:] + hint, "Whiskey Tasting Book", 0x10)
        except Exception:
            pass
    else:
        print(message, file=sys.stderr)


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


def attach_lifecycle(application, state):
    """Let the page report that it is still there, and that it is closing.

    Only the launcher registers these: `python app.py` has a console to close and needs no such
    machinery. A reload fires the same goodbye, so the caller waits a few seconds and any
    heartbeat — from the reloaded page, or from a second window — cancels the shutdown.
    """
    @application.post("/api/heartbeat")
    def _heartbeat():
        state["last"] = time.monotonic()
        state["closing"] = None
        return {"ok": True}

    @application.post("/api/goodbye")
    def _goodbye():
        state["closing"] = time.monotonic()
        return "", 204


def open_window(url):
    """Start the window and let it go. Nothing useful can be learned by waiting on it."""
    browser = find_browser()
    if not browser:
        webbrowser.open(url)
        return False
    profile = profile_path()
    profile.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(window_command(browser, url, str(profile)))
    return True


def tray_image():
    """The tray wants a picture, and the one the page already uses is bundled beside it."""
    from PIL import Image
    return Image.open(app_mod.STATIC_DIR / "icon.ico")


def build_tray(url):
    """Open is the default action, so double-clicking the tray icon puts the window back — which
    is what people try first."""
    import pystray
    return pystray.Icon("whiskey_tasting_book", tray_image(), "Whiskey Tasting Book", pystray.Menu(
        pystray.MenuItem("Open", lambda icon, item: open_window(url), default=True),
        pystray.MenuItem("Quit", lambda icon, item: icon.stop()),
    ))


def wait_in_tray(url):
    """Blocks until Quit is chosen. None means no tray could be created — the caller then needs
    something that can still be stopped."""
    try:
        icon = build_tray(url)
    except Exception:                          # noqa: BLE001 — any failure here means "no tray"
        return None
    try:
        icon.run()
    except Exception:                          # noqa: BLE001
        return None
    return 0


def wait_for_exit(state, url, tray=True):
    """Closing the window is no longer the end of the program. The server stays up behind the
    tray icon; Quit there ends it.

    The fallback matters more than the tray does. A windowless app with no way to quit is worse
    than one that closes too eagerly, so if the tray will not start we go back to stopping when
    the window goes."""
    if tray:
        done = wait_in_tray(url)
        if done is not None:
            return done
    return wait_until_closed(state)


def wait_until_closed(state, grace=15.0, idle=3600.0):
    """Stay up while the window is open. `grace` covers a reload; `idle` is only a leak guard for
    the case where the goodbye never arrives at all."""
    try:
        while True:
            time.sleep(0.5)
            now = time.monotonic()
            if state["closing"] and now - state["closing"] > grace:
                return 0
            if now - state["last"] > idle:
                return 0
    except KeyboardInterrupt:
        return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Start the Whiskey Tasting Book and open its window")
    ap.add_argument("--config", default=None)
    ap.add_argument("--port", type=int)
    ap.add_argument("--no-window", action="store_true",
                    help="start the server and print the address instead of opening a window")
    ap.add_argument("--no-tray", action="store_true",
                    help="stop when the window closes instead of staying in the notification area")
    a = ap.parse_args(argv)

    # Relative paths in config.yaml (./data, ./data/backups) are meant to be relative
    # to the app, not to wherever a shortcut happened to start us.
    os.chdir(app_dir())
    config = a.config or str(app_dir() / "config.yaml")
    if not Path(config).exists():
        report_problem(f"No settings file found at {config}.\n\n"
                       "Copy config.example.yaml to config.yaml and fill in the "
                       "three paths near the top.")
        return 1

    cfg = yaml.safe_load(open(config, encoding="utf-8"))
    srv = cfg.get("server", {})
    host = "0.0.0.0" if srv.get("bind_lan") else srv.get("host", "127.0.0.1")
    port = a.port or int(srv.get("port", 8765))
    url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}"

    if port_open(host, port):
        # Another copy already owns the server; just put a window in front of it and get out.
        print(f"Already running at {url} — opening another window.")
        open_window(url)
        return 0

    state = {"last": time.monotonic(), "closing": None}
    application = app_mod.create_app(config)
    attach_lifecycle(application, state)
    threading.Thread(target=serve, args=(application, host, port), daemon=True).start()
    if not wait_for_port(host, port):
        report_problem("The app did not start listening in time.")
        return 1
    print(f"Whiskey Tasting Book  →  {url}")

    if a.no_window:
        print("Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return 0

    open_window(url)
    return wait_for_exit(state, url, tray=not a.no_tray)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:                      # noqa: BLE001 — a windowless app must not fail silently
        import traceback
        report_problem(traceback.format_exc())
        raise SystemExit(1)
