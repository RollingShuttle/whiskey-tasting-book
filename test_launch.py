"""
test_launch.py — the launcher's moving parts.

    python test_launch.py

Opening a real browser window is not something a test should do, so what is checked here is
everything around it: finding a browser, knowing whether the port is already answering, and
building a command line that actually produces an application window rather than a tab.
"""
import socket
import tempfile
import time
import threading
import unittest
from pathlib import Path
from unittest import mock

import launch


class TestFindBrowser(unittest.TestCase):
    def test_nothing_found_is_not_an_error(self):
        """No Chromium browser just means falling back to the default one."""
        self.assertIsNone(launch.find_browser([r"Z:\nope\msedge.exe"]))

    def test_the_first_existing_candidate_wins(self):
        with tempfile.TemporaryDirectory() as d:
            real = Path(d) / "msedge.exe"
            real.write_bytes(b"")
            found = launch.find_browser([r"Z:\nope\msedge.exe", str(real)])
            self.assertEqual(found, str(real))


class TestWindowCommand(unittest.TestCase):
    def test_it_asks_for_an_app_window(self):
        cmd = launch.window_command("msedge.exe", "http://127.0.0.1:8765", "C:/profile")
        self.assertIn("--app=http://127.0.0.1:8765", cmd)
        self.assertEqual(cmd[0], "msedge.exe")

    def test_it_uses_its_own_profile(self):
        """Without this the browser hands the request to a copy of itself already running and
        exits immediately, which would look like the app closing the instant it opened."""
        cmd = launch.window_command("msedge.exe", "http://x", "C:/profile")
        self.assertIn("--user-data-dir=C:/profile", cmd)

    def test_it_skips_the_first_run_noise(self):
        cmd = launch.window_command("msedge.exe", "http://x", "C:/profile")
        self.assertIn("--no-first-run", cmd)
        self.assertIn("--no-default-browser-check", cmd)

    def test_the_profile_lives_outside_the_project(self):
        here = Path(__file__).resolve().parent
        self.assertNotIn(here, launch.profile_path().parents)


