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
  * There is only ever one of it, and that takes two separate guards. A named mutex keeps the
    *process* unique — the port probe cannot, because the gap between looking and binding is one
    double-click wide. Keeping the *window* unique is the harder half: a second copy cannot judge
    it, since Edge takes seconds to draw a window and during that gap there is nothing to find.
    So a second launch never opens one. It asks the owner over HTTP and leaves, and the owner
    answers from what it remembers asking for rather than from what it can see.

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


def instance_name(port):
    """One instance per port. The port is the thing actually being contended, and naming it that
    way still allows a second copy on a different one when that is deliberate."""
    return r"Local\WhiskeyTastingBook.%d" % int(port)


_INSTANCE_HANDLES = []


def claim_single_instance(name):
    """True when this process is the only one. Creating a named mutex is atomic; probing a port
    and then binding it is not, and the gap between the two is exactly one double-click wide."""
    if os.name != "nt":
        return True
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return True                          # cannot tell — do not stand in the way of starting
    if ctypes.get_last_error() == 183:       # ERROR_ALREADY_EXISTS
        return False
    _INSTANCE_HANDLES.append(handle)         # held for the life of the process; Windows frees it
    return True


def find_app_window(title="Whiskey Tasting Book"):
    """The window belongs to the browser rather than to us, so it can only be found by what it
    says. The match is exact: a window merely *mentioning* the app — an editor, a folder — is not
    it, and a window whose server has gone renames itself so it cannot be mistaken for a live one."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    visitor = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found = []

    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        if buf.value == title:
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(visitor(visit), 0)
    return found[0] if found else None


def raise_window(hwnd):
    """Restore it if it was minimised, then bring it forward. Windows may refuse the foreground
    change and flash the taskbar button instead, which is still the right outcome."""
    import ctypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)           # SW_RESTORE
    user32.SetForegroundWindow(hwnd)


def show_window(state, grace=20.0):
    """Put the app's window in front, without ever creating a second one.

    Only the copy that owns the app can make this decision, which is why every route to a window
    ends up here. "Is there a window?" cannot be answered by looking: Edge takes seconds to draw
    one, so during that gap a second copy sees nothing, concludes there is no window, and opens
    the very duplicate we are avoiding. The owner does not have to look — it remembers asking,
    and a request younger than `grace` counts as a window that is on its way."""
    hwnd = find_app_window()
    if hwnd:
        raise_window(hwnd)
        return False
    if time.monotonic() - state.get("opened", 0.0) < grace:
        return False                         # one is already on its way
    state["opened"] = time.monotonic()
    return open_window(state["url"])


def request_window(url, timeout=25.0):
    """Ask the copy that owns the app to show its window, and keep asking while it starts up.

    A second launch has no business opening a window itself — it cannot know what the owner has
    already done. It asks, and goes away."""
    import urllib.request
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            ask = urllib.request.Request(url + "/api/window", method="POST", data=b"")
            with urllib.request.urlopen(ask, timeout=3):
                return True
        except Exception:                    # noqa: BLE001 — it is still starting, or it is gone
            time.sleep(0.4)
    return False


def ours_at(url, timeout=2.0):
    """Whether the thing already on that port is this app or something unrelated.

    The mutex is the real guard, but it is allowed to fail open — if Windows will not give us one
    we would rather start than refuse to. This is the backstop underneath it, and it has to tell
    "the app is already running" apart from "port 8765 belongs to something else", because the
    first deserves a window and the second deserves an explanation."""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=timeout) as answer:
            return bool(json.load(answer).get("ok"))
    except Exception:                        # noqa: BLE001 — anything at all means "not ours"
        return False


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

    @application.post("/api/window")
    def _window():
        """A second launch asks for the window here rather than opening one of its own."""
        show_window(state)
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


def build_tray(state):
    """Open is the default action, so double-clicking the tray icon puts the window back — which
    is what people try first. It goes through show_window like everything else, so it cannot
    stack up windows either."""
    import pystray
    return pystray.Icon("whiskey_tasting_book", tray_image(), "Whiskey Tasting Book", pystray.Menu(
        pystray.MenuItem("Open", lambda icon, item: show_window(state), default=True),
        pystray.MenuItem("Quit", lambda icon, item: icon.stop()),
    ))


def wait_in_tray(state):
    """Blocks until Quit is chosen. None means no tray could be created — the caller then needs
    something that can still be stopped."""
    try:
        icon = build_tray(state)
    except Exception:                          # noqa: BLE001 — any failure here means "no tray"
        return None
    try:
        icon.run()
    except Exception:                          # noqa: BLE001
        return None
    return 0


def wait_for_exit(state, tray=True):
    """Closing the window is no longer the end of the program. The server stays up behind the
    tray icon; Quit there ends it.

    The fallback matters more than the tray does. A windowless app with no way to quit is worse
    than one that closes too eagerly, so if the tray will not start we go back to stopping when
    the window goes."""
    if tray:
        done = wait_in_tray(state)
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

    if not claim_single_instance(instance_name(port)):
        # Another copy owns the app. Ask it for a window and get out of the way; deciding here
        # whether one already exists is the mistake that produces two.
        print(f"Already running at {url} - asking it to show its window.")
        if not request_window(url):
            report_problem("The app is already running but is not responding.\n\n"
                           "Quit it from the icon in the notification area, then start it again.")
            return 1
        return 0

    if port_open(host, port):
        # We hold the mutex, so in principle this is not us — but the mutex fails open, so find
        # out who is actually answering before accusing an innocent program of squatting.
        if ours_at(url):
            print(f"Already running at {url} - asking it to show its window.")
            request_window(url)
            return 0
        report_problem(f"Port {port} is already in use by another program.\n\n"
                       "Close whatever is using it, or set a different port in config.yaml.")
        return 1

    state = {"last": time.monotonic(), "closing": None, "url": url, "opened": 0.0}
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

    show_window(state)
    return wait_for_exit(state, tray=not a.no_tray)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:                      # noqa: BLE001 — a windowless app must not fail silently
        import traceback
        report_problem(traceback.format_exc())
        raise SystemExit(1)
