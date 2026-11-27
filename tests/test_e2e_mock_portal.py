"""Parcours complet avec le vrai Chromium contre la réplique locale du portail.

Ignoré si Chromium/Playwright n'est pas disponible. Variable SIDJILY_TEST_CHROMIUM :
chemin d'un Chromium à utiliser si la version gérée par Playwright n'est pas installée.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mock_portal import NS, MockPortal, MockPortalBrowser, PortalState  # noqa: E402
from sidjily.sidjilcom.config import SessionConfig  # noqa: E402
from sidjily.sidjilcom.criteria import (  # noqa: E402
    apply_observed_controls,
    criteria_from_mapping,
    reset_observed_controls,
)
from sidjily.sidjilcom.search import SearchExecutionError  # noqa: E402
from sidjily.sidjilcom.session import SessionState, SidjilcomSessionManager  # noqa: E402

BASE = {"mode": "PERSONNE_MORALE", "activite": "442102", "commune_wilaya": "bordj bou arreridj"}


@unittest.skipIf(os.environ.get("SIDJILY_SKIP_E2E"), "SIDJILY_SKIP_E2E défini")
class MockPortalEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            probe = MockPortalBrowser(MockPortal())
            probe.open(SessionConfig(headless=True))
            probe.close()
        except Exception as exc:  # pragma: no cover - dépend de l'environnement
            raise unittest.SkipTest(f"Chromium indisponible : {type(exc).__name__}")
        cls.portal = MockPortal(PortalState())
        cls.profile = tempfile.TemporaryDirectory()
        cls.session = SidjilcomSessionManager(
            SessionConfig(headless=True, profile_path=Path(cls.profile.name)),
            browser_factory=lambda: MockPortalBrowser(cls.portal),
        )
        cls.session.connect()
        deadline = time.time() + 30
        while cls.session.snapshot.state != SessionState.CONNECTED and time.time() < deadline:
            time.sleep(0.2)
        cls.session.open_enterprise_search().result(timeout=60)
        cls.activated = apply_observed_controls(cls.session.capture_search_controls().result(timeout=90))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.session.close()
        cls.profile.cleanup()
        reset_observed_controls()

    def search(self, payload: dict[str, object]):
        return self.session.execute_search(criteria_from_mapping(payload), confirmed=True).result(timeout=120)

    def test_capture_activates_lists_and_ignores_placeholder_option(self) -> None:
        self.assertIn("morale.forme_juridique", self.activated)
        self.assertIn("secteur_activite", self.activated)
        from sidjily.sidjilcom.criteria import SIDJILCOM_CONTROL_MAP
        self.assertEqual(SIDJILCOM_CONTROL_MAP["morale.forme_juridique"].options, ("SARL", "EURL", "SPA", "SNC"))

    def test_two_autocompletes_pages_and_details(self) -> None:
        observation = self.search(BASE)
        self.assertEqual(observation.result_count, 23)
        self.assertTrue(observation.pagination_visible)
        self.assertEqual(observation.rows[0][:2], ("34/00-0100001 B 19", "AUTHENTIQUE SOCIETE 01"))
        link = observation.row_links[0]
        self.assertTrue(link.endswith("/-/fiche/1"))
        self.assertNotIn("p_auth", link)
        self.assertNotIn("jsessionid", link)
        pages = self.session.collect_result_pages(49).result(timeout=120)
        self.assertEqual([len(page.rows) for page in pages], [10, 3])
        details = self.session.read_enterprise_details([("k0", link)]).result(timeout=60)
        self.assertIn(["NIF", "000340000000001"], details[0]["fields"])
        # Une nouvelle recherche fonctionne après la lecture des fiches (navigateur hors formulaire).
        again = self.search({**BASE, "raison_sociale": "authentique"})
        self.assertEqual(again.result_count, 1)

    def test_select_criterion_is_submitted_by_label(self) -> None:
        observation = self.search({**BASE, "forme_juridique": "EURL"})
        self.assertEqual(observation.result_count, 12)
        self.assertEqual(self.portal.state.last_criteria[f"{NS}forme_"], "2")

    def test_no_results_page_is_recognised(self) -> None:
        observation = self.search({"mode": "PERSONNE_MORALE", "raison_sociale": "introuvable"})
        self.assertTrue(observation.no_results)
        self.assertEqual(observation.rows, ())

    def test_text_date_field_is_refused_before_any_submission(self) -> None:
        submissions = len(self.portal.state.submissions)
        self.portal.state.date_input_type = "text"
        try:
            with self.assertRaises(SearchExecutionError) as caught:
                self.search({**BASE, "date_inscription_du": "2020-01-01"})
        finally:
            self.portal.state.date_input_type = "date"
        self.assertIn("Date d'inscription", str(caught.exception))
        self.assertEqual(len(self.portal.state.submissions), submissions)


if __name__ == "__main__":
    unittest.main()
