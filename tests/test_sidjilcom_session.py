from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from sidjily.paths import browser_profile_dir
from sidjily.sidjilcom.config import DEFAULT_SIDJILCOM_URL, SessionConfig
from sidjily.sidjilcom.selectors import PageEvidence, collect_page_evidence, is_portal_host
from sidjily.sidjilcom.session import (
    SessionState,
    SidjilcomSessionManager,
    classify_session,
)


class FakeBrowser:
    def __init__(self, evidence: PageEvidence | None = None, open_error: Exception | None = None):
        self.evidence = evidence or PageEvidence(
            on_portal=True,
            has_login_form=True,
            has_authenticated_marker=False,
            has_expired_notice=False,
        )
        self.open_error = open_error
        self.opened = threading.Event()
        self.closed = threading.Event()
        self.opened_with: SessionConfig | None = None

    def open(self, config: SessionConfig) -> None:
        self.opened_with = config
        if self.open_error is not None:
            raise self.open_error
        self.opened.set()

    def inspect(self, _config: SessionConfig) -> PageEvidence:
        return self.evidence

    def close(self) -> None:
        self.closed.set()


class SessionConfigTests(unittest.TestCase):
    def test_defaults_use_official_url_visible_browser_and_dedicated_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"XDG_DATA_HOME": temp_dir}, clear=False):
                config = SessionConfig()
                self.assertEqual(config.url, DEFAULT_SIDJILCOM_URL)
                self.assertFalse(config.headless)
                self.assertEqual(config.resolved_profile_path, Path(temp_dir) / "SIDJILY" / "browser_profile")
                self.assertNotEqual(config.resolved_profile_path, Path.home() / ".config" / "google-chrome")

    def test_environment_configuration_is_supported_without_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {
                "SIDJILY_SIDJILCOM_URL": DEFAULT_SIDJILCOM_URL,
                "SIDJILY_BROWSER_PROFILE": temp_dir,
                "SIDJILY_BROWSER_HEADLESS": "false",
            }
            with patch.dict(os.environ, env, clear=True):
                config = SessionConfig.from_environment()
        self.assertEqual(config.profile_path, Path(temp_dir))
        self.assertFalse(config.headless)

    def test_non_official_or_credential_bearing_urls_are_rejected(self) -> None:
        for url in (
            "http://sidjilcom.cnrc.dz/",
            "https://user:password@sidjilcom.cnrc.dz/",
            "https://sidjilcom.cnrc.dz/?token=secret",
            "https://example.org/",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                SessionConfig(url=url)

    def test_profile_path_helper_uses_sidjily_user_data_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"XDG_DATA_HOME": temp_dir}, clear=False):
                self.assertEqual(browser_profile_dir(), Path(temp_dir) / "SIDJILY" / "browser_profile")

    def test_profile_inside_git_worktree_is_rejected(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with self.assertRaisesRegex(ValueError, "hors du dépôt Git"):
            SessionConfig(profile_path=repository_root / "browser_profile")

    def test_personal_chromium_profile_is_rejected(self) -> None:
        personal_profile = Path.home() / ".config" / "google-chrome" / "Default"
        with self.assertRaisesRegex(ValueError, "profil personnel Chrome"):
            SessionConfig(profile_path=personal_profile)


class SessionDetectionTests(unittest.TestCase):
    def test_only_the_official_host_counts_as_the_portal(self) -> None:
        self.assertTrue(is_portal_host(DEFAULT_SIDJILCOM_URL, DEFAULT_SIDJILCOM_URL))
        self.assertFalse(
            is_portal_host("https://fake.sidjilcom.cnrc.dz/", DEFAULT_SIDJILCOM_URL)
        )
        self.assertFalse(is_portal_host("https://example.org/", DEFAULT_SIDJILCOM_URL))

    def test_absent_authenticated_evidence_stays_waiting_for_login(self) -> None:
        evidence = PageEvidence(True, True, False, False)
        self.assertEqual(classify_session(evidence, previously_connected=False), SessionState.WAITING_FOR_LOGIN)

    def test_expiration_and_positive_authenticated_marker_are_distinguished(self) -> None:
        active = PageEvidence(True, False, True, False)
        login = PageEvidence(True, True, False, False)
        expired_notice = PageEvidence(True, False, False, True)
        self.assertEqual(classify_session(active, previously_connected=False), SessionState.CONNECTED)
        self.assertEqual(classify_session(login, previously_connected=True), SessionState.SESSION_EXPIRED)
        self.assertEqual(classify_session(expired_notice, previously_connected=False), SessionState.SESSION_EXPIRED)

    def test_login_route_after_a_prior_session_is_expired(self) -> None:
        evidence = collect_page_evidence(
            current_url="https://sidjilcom.cnrc.dz/c/portal/login",
            portal_url=DEFAULT_SIDJILCOM_URL,
            visible_text="Veuillez continuer.",
            has_password_field=False,
            has_signout_control=False,
        )
        self.assertTrue(evidence.has_login_form)
        self.assertEqual(classify_session(evidence, previously_connected=True), SessionState.SESSION_EXPIRED)

    def test_selector_layer_detects_french_and_arabic_expiration_without_page_text_leaks(self) -> None:
        for notice in (
            "Votre session Sidjilcom a expiré. Veuillez vous reconnecter.",
            "انتهت الجلسة، يرجى تسجيل الدخول مرة أخرى",
        ):
            with self.subTest(notice=notice):
                evidence = collect_page_evidence(
                    current_url=DEFAULT_SIDJILCOM_URL,
                    portal_url=DEFAULT_SIDJILCOM_URL,
                    visible_text=notice,
                    has_password_field=True,
                    has_signout_control=False,
                )
                self.assertTrue(evidence.on_portal)
                self.assertTrue(evidence.has_expired_notice)
                self.assertNotIn(notice, repr(evidence))


class SessionManagerTests(unittest.TestCase):
    def make_manager(self, browser: FakeBrowser, temp_dir: str, *, logger: logging.Logger | None = None):
        config = SessionConfig(profile_path=Path(temp_dir) / "browser_profile", poll_interval_seconds=0.02)
        manager = SidjilcomSessionManager(config, browser_factory=lambda: browser, logger=logger)
        self.addCleanup(manager.close)
        return manager

    @staticmethod
    def wait_for_state(manager: SidjilcomSessionManager, state: SessionState, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if manager.snapshot.state == state:
                return
            time.sleep(0.01)
        raise AssertionError(f"État attendu {state.value}, état obtenu {manager.snapshot.state.value}")

    def test_manager_creation_and_verify_without_browser_stay_disconnected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            browser = FakeBrowser()
            manager = self.make_manager(browser, temp_dir)
            manager.verify_session()
            self.assertEqual(manager.snapshot.state, SessionState.DISCONNECTED)
            self.assertFalse(browser.opened.is_set())

    def test_missing_session_waits_for_manual_login_in_isolated_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            browser = FakeBrowser()
            manager = self.make_manager(browser, temp_dir)
            manager.connect()
            self.assertTrue(browser.opened.wait(1))
            self.wait_for_state(manager, SessionState.WAITING_FOR_LOGIN)
            self.assertIn("manuellement", manager.snapshot.message)
            self.assertEqual(browser.opened_with.resolved_profile_path, Path(temp_dir) / "browser_profile")

    def test_connection_and_expiration_are_detected_automatically_then_close_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            browser = FakeBrowser()
            manager = self.make_manager(browser, temp_dir)
            manager.connect()
            self.wait_for_state(manager, SessionState.WAITING_FOR_LOGIN)

            browser.evidence = PageEvidence(True, False, True, False)
            self.wait_for_state(manager, SessionState.CONNECTED)
            self.assertEqual(manager.snapshot.message, "Session Sidjilcom active.")

            browser.evidence = PageEvidence(True, True, False, False)
            self.wait_for_state(manager, SessionState.SESSION_EXPIRED)
            self.assertIn("Veuillez vous reconnecter", manager.snapshot.message)

            self.assertTrue(manager.close(timeout=1))
            self.assertTrue(browser.closed.is_set())
            self.assertEqual(manager.snapshot.state, SessionState.DISCONNECTED)

    def test_expiration_after_restart_uses_only_a_non_secret_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connected_browser = FakeBrowser(PageEvidence(True, False, True, False))
            first_manager = self.make_manager(connected_browser, temp_dir)
            first_manager.connect()
            self.wait_for_state(first_manager, SessionState.CONNECTED)
            marker = first_manager.config.connected_marker_path
            self.assertTrue(marker.is_file())
            self.assertEqual(marker.read_bytes(), b"")
            self.assertTrue(first_manager.close(timeout=1))

            reauth_browser = FakeBrowser(PageEvidence(True, True, False, False))
            restarted_manager = self.make_manager(reauth_browser, temp_dir)
            restarted_manager.connect()
            self.wait_for_state(restarted_manager, SessionState.SESSION_EXPIRED)

    def test_browser_errors_do_not_leak_exception_text_to_logs_or_ui(self) -> None:
        secret = "TEST_ONLY_SENTINEL_7d42f0"
        browser = FakeBrowser(open_error=RuntimeError(secret))
        logger = logging.getLogger("sidjily.session.test-secret-filter")
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = self.make_manager(browser, temp_dir, logger=logger)
            with self.assertLogs(logger, level="INFO") as captured:
                manager.connect()
                self.wait_for_state(manager, SessionState.ERROR)
                error_message = manager.snapshot.message
                self.assertTrue(manager.close(timeout=1))
            rendered_logs = " ".join(captured.output)
            self.assertNotIn(secret, rendered_logs)
            self.assertNotIn(secret, error_message)
            self.assertIn("Impossible", error_message)


if __name__ == "__main__":
    unittest.main()
