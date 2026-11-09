"""
test_ios.py — the native iPhone shell in ios/, checked from Python.

    python tests/test_ios.py
    python -m unittest tests.test_ios

Only Xcode can compile this, and Xcode lives on a borrowed Mac that is opened once. So what is
checked here is the part that fails late or silently there: a project file naming a Swift file that
is not in the folder (Xcode shows it in red and the build stops), an object id used twice (Xcode
refuses to open the project at all), a build setting that quietly reverted (a TestFlight upload
stalls on the export-compliance question, or the bundle id no longer matches the Entra redirect
URI), a copy script that ships the PWA's service worker, an icon with an alpha channel (App Store
Connect rejects the build) — and the one that matters most: a page and a shell that disagree about
the names of the messages they pass each other, which fails with nothing in any log, just a sign-in
that never comes back.
"""

# Run straight from the shell, only tests/ is on the path; discovered from the repo root, only
# the root is. Put all three where imports can find them so both ways of running behave alike.
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT), str(ROOT / "tools")]

import json
import re
import struct
import unittest

IOS = ROOT / "ios"
PBXPROJ = IOS / "TastingBookShell.xcodeproj" / "project.pbxproj"
SOURCES = IOS / "TastingBookShell"
ICON = SOURCES / "Assets.xcassets" / "AppIcon.appiconset" / "AppIcon.png"
DOCS = ROOT / "docs"
# The page's side of the bridge. native.js is the transport; graph.js and store.js ask it for
# sign-in and backup; app.js listens for the shell's unsolicited events.
WEB_FILES = ("native.js", "graph.js", "store.js", "sync.js", "app.js")

# The protocol, as both halves have to speak it. Bridge.swift's header is the native statement of it.
WEB_TO_NATIVE = {"ready", "auth.status", "auth.signIn", "auth.token", "auth.signOut",
                 "backup.save", "backup.load"}
NATIVE_TO_WEB = {"hello", "auth.status.result", "auth.result", "auth.token.result",
                 "auth.signOut.result", "backup.result", "foreground"}


def text(path):
    return Path(path).read_text(encoding="utf-8")


def pbx():
    return text(PBXPROJ)


def pbx_code():
    """The project file with its /* comments */ removed. The comments repeat file names and ids
    for human readers; the structural checks below look past them."""
    return re.sub(r"/\*.*?\*/", "", pbx(), flags=re.DOTALL)


def setting(name):
    """Every value the project gives one build setting — one entry per configuration that sets it,
    so a target-level setting comes back twice (Debug and Release) when both are right."""
    return re.findall(rf"^\s*{re.escape(name)} = (.+);$", pbx(), flags=re.MULTILINE)


def section(name):
    m = re.search(rf"/\* Begin {name} section \*/(.*?)/\* End {name} section \*/", pbx(), re.DOTALL)
    return m.group(1) if m else ""


def swift_code(name):
    """A Swift file with its comments removed, for the checks that are about what the code does
    rather than what the comments say it avoids."""
    src = text(SOURCES / name)
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.DOTALL)
    # A line comment starts at a // that follows whitespace or the line start — not the // inside
    # "://auth", which is a string literal this file's checks have to be able to see.
    return re.sub(r"(^|\s)//.*$", r"\1", src, flags=re.MULTILINE)


def swift_files_on_disk():
    return {p.name for p in SOURCES.glob("*.swift")}


def types_the_shell_handles():
    """The `case "..."` labels of Bridge.swift's switch: what the page may send."""
    return set(re.findall(r'^\s*case "([^"]+)":', text(SOURCES / "Bridge.swift"), flags=re.MULTILINE))


def types_the_shell_sends():
    """Every `"type": "..."` any Swift file puts in an event: what the page may receive."""
    found = set()
    for name in swift_files_on_disk():
        found.update(re.findall(r'"type": "([^"]+)"', text(SOURCES / name)))
    return found


