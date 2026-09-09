"""
test_setup_machine.py — the second-machine setup.

    python test_setup_machine.py

config.yaml is the one file that cannot be shared between computers, so this is the only thing
standing between a fresh clone and an app that will not start. The parts worth checking are the
ones that would go wrong quietly: writing a config that points at the wrong folder, or overwriting
a config that already works.
"""
import os
import tempfile
import unittest
from pathlib import Path

import setup_machine as sm
import yaml

HERE = Path(__file__).resolve().parent


class TestPathFormatting(unittest.TestCase):
    def test_windows_paths_are_written_with_forward_slashes(self):
        """Backslashes in a YAML double-quoted string are escapes, so a Windows path written
        literally would either break the parse or silently lose a separator."""
        out = sm.as_yaml_path(Path(r"C:\Users\someone\OneDrive\Apps\Whiskey Tasting Book"))
        self.assertNotIn("\\", out)
        self.assertTrue(out.startswith("C:/Users/"))

    def test_a_written_path_survives_a_yaml_round_trip(self):
        p = sm.as_yaml_path(Path(r"C:\Users\someone\OneDrive\文档\Whiskey File"))
        parsed = yaml.safe_load('paths:\n  app_folder: "%s"\n' % p)
        self.assertEqual(parsed["paths"]["app_folder"], p)


class TestOneDriveDiscovery(unittest.TestCase):
    def test_every_sign_in_is_considered(self):
        """A work and a personal OneDrive are separate folders, and the whiskey files are only in
        one of them. Looking at just the first would find the wrong one half the time."""
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "OneDrive", Path(d) / "OneDrive - work.example"
            a.mkdir(), b.mkdir()
            keep = {k: os.environ.get(k) for k in ("OneDrive", "OneDriveCommercial")}
            os.environ["OneDrive"], os.environ["OneDriveCommercial"] = str(a), str(b)
            try:
                found = sm.onedrive_roots()
            finally:
                for k, v in keep.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
            self.assertIn(a, found)
            self.assertIn(b, found)

    def test_the_same_folder_is_not_listed_twice(self):
        with tempfile.TemporaryDirectory() as d:
            one = Path(d) / "OneDrive"
            one.mkdir()
            keep = {k: os.environ.get(k) for k in ("OneDrive", "OneDriveConsumer")}
            os.environ["OneDrive"] = os.environ["OneDriveConsumer"] = str(one)
            try:
                found = sm.onedrive_roots()
            finally:
                for k, v in keep.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
            self.assertEqual(found.count(one), 1)

    def test_the_app_folder_is_looked_for_where_it_actually_lives(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / sm.APP_FOLDER_TAIL).mkdir(parents=True)
            self.assertEqual(sm.find_app_folder([root]), root / sm.APP_FOLDER_TAIL)

    def test_a_workbook_is_found_however_deep_the_folder_is_localised(self):
        """The documents folder is named in the account's own language, so the path cannot be
        assumed — only the filename can."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            deep = root / "文档" / "Whiskey File"
            deep.mkdir(parents=True)
            (deep / sm.MASTER_NAME).write_bytes(b"")
            self.assertEqual(sm.find_file([root], sm.MASTER_NAME), deep / sm.MASTER_NAME)

    def test_a_missing_workbook_is_reported_rather_than_invented(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(sm.find_file([Path(d)], sm.MASTER_NAME))


class TestItRefusesToClobber(unittest.TestCase):
    def test_an_existing_config_is_left_alone(self):
        """Someone running this twice on a working machine must not lose the paths that work."""
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            (work / sm.TEMPLATE).write_text("paths:\n  app_folder: \"x\"\n", encoding="utf-8")
            existing = work / sm.TARGET
            existing.write_text("mine\n", encoding="utf-8")
            here, sm.__file__ = sm.__file__, str(work / "setup_machine.py")
            try:
                self.assertEqual(sm.main(), 0)
            finally:
                sm.__file__ = here
            self.assertEqual(existing.read_text(encoding="utf-8"), "mine\n")


class TestTheTemplateItStartsFrom(unittest.TestCase):
    def test_the_template_carries_the_keys_it_rewrites(self):
        """If a key were renamed in the template this would write a config missing it, and the
        app would fail somewhere far away from the cause."""
        text = (HERE / sm.TEMPLATE).read_text(encoding="utf-8")
        for key in ("master_workbook", "rollup_workbook", "app_folder"):
            self.assertIn(key + ":", text)

    def test_the_template_is_committed_and_the_config_is_not(self):
        """The template is the shareable half; config.yaml holds a username."""
        ignored = (HERE / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("config.yaml", ignored)
        self.assertTrue((HERE / sm.TEMPLATE).exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
