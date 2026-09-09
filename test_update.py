"""
test_update.py — the updater.

    python test_update.py

This is the one script that runs unattended on a machine that is not this one, so what matters is
the paths it must NOT take: overwriting work you have not committed, rebuilding while the app is
holding its own executable open, or reporting success after a failed build.
"""
import unittest
from pathlib import Path
from unittest import mock

import update

HERE = Path(__file__).resolve().parent


def fake_run(script):
    """A stand-in for update.run, driven by a dict of {first-argument-word: (ok, output)}.
    Anything not listed succeeds silently, which is the boring case."""
    calls = []

    def run(args, **kw):
        calls.append(list(args))
        key = next((k for k in script if k in " ".join(str(a) for a in args)), None)
        return script[key] if key else (True, "")

    run.calls = calls
    return run


class TestItRefusesToClobberYourWork(unittest.TestCase):
    def test_local_changes_stop_the_update(self):
        """git pull would either refuse or overwrite. Stopping first, and naming the file, beats
        either — this runs unattended and nobody is watching the output."""
        run = fake_run({"status": (True, " M app.py\n")})
        with mock.patch.object(update, "run", run):
            self.assertFalse(update.check_repo())

    def test_a_clean_checkout_is_fine(self):
        run = fake_run({"status": (True, "")})
        with mock.patch.object(update, "run", run):
            self.assertTrue(update.check_repo())

    def test_untracked_files_are_not_local_changes(self):
        """config.yaml, data/ and the .exe are all untracked, and every machine has them."""
        run = fake_run({"status": (True, "")})
        with mock.patch.object(update, "run", run):
            update.check_repo()
        status = next(c for c in run.calls if "status" in c)
        self.assertIn("--untracked-files=no", status)

    def test_it_says_so_when_this_is_not_a_checkout(self):
        run = fake_run({"rev-parse --is-inside-work-tree": (False, "not a git repository")})
        with mock.patch.object(update, "run", run):
            self.assertFalse(update.check_repo())


class TestPulling(unittest.TestCase):
    def test_a_failed_pull_is_not_reported_as_up_to_date(self):
        run = fake_run({"pull": (False, "Could not resolve host github.com")})
        with mock.patch.object(update, "run", run):
            ok, changed = update.pull()
        self.assertFalse(ok)
        self.assertFalse(changed)

    def test_it_only_fast_forwards(self):
        """A merge here would create a commit on a machine nobody is going to look at."""
        run = fake_run({})
        with mock.patch.object(update, "run", run):
            update.pull()
        pull = next(c for c in run.calls if "pull" in c)
        self.assertIn("--ff-only", pull)

    def test_an_unchanged_head_means_nothing_new(self):
        run = fake_run({"rev-parse HEAD": (True, "abc123\n")})
        with mock.patch.object(update, "run", run):
            ok, changed = update.pull()
        self.assertTrue(ok)
        self.assertFalse(changed)


