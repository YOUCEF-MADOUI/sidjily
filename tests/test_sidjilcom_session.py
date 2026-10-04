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
from sidjily.sidjilcom.browser import NavigationItem, PageDiagnostics
from sidjily.sidjilcom.selectors import (
    DEFAULT_ENTERPRISE_SEARCH_ROUTE,
    NAVIGATION_LABEL_PATTERNS,
    PageEvidence,
    collect_page_evidence,
    identify_section,
    is_portal_host,
    sanitize_current_url,
)
from sidjily.sidjilcom.session import (
    NavigationElementNotFound,
    NavigationPageIncomplete,
    SessionExpiredError,
    SessionNotConnected,
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
        self.probe_calls = 0
        self.probe_evidence: PageEvidence | None = None
        self.home_evidence: PageEvidence | None = None
        self.home_calls = 0
        self.search_calls = 0
        self.dashboard_calls = 0
        self.search_mode_calls = 0
        self.navigation_evidence = PageEvidence(True, False, False, False, True)
        self.dashboard_evidence = PageEvidence(True, False, False, False, False, True)
        self.page_diagnostics = PageDiagnostics(
            url=f"https://sidjilcom.cnrc.dz{DEFAULT_ENTERPRISE_SEARCH_ROUTE}",
            title="Trouver une entreprise - Sidjilcom",
            section="Trouver une entreprise",
            navigation_items=(
                NavigationItem("Trouver une entreprise", f"https://sidjilcom.cnrc.dz{DEFAULT_ENTERPRISE_SEARCH_ROUTE}"),
            ),
            visible_fields=("Raison Sociale / Nom commercial [text]",),
        )

    def open(self, config: SessionConfig) -> None:
        self.opened_with = config
        if self.open_error is not None:
            raise self.open_error
        self.opened.set()

    def probe_authenticated_route(self, _config: SessionConfig) -> None:
        self.probe_calls += 1
        if self.probe_evidence is not None:
            self.evidence = self.probe_evidence

    def inspect(self, _config: SessionConfig) -> PageEvidence:
        return self.evidence

    def go_home(self, _config: SessionConfig) -> None:
        self.home_calls += 1
        if self.home_evidence is not None:
            self.evidence = self.home_evidence

    def navigate_to_enterprise_search(self, _config: SessionConfig) -> None:
        self.search_calls += 1
        self.evidence = self.navigation_evidence

    def navigate_to_dashboard(self, _config: SessionConfig) -> None:
        self.dashboard_calls += 1
        self.evidence = self.dashboard_evidence
        self.page_diagnostics = PageDiagnostics(
            "https://sidjilcom.cnrc.dz/group/sidjilcom/mon-tableau-de-bord",
            "Nos abonnés - Sidjilcom",
            "Tableau de bord",
            (NavigationItem("Tableau de bord", "https://sidjilcom.cnrc.dz/group/sidjilcom/mon-tableau-de-bord"),),
            (),
        )

    def diagnostics(self, _config: SessionConfig) -> PageDiagnostics:
        return self.page_diagnostics

    def diagnose_search_modes(self, _config: SessionConfig) -> PageDiagnostics:
        self.search_mode_calls += 1
        return self.page_diagnostics

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
        self.assertFalse(is_portal_host("http://sidjilcom.cnrc.dz/", DEFAULT_SIDJILCOM_URL))
        self.assertEqual(identify_section("https://login.example.org/login"), "Page externe")

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

    def test_semantic_navigation_patterns_match_observed_labels(self) -> None:
        self.assertRegex("Trouver Une Entreprise", NAVIGATION_LABEL_PATTERNS["Trouver une entreprise"])
        self.assertRegex("Recherche Commerçant Ambulant", NAVIGATION_LABEL_PATTERNS["Recherche Commerçant Ambulant"])
        self.assertRegex("Nomenclature De Vos Activités", NAVIGATION_LABEL_PATTERNS["Nomenclature de vos activités"])

    def test_observed_routes_and_safe_url_diagnostics(self) -> None:
        self.assertEqual(identify_section("https://sidjilcom.cnrc.dz/"), "Accueil")
        self.assertEqual(
            identify_section(f"https://sidjilcom.cnrc.dz{DEFAULT_ENTERPRISE_SEARCH_ROUTE}"),
            "Trouver une entreprise",
        )
        self.assertEqual(
            identify_section("https://sidjilcom.cnrc.dz/fr/web/sidjilcom/nomenclature-de-vos-activites"),
            "Nomenclature de vos activités",
        )
        self.assertEqual(
            sanitize_current_url("https://sidjilcom.cnrc.dz/page?token=private#secret", DEFAULT_SIDJILCOM_URL),
            "https://sidjilcom.cnrc.dz/page",
        )
        self.assertNotIn("private", sanitize_current_url("https://login.example.org/a?token=private", DEFAULT_SIDJILCOM_URL))

    def test_dashboard_route_and_content_can_confirm_access(self) -> None:
        evidence = collect_page_evidence(
            current_url="https://sidjilcom.cnrc.dz/group/sidjilcom/mon-tableau-de-bord",
            portal_url=DEFAULT_SIDJILCOM_URL,
            visible_text="Nos abonnés — Tableau de bord",
            has_password_field=False,
            has_signout_control=False,
        )
        self.assertTrue(evidence.on_dashboard_page)
        self.assertEqual(classify_session(evidence, previously_connected=False), SessionState.CONNECTED)

    def test_authenticated_search_content_can_confirm_access_without_menu_marker(self) -> None:
        evidence = collect_page_evidence(
            current_url=f"https://sidjilcom.cnrc.dz{DEFAULT_ENTERPRISE_SEARCH_ROUTE}",
            portal_url=DEFAULT_SIDJILCOM_URL,
            visible_text="Raison Sociale / Nom commercial",
            has_password_field=False,
            has_signout_control=False,
            has_visible_form_controls=True,
        )
        self.assertTrue(evidence.on_enterprise_search_page)
        self.assertEqual(classify_session(evidence, previously_connected=False), SessionState.CONNECTED)

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
            self.assertEqual(browser.probe_calls, 1)
            self.assertEqual(browser.opened_with.resolved_profile_path, Path(temp_dir) / "browser_profile")

    def test_already_authenticated_session_is_detected_during_startup_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            browser = FakeBrowser()
            browser.probe_evidence = PageEvidence(True, False, False, False, True)
            manager = self.make_manager(browser, temp_dir)
            manager.connect()
            self.assertTrue(browser.opened.wait(1))
            self.wait_for_state(manager, SessionState.CONNECTED)
            self.assertEqual(browser.probe_calls, 1)
            self.assertTrue(manager.config.connected_marker_path.is_file())

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

    def test_navigation_opens_enterprise_search_and_reports_fields_without_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            browser = FakeBrowser()
            manager = self.make_manager(browser, temp_dir)
            manager.connect()
            self.assertTrue(browser.opened.wait(1))
            report = manager.open_enterprise_search().result(timeout=1)
            self.assertEqual(browser.search_calls, 1)
            self.assertEqual(report.state, SessionState.CONNECTED)
            self.assertEqual(manager.snapshot.state, SessionState.CONNECTED)
            self.assertEqual(report.page.section, "Trouver une entreprise")
            self.assertIn("Raison Sociale", report.page.visible_fields[0])
            self.assertTrue(manager.config.connected_marker_path.is_file())

    def test_search_mode_analysis_requires_authenticated_search_page_and_never_submits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            browser = FakeBrowser()
            browser.evidence = browser.navigation_evidence
            manager = self.make_manager(browser, temp_dir)
            manager.connect()
            self.assertTrue(browser.opened.wait(1))
            result = manager.diagnose_search_modes().result(timeout=1)
            self.assertEqual(browser.search_mode_calls, 1)
            self.assertEqual(result.state, SessionState.CONNECTED)
            self.assertEqual(result.page.section, "Trouver une entreprise")

        with tempfile.TemporaryDirectory() as temp_dir:
            browser = FakeBrowser(PageEvidence(True, True, False, False))
            manager = self.make_manager(browser, temp_dir)
            manager.connect()
            self.assertTrue(browser.opened.wait(1))
            with self.assertRaises(SessionNotConnected):
                manager.diagnose_search_modes().result(timeout=1)
            self.assertEqual(browser.search_mode_calls, 0)

    def test_unauthenticated_redirect_is_reported_to_the_caller(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            browser = FakeBrowser()
            browser.navigation_evidence = PageEvidence(True, True, False, False)
            browser.page_diagnostics = PageDiagnostics(
                "https://sidjilcom.cnrc.dz/web/sidjilcom/login",
                "login - Sidjilcom",
                "Authentification",
                (),
                ("Adresse e-mail [text]",),
            )
            manager = self.make_manager(browser, temp_dir)
            manager.connect()
            self.assertTrue(browser.opened.wait(1))
            with self.assertRaises(SessionNotConnected):
                manager.open_enterprise_search().result(timeout=1)
            self.assertEqual(manager.snapshot.state, SessionState.WAITING_FOR_LOGIN)

    def test_expired_session_during_navigation_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            browser = FakeBrowser()
            browser.navigation_evidence = PageEvidence(True, True, False, False)
            config = SessionConfig(
                profile_path=Path(temp_dir) / "browser_profile", poll_interval_seconds=0.02
            )
            marker = config.connected_marker_path
            marker.parent.mkdir(parents=True)
            marker.touch()
            manager = SidjilcomSessionManager(config, browser_factory=lambda: browser)
            self.addCleanup(manager.close)
            manager.connect()
            self.assertTrue(browser.opened.wait(1))
            with self.assertRaises(SessionExpiredError):
                manager.open_enterprise_search().result(timeout=1)
            self.assertEqual(manager.snapshot.state, SessionState.SESSION_EXPIRED)

    def test_missing_navigation_element_is_reported_and_not_silenced(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            browser = FakeBrowser(PageEvidence(True, False, True, False))
            browser.page_diagnostics = PageDiagnostics(
                "https://sidjilcom.cnrc.dz/", "Accueil - Sidjilcom", "Accueil", (), ()
            )
            manager = self.make_manager(browser, temp_dir)
            manager.connect()
            self.assertTrue(browser.opened.wait(1))
            with self.assertRaises(NavigationElementNotFound):
                manager.open_enterprise_search().result(timeout=1)
            self.assertEqual(manager.snapshot.state, SessionState.CONNECTED)

    def test_incomplete_search_page_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            browser = FakeBrowser(PageEvidence(True, False, True, False))
            browser.page_diagnostics = PageDiagnostics(
                f"https://sidjilcom.cnrc.dz{DEFAULT_ENTERPRISE_SEARCH_ROUTE}",
                "Trouver une entreprise - Sidjilcom",
                "Trouver une entreprise",
                (),
                (),
            )
            manager = self.make_manager(browser, temp_dir)
            manager.connect()
            self.assertTrue(browser.opened.wait(1))
            with self.assertRaises(NavigationPageIncomplete):
                manager.open_enterprise_search().result(timeout=1)

    def test_dashboard_navigation_uses_mock_browser_and_reports_section(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            browser = FakeBrowser()
            manager = self.make_manager(browser, temp_dir)
            manager.connect()
            self.assertTrue(browser.opened.wait(1))
            report = manager.open_dashboard().result(timeout=1)
            self.assertEqual(browser.dashboard_calls, 1)
            self.assertEqual(report.state, SessionState.CONNECTED)
            self.assertEqual(report.page.section, "Tableau de bord")

    def test_home_navigation_is_available_and_reports_the_section(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            browser = FakeBrowser(PageEvidence(True, False, True, False))
            browser.page_diagnostics = PageDiagnostics(
                "https://sidjilcom.cnrc.dz/", "Accueil - Sidjilcom", "Accueil", (), ()
            )
            manager = self.make_manager(browser, temp_dir)
            manager.connect()
            self.assertTrue(browser.opened.wait(1))
            report = manager.go_home().result(timeout=1)
            self.assertEqual(browser.home_calls, 1)
            self.assertEqual(report.page.section, "Accueil")

    def test_confirmed_session_stays_connected_after_unmarked_portal_navigation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            browser = FakeBrowser(PageEvidence(True, False, True, False))
            browser.home_evidence = PageEvidence(True, False, False, False)
            manager = self.make_manager(browser, temp_dir)
            manager.connect()
            self.wait_for_state(manager, SessionState.CONNECTED)

            report = manager.go_home().result(timeout=1)

            self.assertEqual(report.state, SessionState.CONNECTED)
            self.assertEqual(manager.snapshot.state, SessionState.CONNECTED)

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
