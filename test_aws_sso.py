"""Regression checks for legacy AWS SSO profile migration and sign-in."""

import configparser
import hashlib
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import setup_aws
import suno_studio as app


def profile_text(name="team", url="https://login.example.test/start", region="us-east-2"):
    section = "default" if name == "default" else f"profile {name}"
    return ("# Keep this file's comments and spacing.\n"
            f"[{section}]\n"
            f"sso_start_url = {url}\n"
            f"sso_region = {region}\n"
            "sso_account_id = 123456789012\n"
            "sso_role_name = Artist\n"
            "sso_registration_scopes = custom:scope\n"
            "custom_permission = KeepThisValue\n"
            "\n# This unrelated profile must remain byte-for-byte intact.\n"
            "[profile other]\n"
            "region = eu-west-1\n"
            "output = json\n")


def parsed(path):
    config = configparser.RawConfigParser()
    config.read(path)
    return config


def strip_migration(text, session, url, region):
    """Remove only the two expected additions, exposing any other text edits."""
    line = f"sso_session = {session}\n"
    assert line in text
    text = text.replace(line, "", 1)
    block = (f"\n[sso-session {session}]\n"
             f"sso_start_url = {url}\n"
             f"sso_region = {region}\n"
             "sso_registration_scopes = sso:account:access\n")
    assert text.endswith(block)
    return text[:-len(block)]