class TestTheOrderOfOperations(unittest.TestCase):
    """The sequence is the whole point: pull, then stop the app, then build."""

    def test_nothing_is_rebuilt_when_there_is_nothing_new(self):
        """The build takes twenty seconds and would otherwise run on every launch for nothing."""
        with mock.patch.object(update, "check_repo", return_value=True), \
             mock.patch.object(update, "pull", return_value=(True, False)), \
             mock.patch.object(update, "EXE", HERE / "test_update.py"), \
             mock.patch.object(update, "rebuild") as built, \
             mock.patch.object(update, "stop_app") as stopped:
            self.assertEqual(update.main(), 0)
        built.assert_not_called()
        stopped.assert_not_called()

    def test_a_missing_app_is_built_even_with_nothing_new(self):
        """A fresh clone has the code and no executable."""
        with mock.patch.object(update, "check_repo", return_value=True), \
             mock.patch.object(update, "pull", return_value=(True, False)), \
             mock.patch.object(update, "EXE", HERE / "no-such-app.exe"), \
             mock.patch.object(update, "stop_app", return_value=True), \
             mock.patch.object(update, "rebuild", return_value=True) as built, \
             mock.patch.object(update, "run", fake_run({})):
            self.assertEqual(update.main(), 0)
        built.assert_called_once()

    def test_the_app_is_stopped_before_the_build(self):
        """It holds its own .exe open, so a build with it running cannot replace it."""
        order = []
        with mock.patch.object(update, "check_repo", return_value=True), \
             mock.patch.object(update, "pull", return_value=(True, True)), \
             mock.patch.object(update, "stop_app", side_effect=lambda: order.append("stop") or True), \
             mock.patch.object(update, "rebuild", side_effect=lambda: order.append("build") or True), \
             mock.patch.object(update, "run", fake_run({})):
            update.main()
        self.assertEqual(order, ["stop", "build"])

    def test_it_gives_up_if_the_app_will_not_close(self):
        with mock.patch.object(update, "check_repo", return_value=True), \
             mock.patch.object(update, "pull", return_value=(True, True)), \
             mock.patch.object(update, "stop_app", return_value=False), \
             mock.patch.object(update, "rebuild") as built:
            self.assertEqual(update.main(), 1)
        built.assert_not_called()

    def test_a_failed_build_is_reported_as_a_failure(self):
        """Exiting zero here would leave the batch file saying it worked."""
        with mock.patch.object(update, "check_repo", return_value=True), \
             mock.patch.object(update, "pull", return_value=(True, True)), \
             mock.patch.object(update, "stop_app", return_value=True), \
             mock.patch.object(update, "rebuild", return_value=False):
            self.assertEqual(update.main(), 1)

    def test_libraries_are_reinstalled_only_when_they_changed(self):
        with mock.patch.object(update, "check_repo", return_value=True), \
             mock.patch.object(update, "pull", return_value=(True, True)), \
             mock.patch.object(update, "digest", side_effect=["one", "one"]), \
             mock.patch.object(update, "stop_app", return_value=True), \
             mock.patch.object(update, "rebuild", return_value=True), \
             mock.patch.object(update, "install_requirements") as installed, \
             mock.patch.object(update, "run", fake_run({})):
            update.main()
        installed.assert_not_called()

    def test_they_are_reinstalled_when_they_did(self):
        with mock.patch.object(update, "check_repo", return_value=True), \
             mock.patch.object(update, "pull", return_value=(True, True)), \
             mock.patch.object(update, "digest", side_effect=["one", "two"]), \
             mock.patch.object(update, "stop_app", return_value=True), \
             mock.patch.object(update, "rebuild", return_value=True), \
             mock.patch.object(update, "install_requirements", return_value=True) as installed, \
             mock.patch.object(update, "run", fake_run({})):
            update.main()
        installed.assert_called_once()


class TestTheBuildMatchesBuildExeBat(unittest.TestCase):
    """The rebuild has to produce the same app build_exe.bat does, or updating would quietly
    change how it behaves."""

    def test_it_carries_the_flags_the_app_depends_on(self):
        bat = (HERE / "build_exe.bat").read_text(encoding="utf-8")
        src = (HERE / "update.py").read_text(encoding="utf-8")
        for flag in ("--windowed", "--onefile", "--hidden-import", "pystray._win32"):
            self.assertIn(flag, bat)
            self.assertIn(flag, src, f"update.py builds without {flag}")

    def test_it_builds_beside_config_not_into_dist(self):
        """The app reads config.yaml and data/ from the folder it sits in."""
        self.assertIn("--distpath", (HERE / "update.py").read_text(encoding="utf-8"))


class TestTheLauncher(unittest.TestCase):
    def test_the_batch_file_waits_so_the_message_can_be_read(self):
        """Double-clicked, the window closes the moment it finishes."""
        bat = (HERE / "update.bat").read_text(encoding="utf-8")
        self.assertIn("pause", bat)
        self.assertIn("update.py", bat)

    def test_it_runs_in_its_own_folder(self):
        self.assertIn('cd /d "%~dp0"', (HERE / "update.bat").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
