from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sidjily.database import Database
from sidjily.sidjilcom.browser import PlaywrightBrowser
from sidjily.sidjilcom.config import SessionConfig
from sidjily.sidjilcom.selectors import DEFAULT_ENTERPRISE_SEARCH_ROUTE
from sidjily.sidjilcom.criteria import (
    SELECT_OPTION_CATALOGS,
    SIDJILCOM_CONTROL_MAP,
    ComponentType,
    CriteriaValidationError,
    apply_observed_controls,
    criteria_from_mapping,
    reset_observed_controls,
)
from sidjily.sidjilcom.search import SearchExecutor, SearchStep, validate_first_controlled_search
from sidjily.task_manager import TaskManager
from tests.test_sidjilcom_search import FakeSearchDriver

FORMES = ["", "SARL", "EURL", "SPA", "SARL"]


class ObservedControlsTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_observed_controls()

    def tearDown(self) -> None:
        reset_observed_controls()

    def test_observed_select_is_activated_with_clean_unique_options(self) -> None:
        updated = apply_observed_controls({
            "morale.forme_juridique": {"tag": "select", "type": "select-one", "options": FORMES + ["SNC", "--"]},
        })
        self.assertEqual(updated, ("morale.forme_juridique",))
        mapping = SIDJILCOM_CONTROL_MAP["morale.forme_juridique"]
        self.assertIs(mapping.component_type, ComponentType.SELECT)
        # Vide, ponctuation et doublon (ambigu au remplissage) sont écartés.
        self.assertEqual(mapping.options, ("EURL", "SPA", "SNC"))
        self.assertEqual(SELECT_OPTION_CATALOGS["morale.forme_juridique"], ("EURL", "SPA", "SNC"))

    def test_unconfirmed_components_follow_only_the_observed_dom_type(self) -> None:
        updated = apply_observed_controls({
            "numero_inscription.nrc1": {"tag": "input", "type": "text", "options": []},
            "numero_inscription.nrc2": {"tag": "select", "type": "select-one", "options": ["A", "B"]},
            "morale.presume": {"tag": "input", "type": "checkbox", "options": []},
            "date_inscription_du": {"tag": "input", "type": "text", "options": []},
            "morale.raison_sociale": {"tag": "select", "type": "select-one", "options": ["X"]},
        })
        self.assertEqual(set(updated), {"numero_inscription.nrc1", "numero_inscription.nrc2"})
        self.assertIs(SIDJILCOM_CONTROL_MAP["numero_inscription.nrc1"].component_type, ComponentType.TEXT)
        self.assertTrue(SIDJILCOM_CONTROL_MAP["numero_inscription.nrc2"].input_semantics_confirmed)
        self.assertIs(SIDJILCOM_CONTROL_MAP["morale.presume"].component_type, ComponentType.UNCONFIRMED)
        self.assertIs(SIDJILCOM_CONTROL_MAP["date_inscription_du"].component_type, ComponentType.DATE)
        self.assertIs(SIDJILCOM_CONTROL_MAP["morale.raison_sociale"].component_type, ComponentType.TEXT)

    def test_reapplying_replaces_previous_capture(self) -> None:
        apply_observed_controls({"etat_commercant": {"tag": "select", "options": ["Actif", "Radié"]}})
        apply_observed_controls({})
        self.assertEqual(SIDJILCOM_CONTROL_MAP["etat_commercant"].options, ())
        self.assertEqual(SELECT_OPTION_CATALOGS["etat_commercant"], ())

    def test_captured_select_value_is_validated_and_filled(self) -> None:
        with self.assertRaises(CriteriaValidationError):
            criteria_from_mapping({"mode": "PERSONNE_MORALE", "forme_juridique": "SPA"})
        apply_observed_controls({"morale.forme_juridique": {"tag": "select", "options": ["SARL", "SPA"]}})
        with self.assertRaises(CriteriaValidationError):
            criteria_from_mapping({"mode": "PERSONNE_MORALE", "forme_juridique": "Inconnue"})
        criteria = criteria_from_mapping({
            "mode": "PERSONNE_MORALE", "forme_juridique": "SPA", "raison_sociale": "Test",
        })
        validate_first_controlled_search(criteria)
        driver = FakeSearchDriver()
        driver.select_options = {"morale.forme_juridique": ("", "SARL", "SPA")}
        driver.filled_values = {}
        SearchExecutor(driver).execute(criteria, confirmed=True, on_step=driver.steps.append)
        self.assertIn(("morale.forme_juridique", "SPA"), driver.fill_calls)
        self.assertIn(SearchStep.SUBMITTED, driver.steps)
        self.assertEqual(driver.submissions, 1)

    def test_nrc_text_component_becomes_searchable_once_observed(self) -> None:
        criteria = criteria_from_mapping({"mode": "PERSONNE_MORALE", "numero_inscription": {"nrc1": "16"}})
        with self.assertRaises(Exception):
            validate_first_controlled_search(criteria)
        apply_observed_controls({"numero_inscription.nrc1": {"tag": "input", "type": "text"}})
        validate_first_controlled_search(criteria)