class TestTheProjectFile(unittest.TestCase):
    """Hand-written pbxproj. Xcode gives no second chances with one of these."""

    def test_it_names_exactly_the_swift_files_in_the_folder(self):
        """A file on disk that the project does not list is silently not compiled — the app builds
        without it and fails at runtime; a listed file that is not on disk stops the build."""
        referenced = set(re.findall(r"path = (\w+\.swift);", pbx()))
        self.assertEqual(referenced, swift_files_on_disk())

    def test_every_swift_file_is_in_the_sources_phase(self):
        """A file reference alone only shows the file in the navigator; compiling it takes an
        entry in the Sources build phase too."""
        compiled = set(re.findall(r"/\* (\w+\.swift) in Sources \*/", section("PBXSourcesBuildPhase")))
        self.assertEqual(compiled, swift_files_on_disk())

    def test_every_object_id_is_defined_exactly_once(self):
        defined = re.findall(r"^\t\t([0-9A-F]{24})(?: /\*.*?\*/)? = \{", pbx(), flags=re.MULTILINE)
        duplicates = {i for i in defined if defined.count(i) > 1}
        self.assertEqual(duplicates, set(), f"object ids defined more than once: {duplicates}")
        self.assertGreater(len(defined), 30, "the object graph is suspiciously small")

    def test_every_referenced_id_is_defined(self):
        """A dangling reference is the classic hand-editing slip: Xcode either refuses the project
        or opens it with a phantom file."""
        defined = set(re.findall(r"^\t\t([0-9A-F]{24})(?: /\*.*?\*/)? = \{", pbx(), flags=re.MULTILINE))
        referenced = set(re.findall(r"\b[0-9A-F]{24}\b", pbx_code()))
        self.assertEqual(referenced - defined, set(), "referenced but never defined")
        self.assertEqual(defined - referenced, set(), "defined but never referenced")

    def test_the_root_object_is_the_project(self):
        root = re.search(r"rootObject = ([0-9A-F]{24})", pbx()).group(1)
        self.assertRegex(pbx_code(), rf"{root}\s*=\s*\{{\s*isa = PBXProject;")

    def test_the_ids_are_not_the_templates(self):
        """The workout app's project uses ids like B00000000000000000000001. Reusing them would make
        two projects on one Mac share object identity in Xcode's caches."""
        self.assertNotRegex(pbx(), r"[A-F]0000000000000000000000\d")

    def test_the_build_phases_run_in_order(self):
        """The web files are copied after Resources, into the folder Resources has just created."""
        phases = re.search(r"buildPhases = \((.*?)\);", section("PBXNativeTarget"), re.DOTALL).group(1)
        names = re.findall(r"/\* (.+?) \*/", phases)
        self.assertEqual(names, ["Sources", "Frameworks", "Resources", "Copy web files"])

    def test_the_copy_phase_runs_copy_web_sh_on_every_build(self):
        """Without alwaysOutOfDate Xcode skips the phase when no *input* changed — and the script
        declares none, so a docs/ edit would never reach the phone."""
        phase = section("PBXShellScriptBuildPhase")
        self.assertIn("alwaysOutOfDate = 1;", phase)
        self.assertIn("copy-web.sh", phase)
        self.assertIn("/bin/sh ", phase, "run through the shell so the script's mode bit does not matter")

    def test_the_project_format_matches_the_template_xcode_accepted(self):
        self.assertIn("objectVersion = 56;", pbx())
        self.assertIn("LastUpgradeCheck = 2720;", pbx())
        self.assertEqual(setting("SWIFT_VERSION"), ["5.0", "5.0"])


