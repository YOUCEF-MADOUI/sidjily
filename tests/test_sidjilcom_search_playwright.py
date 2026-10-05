from __future__ import annotations

import unittest

from sidjily.sidjilcom.criteria import SearchMode
from sidjily.sidjilcom.search import SearchNavigationUnexpected
from sidjily.sidjilcom.search_playwright import PlaywrightSearchDriver
from sidjily.sidjilcom.selectors import DEFAULT_ENTERPRISE_SEARCH_ROUTE


class FakePage:
    def __init__(self, url: str, result_snapshot: dict[str, object] | None = None):
        self.url = url
        self.result_snapshot = result_snapshot or {}
        self.evaluated: list[str] = []

    def is_closed(self) -> bool:
        return False

    def evaluate(self, script: str) -> object:
        self.evaluated.append(script)
        if "session" in script and "expiredPattern" not in script:
            return False
        if self.result_snapshot:
            return self.result_snapshot
        return False

    def title(self) -> str:
        return "Recherche"


class PlaywrightSearchDriverSafetyTests(unittest.TestCase):
    def test_wrong_page_is_rejected_before_mode_interaction(self) -> None:
        page = FakePage("https://sidjilcom.cnrc.dz/group/sidjilcom/mon-tableau-de-bord")
        mode_actions: list[str] = []
        driver = PlaywrightSearchDriver(
            page, "https://sidjilcom.cnrc.dz", lambda _mode: mode_actions.append("select")
        )
        with self.assertRaises(SearchNavigationUnexpected):
            driver.select_mode(SearchMode.PERSONNE_MORALE)
        self.assertEqual(mode_actions, [])

    def test_wrong_host_is_rejected_before_page_evaluation(self) -> None:
        page = FakePage("https://example.invalid/fr/group/sidjilcom/repertoire-des-commercants")
        driver = PlaywrightSearchDriver(page, "https://sidjilcom.cnrc.dz", lambda _mode: None)
        with self.assertRaises(SearchNavigationUnexpected):
            driver.observe_results()
        self.assertEqual(page.evaluated, [])

    def test_result_observation_is_sanitized_and_contains_structure_only(self) -> None:
        page = FakePage(
            f"https://sidjilcom.cnrc.dz{DEFAULT_ENTERPRISE_SEARCH_ROUTE}?token=must-not-escape",
            {
                "title": "Résultats",
                "tableCount": 1,
                "rowCount": 3,
                "resultZoneFound": True,
                "resultZoneTag": "main",
                "headers": ["Dénomination", "Wilaya"],
                "paginationVisible": True,
                "noResults": False,
                "resultCount": 3,
                "errors": [],
                "sessionExpired": False,
            },
        )
        driver = PlaywrightSearchDriver(page, "https://sidjilcom.cnrc.dz", lambda _mode: None)
        observation = driver.observe_results()
        report = observation.to_mapping()
        self.assertNotIn("token=must-not-escape", observation.sanitized_url)
        self.assertEqual(observation.table_count, 1)
        self.assertEqual(observation.row_count, 3)
        self.assertEqual(observation.columns, ("Dénomination", "Wilaya"))
        self.assertTrue(observation.result_zone_found)
        self.assertEqual(observation.result_zone_tag, "main")
        self.assertFalse(report["company_rows_collected"])
        self.assertFalse(report["details_opened"])
        script = page.evaluated[-1]
        self.assertIn("tbody tr", script)
        self.assertNotIn("row.innerText", script)
        self.assertNotIn("row.textContent", script)


if __name__ == "__main__":
    unittest.main()
