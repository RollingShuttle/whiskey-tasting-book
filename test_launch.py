"""
test_launch.py — the launcher's moving parts.

    python test_launch.py

Opening a real browser window is not something a test should do, so what is checked here is
everything around it: finding a browser, knowing whether the port is already answering, and
building a command line that actually produces an application window rather than a tab.
"""
import socket
import tempfile
import threading
import unittest
from pathlib import Path

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