class TestTheBuildSettings(unittest.TestCase):
    """Each of these is set on both configurations; a value that is right in Debug and wrong in
    Release ships wrong, because TestFlight archives are Release."""

    def test_signing_and_identity(self):
        self.assertEqual(setting("DEVELOPMENT_TEAM"), ["4RLD77Y492"] * 2)
        self.assertEqual(setting("PRODUCT_BUNDLE_IDENTIFIER"), ["com.mikeyang.whiskey"] * 2)
        self.assertEqual(setting("CODE_SIGN_STYLE"), ["Automatic"] * 2)
        self.assertEqual(setting("INFOPLIST_KEY_CFBundleDisplayName"), ['"Tasting Book"'] * 2)

    def test_the_bundle_id_is_the_one_in_the_redirect_scheme(self):
        """The Entra redirect URI is msauth.<bundle id>://auth. Change one without the other and
        the sign-in sheet opens, Microsoft refuses, and nothing in the app says why."""
        bundle = setting("PRODUCT_BUNDLE_IDENTIFIER")[0]
        self.assertIn(f"msauth.{bundle}://auth", text(IOS / "README.md"))

    def test_testflight_never_asks_about_encryption(self):
        """Without this key every upload sits at Missing Compliance until someone answers a form."""
        self.assertEqual(setting("INFOPLIST_KEY_ITSAppUsesNonExemptEncryption"), ["NO"] * 2)

    def test_deployment_target_and_script_sandboxing(self):
        """Sandboxed script phases may not read outside the project folder — and copy-web.sh reads
        ../docs, which is exactly that."""
        self.assertEqual(setting("IPHONEOS_DEPLOYMENT_TARGET"), ["17.0"] * 2)
        self.assertEqual(setting("ENABLE_USER_SCRIPT_SANDBOXING"), ["NO"] * 2)

    def test_the_info_plist_is_generated(self):
        self.assertEqual(setting("GENERATE_INFOPLIST_FILE"), ["YES"] * 2)
        self.assertEqual(setting("INFOPLIST_KEY_UILaunchScreen_Generation"), ["YES"] * 2)
        self.assertEqual(setting("INFOPLIST_KEY_UIApplicationSceneManifest_Generation"), ["YES"] * 2)

    def test_the_app_is_dark_only_with_a_light_status_bar(self):
        """The page declares color-scheme dark and paints #12100E. A light system style would put a
        black status-bar clock on it."""
        self.assertEqual(setting("INFOPLIST_KEY_UIUserInterfaceStyle"), ["Dark"] * 2)
        self.assertEqual(setting("INFOPLIST_KEY_UIStatusBarStyle"), ["UIStatusBarStyleLightContent"] * 2)
        self.assertEqual(setting("INFOPLIST_KEY_UIViewControllerBasedStatusBarAppearance"), ["NO"] * 2)

    def test_iphone_portrait_only(self):
        self.assertEqual(setting("TARGETED_DEVICE_FAMILY"), ["1"] * 2)
        self.assertEqual(setting("INFOPLIST_KEY_UISupportedInterfaceOrientations"),
                         ["UIInterfaceOrientationPortrait"] * 2)

    def test_the_backup_file_is_visible_in_the_files_app(self):
        self.assertEqual(setting("INFOPLIST_KEY_UIFileSharingEnabled"), ["YES"] * 2)
        self.assertEqual(setting("INFOPLIST_KEY_LSSupportsOpeningDocumentsInPlace"), ["YES"] * 2)

    def test_the_version_numbers_start_at_one(self):
        self.assertEqual(setting("MARKETING_VERSION"), ["1.0"] * 2)
        self.assertEqual(setting("CURRENT_PROJECT_VERSION"), ["1"] * 2)


class TestCopyWeb(unittest.TestCase):
    def setUp(self):
        self.script = text(IOS / "copy-web.sh")

    def test_it_is_a_posix_shell_script_that_stops_on_error(self):
        self.assertTrue(self.script.startswith("#!/bin/sh\n"))
        self.assertIn("set -e", self.script)

    def test_it_copies_from_docs(self):
        """docs/ is the phone client, fixed by GitHub Pages; the shell has no copy of its own."""
        self.assertIn('SRC="$SRCROOT/../docs"', self.script)

    def test_it_leaves_the_service_worker_out(self):
        """A service worker cannot register on the app:// scheme, and the bundle is the cache."""
        self.assertRegex(self.script, r"sw\.js\)\s*;;")

    def test_it_keeps_no_list_of_file_names(self):
        """The workout template copied a named list. A list has to be remembered every time docs/
        grows, and the first time it is not, a file is missing from the phone with no error."""
        for name in (p.name for p in DOCS.iterdir() if p.name != "sw.js"):
            self.assertNotIn(name, self.script, f"{name} is listed by name — copy everything instead")

    def test_it_copies_folders_too(self):
        self.assertIn("cp -R", self.script)

    def test_it_guards_the_unmatched_glob(self):
        """An empty docs/ leaves the literal '*' in the loop; cp would then fail the build."""
        self.assertIn('[ -e "$f" ] || continue', self.script)


class TestTheIcon(unittest.TestCase):
    def test_it_is_a_1024_square_png_with_no_alpha_channel(self):
        """App Store Connect rejects an app icon with transparency, and TestFlight uploads go
        through the same check."""
        data = ICON.read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", data[16:24])
        self.assertEqual((width, height), (1024, 1024))
        colour_type = data[25]
        self.assertIn(colour_type, (0, 2), "the icon has an alpha channel")
        self.assertNotIn(b"tRNS", data, "the icon carries transparency")

    def test_the_catalog_points_at_it(self):
        contents = json.loads(text(ICON.parent / "Contents.json"))
        image = contents["images"][0]
        self.assertEqual(image["filename"], ICON.name)
        self.assertEqual(image["size"], "1024x1024")
        self.assertEqual(image["idiom"], "universal")
        self.assertTrue((ICON.parent / image["filename"]).exists())
        self.assertEqual(setting("ASSETCATALOG_COMPILER_APPICON_NAME"), ["AppIcon"] * 2)