class _PagedResultsPage:
    """Fausse page Playwright : chaque clic « suivant » affiche la page suivante."""

    def __init__(self, pages: list[list[list[str]]], *, stuck_after: int | None = None) -> None:
        self.url = f"https://sidjilcom.cnrc.dz{DEFAULT_ENTERPRISE_SEARCH_ROUTE}"
        self.pages = pages
        self.index = 0
        self.stuck_after = stuck_after
        self.clicks = 0

    def is_closed(self) -> bool:
        return False

    def title(self) -> str:
        return "Recherche"

    def wait_for_load_state(self, *_args: object, **_kwargs: object) -> None:
        return None

    def wait_for_timeout(self, _ms: int) -> None:
        return None

    def evaluate(self, script: str) -> object:
        if "clicked" in script:
            if self.index + 1 >= len(self.pages):
                return {"clicked": False}
            self.clicks += 1
            if self.stuck_after is None or self.index < self.stuck_after:
                self.index += 1
            return {"clicked": True}
        if "headers, rows" in script:
            return {"headers": ["Dénomination"], "rows": self.pages[self.index]}
        return {
            "title": "Résultats", "tableCount": 1, "rowCount": len(self.pages[self.index]),
            "headers": ["Dénomination"], "paginationVisible": True, "noResults": False,
            "resultCount": None, "errors": [], "sessionExpired": False,
        }


class ResultPaginationTests(unittest.TestCase):
    def _browser(self, page: _PagedResultsPage) -> PlaywrightBrowser:
        browser = PlaywrightBrowser()
        browser._page = page
        return browser

    def test_following_pages_are_collected_until_no_next_link(self) -> None:
        page = _PagedResultsPage([[["A"]], [["B"]], [["C"]]])
        pages = self._browser(page).collect_result_pages(SessionConfig(), 10)
        self.assertEqual([observation.rows for observation in pages], [(("B",),), (("C",),)])

    def test_collection_stops_when_click_does_not_change_the_page(self) -> None:
        page = _PagedResultsPage([[["A"]], [["B"]], [["C"]]], stuck_after=1)
        pages = self._browser(page).collect_result_pages(SessionConfig(), 10)
        self.assertEqual([observation.rows for observation in pages], [(("B",),)])
        self.assertEqual(page.clicks, 2)

    def test_collection_respects_page_limit(self) -> None:
        page = _PagedResultsPage([[["A"]], [["B"]], [["C"]], [["D"]]])
        self.assertEqual(len(self._browser(page).collect_result_pages(SessionConfig(), 2)), 2)


class SettingsPersistenceTests(unittest.TestCase):
    def test_setting_round_trip_and_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = TaskManager(Database(Path(temp_dir) / "s.sqlite3"))
            self.assertEqual(manager.load_setting("absent", {}), {})
            manager.save_setting("catalog", {"etat_commercant": {"tag": "select", "options": ["Actif"]}})
            manager.save_setting("catalog", {"etat_commercant": {"tag": "select", "options": ["Radié"]}})
            restored = TaskManager(Database(Path(temp_dir) / "s.sqlite3")).load_setting("catalog")
            self.assertEqual(restored["etat_commercant"]["options"], ["Radié"])


if __name__ == "__main__":
    unittest.main()