class TestPortProbe(unittest.TestCase):
    """A listener that accepts, like a real server. One that only binds fills its backlog after
    a probe or two and then refuses, which is a property of the fake and not of the code."""

    def listener(self):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(16)
        server.settimeout(0.2)
        stop = threading.Event()

        def accept_loop():
            while not stop.is_set():
                try:
                    conn, _ = server.accept()
                    conn.close()
                except OSError:
                    pass

        thread = threading.Thread(target=accept_loop, daemon=True)
        thread.start()
        self.addCleanup(server.close)
        self.addCleanup(stop.set)
        return server.getsockname()[1]

    def test_a_closed_port_reads_as_closed(self):
        with socket.socket() as s:            # bind then close to get a port nobody holds
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.assertFalse(launch.port_open("127.0.0.1", port))

    def test_a_listening_port_reads_as_open(self):
        port = self.listener()
        self.assertTrue(launch.port_open("127.0.0.1", port))
        self.assertTrue(launch.wait_for_port("127.0.0.1", port, timeout=3))

    def test_binding_everywhere_is_probed_on_loopback(self):
        """The server may bind 0.0.0.0 for the home network, but you cannot connect *to* that."""
        self.assertTrue(launch.port_open("0.0.0.0", self.listener()))

    def test_waiting_gives_up_rather_than_hanging(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.assertFalse(launch.wait_for_port("127.0.0.1", port, timeout=0.5))


class TestLifecycle(unittest.TestCase):
    """The window cannot be waited on, so the page reports for itself."""

    def app(self):
        import tempfile as tf
        import app as app_mod
        d = tf.mkdtemp()
        application = app_mod.create_app(
            "config.yaml",
            app_folder=str(Path(d) / "journal"),
            snapshot_path=str(Path(d) / "missing.json"),
            master="Z:/nonexistent/x.xlsx",
            rollup=str(Path(d) / "r.xlsx"),
            backups=str(Path(d) / "b"),
        )
        state = {"last": 0.0, "closing": None}
        launch.attach_lifecycle(application, state)
        return application.test_client(), state

    def test_a_heartbeat_marks_the_window_alive(self):
        c, state = self.app()
        self.assertEqual(c.post("/api/heartbeat").status_code, 200)
        self.assertGreater(state["last"], 0)

    def test_goodbye_starts_the_countdown(self):
        c, state = self.app()
        self.assertEqual(c.post("/api/goodbye").status_code, 204)
        self.assertIsNotNone(state["closing"])

    def test_a_heartbeat_cancels_a_pending_shutdown(self):
        """A reload fires goodbye too, and so does closing one of two windows."""
        c, state = self.app()
        c.post("/api/goodbye")
        c.post("/api/heartbeat")
        self.assertIsNone(state["closing"])

    def test_it_waits_while_the_window_is_open(self):
        state = {"last": time.monotonic(), "closing": None}
        started = time.monotonic()
        done = []
        t = threading.Thread(target=lambda: done.append(launch.wait_until_closed(state, 0.2, 1.0)),
                             daemon=True)
        t.start()
        time.sleep(0.4)
        self.assertEqual(done, [], "it gave up while the window was still open")
        state["closing"] = time.monotonic()
        t.join(timeout=3)
        self.assertEqual(done, [0])
        self.assertLess(time.monotonic() - started, 3)

    def test_it_gives_up_if_nothing_ever_speaks_to_it(self):
        """Only a leak guard: without it a crashed window would leave the port held forever."""
        state = {"last": time.monotonic() - 10, "closing": None}
        self.assertEqual(launch.wait_until_closed(state, grace=5, idle=1.0), 0)


class TestTray(unittest.TestCase):
    """Once the window is no longer the way out, the tray menu is the contract: it is the only
    thing standing between the user and Task Manager."""

    def test_the_menu_offers_open_and_quit(self):
        icon = launch.build_tray("http://127.0.0.1:8765")
        self.assertEqual([str(i.text) for i in icon.menu], ["Open", "Quit"])

    def test_opening_is_the_default_action(self):
        """Double-clicking the tray icon is what people try first; it should bring the window back."""
        icon = launch.build_tray("http://x")
        self.assertEqual([str(i.text) for i in icon.menu if i.default], ["Open"])

    def test_it_wears_the_app_icon(self):
        self.assertGreaterEqual(min(launch.tray_image().size), 16)

    def test_the_window_closing_does_not_end_it(self):
        """The whole point: a closed window leaves the server up behind the tray icon."""
        ran = []
        icon = mock.Mock(run=lambda: ran.append("ran"))
        with mock.patch.object(launch, "build_tray", return_value=icon):
            closed = {"last": 0.0, "closing": 0.0}      # a window shut long ago
            self.assertEqual(launch.wait_for_exit(closed, "http://x", tray=True), 0)
        self.assertEqual(ran, ["ran"], "it waited on the window instead of the tray")

    def test_without_a_tray_it_still_closes_with_the_window(self):
        """A resident app with no way to quit would be worse than one that quits too eagerly."""
        gone = {"last": time.monotonic(), "closing": time.monotonic() - 99}   # window already shut
        with mock.patch.object(launch, "build_tray", side_effect=RuntimeError("no tray here")):
            self.assertEqual(launch.wait_for_exit(gone, "http://x", tray=True), 0)

    def test_no_tray_asked_for_skips_it_entirely(self):
        gone = {"last": time.monotonic(), "closing": time.monotonic() - 99}
        with mock.patch.object(launch, "build_tray") as built:
            self.assertEqual(launch.wait_for_exit(gone, "http://x", tray=False), 0)
        built.assert_not_called()


class TestServesInThread(unittest.TestCase):
    def test_the_app_answers_from_a_background_thread(self):
        """The server runs inside the launcher process so it cannot outlive the window and leave
        a port held by something invisible."""
        import app as app_mod
        with tempfile.TemporaryDirectory() as d:
            application = app_mod.create_app(
                "config.yaml",
                app_folder=str(Path(d) / "journal"),
                snapshot_path=str(Path(d) / "missing.json"),
                master="Z:/nonexistent/x.xlsx",
                rollup=str(Path(d) / "r.xlsx"),
                backups=str(Path(d) / "b"),
            )
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]

            t = threading.Thread(target=launch.serve, args=(application, "127.0.0.1", port),
                                 daemon=True)
            t.start()
            self.assertTrue(launch.wait_for_port("127.0.0.1", port, timeout=15),
                            "the server never came up")

            import urllib.request
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as r:
                self.assertEqual(r.status, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