class TestTheShell(unittest.TestCase):
    """What can be said about the Swift without compiling it."""

    def test_the_bridge_handles_the_protocol_and_nothing_else(self):
        self.assertEqual(types_the_shell_handles(), WEB_TO_NATIVE)
        self.assertEqual(types_the_shell_sends(), NATIVE_TO_WEB)

    def test_the_bridge_announces_the_two_capabilities(self):
        self.assertIn('static let caps = ["auth", "backup"]', text(SOURCES / "Bridge.swift"))
        self.assertIn('static let version = "1.0"', text(SOURCES / "Bridge.swift"))

    def test_every_reply_echoes_the_request_id(self):
        """The page matches answers to questions by id. A reply built with send() instead of
        reply() would arrive with no id and be dropped on the floor."""
        src = swift_code("Bridge.swift")
        replies = re.findall(r'(\w+)\(id, \["type": "([\w.]+)"', src)
        self.assertEqual({t for _, t in replies}, NATIVE_TO_WEB - {"hello", "foreground"})
        self.assertEqual({fn for fn, _ in replies}, {"reply"})
        self.assertIn('if let id { event["id"] = id }', src)

    def test_tokens_live_in_the_keychain_and_never_in_a_log(self):
        auth = text(SOURCES / "MicrosoftAuth.swift")
        self.assertIn("kSecClassGenericPassword", auth)
        self.assertIn('"com.mikeyang.whiskey.auth"', auth)
        self.assertIn("kSecAttrAccessibleAfterFirstUnlock", auth)
        for name in swift_files_on_disk():
            code = swift_code(name)
            for sink in ("print(", "NSLog(", "os_log", "Logger("):
                self.assertNotIn(sink, code, f"{name} logs; a token could end up in the console")

    def test_the_identity_comes_from_the_page_not_the_binary(self):
        """docs/config.js is the one source of truth. A client id or redirect scheme compiled into
        the app would be a second one, and they would drift."""
        code = swift_code("MicrosoftAuth.swift")
        self.assertIn('redirectScheme + "://auth"', code)
        self.assertNotIn("msauth", code)
        self.assertNotRegex(code, r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}", "a client id is hardcoded")
        self.assertNotIn("login.microsoftonline.com", code, "the authority is hardcoded")

    def test_the_sign_in_is_pkce_with_s256_in_the_system_sheet(self):
        code = swift_code("MicrosoftAuth.swift")
        for needle in ("ASWebAuthenticationSession(", "SHA256.hash", '"S256"', "code_verifier",
                       "code_challenge", '"offline_access"', "prefersEphemeralWebBrowserSession = false",
                       "/oauth2/v2.0/authorize", "/oauth2/v2.0/token", "grant_type"):
            self.assertIn(needle, code, f"{needle} missing from the sign-in")

    def test_a_dead_refresh_token_asks_for_a_sign_in_and_a_dead_network_does_not(self):
        code = swift_code("MicrosoftAuth.swift")
        self.assertIn('"invalid_grant"', code)
        self.assertIn('"interaction_required"', code)
        self.assertIn("interactive: true", code)
        self.assertIn("interactive: false", code)

    def test_the_page_is_served_from_the_bundle_not_the_network(self):
        """A shell that loaded the live Pages site would be Plan B (guide/IOS-APP.md): daily
        sign-in, and offline only as good as the service worker. This one bundles the page."""
        handler = swift_code("AppSchemeHandler.swift")
        self.assertIn('static let scheme = "app"', handler)
        self.assertIn('static let host = "local"', handler)
        self.assertIn('"webmanifest"', handler)
        self.assertIn('"json"', handler)
        for name in swift_files_on_disk():
            self.assertNotIn("github.io", swift_code(name), f"{name} points at the live site")

    def test_the_web_view_is_dark_before_the_page_paints(self):
        """isOpaque false plus the page's own background, or a white frame flashes at launch."""
        view = swift_code("WebView.swift")
        self.assertIn("isOpaque = false", view)
        self.assertIn("18.0 / 255.0, green: 16.0 / 255.0, blue: 14.0 / 255.0", view)
        self.assertIn("contentInsetAdjustmentBehavior = .never", view)
        self.assertIn("0.0706, green: 0.0627, blue: 0.0549", swift_code("TastingBookShellApp.swift"))

    def test_the_delegate_only_reports_the_foreground(self):
        delegate = swift_code("AppDelegate.swift")
        self.assertIn('"foreground"', delegate)
        self.assertNotIn("UserNotifications", delegate)

    def test_the_gitignore_keeps_xcode_state_out(self):
        self.assertIn("xcuserdata/", text(IOS / ".gitignore"))