class SsoMigrationTests(unittest.TestCase):
    def test_migrates_named_profile_preserving_all_existing_text_and_values(self):
        original = profile_text()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "aws-config"
            path.write_text(original)

            self.assertTrue(setup_aws.migrate_sso_profile("team", config_path=path))

            migrated = path.read_text()
            config = parsed(path)
            session = config.get("profile team", "sso_session")
            self.assertEqual(config.get("profile team", "sso_account_id"), "123456789012")
            self.assertEqual(config.get("profile team", "sso_role_name"), "Artist")
            self.assertEqual(config.get("profile team", "custom_permission"), "KeepThisValue")
            self.assertEqual(config.get(f"sso-session {session}", "sso_start_url"),
                             "https://login.example.test/start")
            self.assertEqual(config.get(f"sso-session {session}", "sso_region"), "us-east-2")
            self.assertEqual(config.get(f"sso-session {session}", "sso_registration_scopes"),
                             "sso:account:access")
            self.assertEqual(strip_migration(migrated, session,
                                             "https://login.example.test/start", "us-east-2"),
                             original)

            backups = list(path.parent.glob(path.name + ".before-sso-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), original)
            self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)

            self.assertFalse(setup_aws.migrate_sso_profile("team", config_path=path))
            self.assertEqual(path.read_text(), migrated)
            self.assertEqual(len(list(path.parent.glob(path.name + ".before-sso-*"))), 1)

    def test_blank_profile_uses_aws_profile_then_default(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "aws-config"
            original = ("[default]\n"
                        "sso_start_url = https://default.example.test/start\n"
                        "sso_region = us-east-1\n"
                        "sso_account_id = 123456789012\n"
                        "sso_role_name = DefaultRole\n\n"
                        "[profile selected]\n"
                        "sso_start_url = https://selected.example.test/start\n"
                        "sso_region = us-west-2\n"
                        "sso_account_id = 123456789012\n"
                        "sso_role_name = SelectedRole\n")
            path.write_text(original)
            with mock.patch.dict(os.environ, {"AWS_CONFIG_FILE": str(path),
                                              "AWS_PROFILE": "selected"}):
                self.assertTrue(setup_aws.migrate_sso_profile(""))
                config = parsed(path)
                self.assertTrue(config.has_option("profile selected", "sso_session"))
                self.assertFalse(config.has_option("default", "sso_session"))

            with mock.patch.dict(os.environ, {"AWS_CONFIG_FILE": str(path), "AWS_PROFILE": ""}):
                self.assertTrue(setup_aws.migrate_sso_profile(""))
            self.assertTrue(parsed(path).has_option("default", "sso_session"))

    def test_collision_gets_distinct_session_and_keeps_existing_blocks(self):
        name = "team"
        url = "https://login.example.test/start"
        region = "us-east-2"
        base = "suno-studio-" + hashlib.sha256((name + url + region).encode()).hexdigest()[:12]
        original = (profile_text(name, url, region) +
                    f"\n[sso-session {base}]\n"
                    "sso_start_url = https://other.example.test/start\n"
                    "sso_region = us-west-1\n"
                    "custom = preserve\n")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config"
            path.write_text(original)

            self.assertTrue(setup_aws.migrate_sso_profile(name, config_path=path))

            config = parsed(path)
            session = config.get("profile team", "sso_session")
            self.assertEqual(session, base + "-1")
            self.assertEqual(config.get(f"sso-session {base}", "sso_start_url"),
                             "https://other.example.test/start")
            self.assertEqual(config.get(f"sso-session {session}", "sso_start_url"), url)
            self.assertEqual(strip_migration(path.read_text(), session, url, region), original)

    def test_incomplete_legacy_profile_is_rejected_without_writing(self):
        original = "[profile broken]\nsso_start_url = https://login.example.test/start\n"
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config"
            path.write_text(original)
            with self.assertRaisesRegex(ValueError, "Incomplete AWS SSO profile"):
                setup_aws.migrate_sso_profile("broken", config_path=path)
            self.assertEqual(path.read_text(), original)
            self.assertEqual(list(path.parent.glob(path.name + ".before-sso-*")), [])

    def test_modern_and_non_sso_profiles_are_noops(self):
        original = ("[profile modern]\nsso_session = company\nsso_account_id = 123\n"
                    "sso_role_name = Artist\n\n"
                    "[sso-session company]\nsso_start_url = https://login.example.test/start\n"
                    "sso_region = us-east-2\n\n"
                    "[profile plain]\nregion = us-east-1\n")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config"
            path.write_text(original)
            self.assertFalse(setup_aws.migrate_sso_profile("modern", config_path=path))
            self.assertFalse(setup_aws.migrate_sso_profile("plain", config_path=path))
            self.assertEqual(path.read_text(), original)
            self.assertEqual(list(path.parent.glob(path.name + ".before-sso-*")), [])

    def test_failed_atomic_replace_preserves_original_and_backup(self):
        original = profile_text()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config"
            path.write_text(original)
            with mock.patch.object(setup_aws.os, "replace", side_effect=OSError("replace failed")):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    setup_aws.migrate_sso_profile("team", config_path=path)
            self.assertEqual(path.read_text(), original)
            backups = list(path.parent.glob(path.name + ".before-sso-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), original)
            self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(path.parent.glob("tmp*")), [])


class SsoSigninTests(unittest.TestCase):
    def setUp(self):
        self.aws_state = mock.patch.dict(app.AWS_SETUP, {"status": "signing_in", "lines": [],
                                                         "checked": None})
        self.aws_state.start()
        self.addCleanup(self.aws_state.stop)

    def test_signin_migrates_before_launch_and_explains_session_expiry(self):
        events = []
        process = mock.Mock(stdout=iter(()))
        process.wait.return_value = 0
        version = subprocess.CompletedProcess(
            ["aws", "--version"], 0, stdout="", stderr="aws-cli/2.9.0 Python/3.11.0")

        def migrate(profile):
            events.append(("migrate", profile))
            return True

        def launch(command, **_kwargs):
            events.append(("launch", command))
            return process

        with mock.patch.object(app.shutil, "which", return_value="/usr/bin/aws"), \
                mock.patch.object(app.subprocess, "run", return_value=version), \
                mock.patch.object(setup_aws, "migrate_sso_profile", side_effect=migrate), \
                mock.patch.object(app.subprocess, "Popen", side_effect=launch):
            app.run_aws_setup("signin", "team", "us-east-2")

        self.assertEqual(events[0], ("migrate", "team"))
        self.assertEqual(events[1], ("launch", ["/usr/bin/aws", "sso", "login",
                                                  "--profile", "team"]))
        self.assertTrue(any("backed up and upgraded" in line for line in app.AWS_SETUP["lines"]))
        self.assertTrue(any("administrator's identity session expires" in line
                            for line in app.AWS_SETUP["lines"]))
        self.assertEqual(app.AWS_SETUP["status"], "done")

    def test_old_aws_cli_is_rejected_before_migration_or_login(self):
        version = subprocess.CompletedProcess(
            ["aws", "--version"], 0, stdout="aws-cli/2.8.9 Python/3.11.0", stderr="")
        with mock.patch.object(app.shutil, "which", return_value="/usr/bin/aws"), \
                mock.patch.object(app.subprocess, "run", return_value=version), \
                mock.patch.object(setup_aws, "migrate_sso_profile") as migrate, \
                mock.patch.object(app.subprocess, "Popen") as launch:
            app.run_aws_setup("signin", "team", "us-east-2")

        migrate.assert_not_called()
        launch.assert_not_called()
        self.assertEqual(app.AWS_SETUP["status"], "error")
        self.assertTrue(any("v2.9" in line for line in app.AWS_SETUP["lines"]))


if __name__ == "__main__":
    unittest.main()