@unittest.skipIf(not all((DOCS / f).exists() for f in WEB_FILES),
                 "the page's side of the bridge is not in docs/ yet: " +
                 ", ".join(f for f in WEB_FILES if not (DOCS / f).exists()))
class TestTheProtocolAgainstThePage(unittest.TestCase):
    """The two halves are written separately and never compiled together. A type string that one
    side spells differently fails in the only way a bridge can: silently."""

    NAME = re.compile(r"""['"`]((?:auth|backup)\.[A-Za-z][\w.]*|ready|hello|foreground)['"`]""")

    def names_the_page_spells_out(self):
        """Every quoted protocol-looking name in the web files. The page does not spell out every
        reply: native.js derives most of them as <request>.result, so the checks below allow that
        derivation rather than demanding the literal."""
        found = set()
        for name in WEB_FILES:
            found.update(self.NAME.findall(text(DOCS / name)))
        return found

    def test_the_page_sends_exactly_what_the_shell_handles(self):
        """A request the shell has no case for is dropped in silence; a case the page never sends
        is dead code wearing the protocol's name."""
        page = self.names_the_page_spells_out()
        handles, sends = types_the_shell_handles(), types_the_shell_sends()
        self.assertEqual(handles - page, set(), "the shell handles requests the page never sends")
        self.assertEqual(page - handles - sends, set(), "the page names messages the shell does not know")

    def test_the_page_expects_exactly_what_the_shell_sends(self):
        """Every reply and event the shell sends must be one the page can receive: spelled out, or
        derivable as <request>.result from a request the page makes. The two exceptions to that
        derivation — auth.result for auth.signIn, backup.result for backup.load — have to be
        written out on the page, and this is where a typo in either would show."""
        page = self.names_the_page_spells_out()
        handles, sends = types_the_shell_handles(), types_the_shell_sends()
        derivable = {f"{request}.result" for request in page & handles}
        self.assertEqual(sends - page - derivable, set(), "the shell sends what the page cannot receive")
        self.assertEqual({t for t in page if t.endswith(".result")} - sends, set(),
                         "the page waits for replies the shell never sends")

    def test_the_redirect_scheme_the_page_sends_is_the_bundle_id(self):
        """The page tells the shell which scheme to listen on; Entra was told msauth.<bundle id>.
        If the bundle id ever changes, the page's constant and the Entra entry change with it, or
        Microsoft refuses the redirect and the sheet closes on nothing."""
        m = re.search(r'NATIVE_REDIRECT_SCHEME\s*=\s*"([^"]+)"', text(DOCS / "graph.js"))
        self.assertIsNotNone(m, "graph.js lost its NATIVE_REDIRECT_SCHEME constant")
        self.assertEqual(m.group(1), f"msauth.{setting('PRODUCT_BUNDLE_IDENTIFIER')[0]}")


class TestTheMacUpdater(unittest.TestCase):
    def setUp(self):
        self.script = text(ROOT / "update-mac.command")

    def test_it_is_a_bash_script_that_runs_in_its_own_folder(self):
        """Double-clicked from Finder, the working directory is the home folder, not the repo."""
        self.assertTrue(self.script.startswith("#!/bin/bash\n"))
        self.assertIn('cd "$(dirname "$0")"', self.script)

    def test_it_only_fast_forwards_and_keeps_local_signing_edits(self):
        """A merge would make a commit on a machine nobody looks at; a refusal over the pbxproj
        edit Xcode makes when a Team is picked would stop every update."""
        self.assertIn("git pull --ff-only --autostash", self.script)

    def test_it_reports_the_signing_lines_of_this_project(self):
        self.assertIn("ios/TastingBookShell.xcodeproj/project.pbxproj", self.script)
        self.assertIn("DEVELOPMENT_TEAM|PRODUCT_BUNDLE_IDENTIFIER", self.script)


class TestTheReadme(unittest.TestCase):
    def setUp(self):
        self.readme = text(IOS / "README.md")

    def test_it_tells_the_owner_the_entra_step(self):
        self.assertIn("Mobile and desktop applications", self.readme)
        self.assertIn("msauth.com.mikeyang.whiskey://auth", self.readme)

    def test_it_covers_release_and_updating(self):
        for needle in ("update-mac.command", "TestFlight Internal Only", "Xcode Cloud",
                       "Product → Archive", "Develop", "airplane mode"):
            self.assertIn(needle, self.readme, f"README does not mention {needle}")


if __name__ == "__main__":
    unittest.main()
