from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sidjily.database import Database
from sidjily.export import build_export_table
from sidjily.sidjilcom.browser import PlaywrightBrowser
from sidjily.sidjilcom.config import SessionConfig
from sidjily.sidjilcom.search_playwright import PlaywrightSearchDriver, sanitize_detail_url
from sidjily.sidjilcom.selectors import DEFAULT_ENTERPRISE_SEARCH_ROUTE
from sidjily.task_manager import TaskManager

PORTAL = "https://sidjilcom.cnrc.dz"
SEARCH_URL = f"{PORTAL}{DEFAULT_ENTERPRISE_SEARCH_ROUTE}"


class DetailUrlSanitizationTests(unittest.TestCase):
    def test_tokens_and_session_identifiers_are_removed(self) -> None:
        url = (
            f"{PORTAL}/fr/group/sidjilcom/fiche;jsessionid=ABC123?p_p_id=detail&p_auth=SECRET"
            "&_detail_id=42&csrfToken=x#top"
        )
        self.assertEqual(
            sanitize_detail_url(url, PORTAL),
            f"{PORTAL}/fr/group/sidjilcom/fiche?p_p_id=detail&_detail_id=42",
        )

    def test_foreign_or_insecure_links_are_rejected(self) -> None:
        self.assertEqual(sanitize_detail_url("https://evil.example/fiche?id=1", PORTAL), "")
        self.assertEqual(sanitize_detail_url("http://sidjilcom.cnrc.dz/fiche?id=1", PORTAL), "")
        self.assertEqual(sanitize_detail_url("", PORTAL), "")


class _ResultsWithLinksPage:
    url = SEARCH_URL

    def is_closed(self) -> bool:
        return False

    def title(self) -> str:
        return "Recherche"

    def evaluate(self, script: str) -> object:
        if "headers, rows, links" in script:
            return {
                "headers": ["Dénomination"],
                "rows": [["SARL ABC"], "invalide", ["EURL DEF"]],
                "links": [f"{PORTAL}/fiche?id=1&p_auth=T", "", "https://evil.example/x"],
            }
        return {
            "title": "Résultats", "tableCount": 1, "rowCount": 2, "headers": ["Dénomination"],
            "paginationVisible": False, "noResults": False, "resultCount": 2, "errors": [],
            "sessionExpired": False,
        }


class _DetailPage:
    def __init__(self, details: dict[str, dict[str, object]]) -> None:
        self.url = SEARCH_URL
        self.details = details
        self.visited: list[str] = []

    def is_closed(self) -> bool:
        return False

    def goto(self, url: str, **_kwargs: object) -> None:
        self.visited.append(url)
        self.url = url

    def wait_for_timeout(self, _ms: int) -> None:
        return None

    def evaluate(self, _script: str) -> object:
        return self.details[self.url]


class DetailReadingTests(unittest.TestCase):
    def test_business_identifiers_in_cells_are_kept_intact(self) -> None:
        class Page(_ResultsWithLinksPage):
            def evaluate(self, script: str) -> object:
                if "headers, rows, links" in script:
                    return {"headers": ["Nom"], "rows": [[
                        "AUTHENTIQUE SARL", "16/00-0123456 B 19", "000216001234567", "contact@abc.dz",
                    ]], "links": []}
                return super().evaluate(script)

        observation = PlaywrightSearchDriver(Page(), PORTAL, lambda _mode: None).observe_results()
        self.assertEqual(
            observation.rows[0],
            ("AUTHENTIQUE SARL", "16/00-0123456 B 19", "000216001234567", "contact@abc.dz"),
        )

    def test_row_links_stay_aligned_with_rows_and_are_sanitized(self) -> None:
        driver = PlaywrightSearchDriver(_ResultsWithLinksPage(), PORTAL, lambda _mode: None)
        observation = driver.observe_results()
        self.assertEqual(observation.rows, (("SARL ABC",), ("EURL DEF",)))
        self.assertEqual(observation.row_links, (f"{PORTAL}/fiche?id=1", ""))

    def test_detail_fields_are_read_and_empty_pairs_dropped(self) -> None:
        url = f"{PORTAL}/fiche?id=1"
        page = _DetailPage({url: {
            "title": "SARL ABC",
            "fields": [["Adresse", "Rue 1, Alger"], ["Capital", ""], ["NIF", "000123"], "bad"],
            "sessionExpired": False,
        }})
        detail = PlaywrightSearchDriver(page, PORTAL, lambda _mode: None).read_detail(url + "&p_auth=X")
        self.assertEqual(page.visited, [url])
        self.assertEqual(detail["title"], "SARL ABC")
        self.assertEqual(detail["fields"], [["Adresse", "Rue 1, Alger"], ["NIF", "000123"]])

    def test_batch_reading_stops_at_session_expiry_and_skips_foreign_links(self) -> None:
        first, second, third = (f"{PORTAL}/fiche?id={index}" for index in (1, 2, 3))
        page = _DetailPage({
            first: {"title": "A", "fields": [["NIF", "1"]], "sessionExpired": False},
            second: {"title": "", "fields": [], "sessionExpired": True},
            third: {"title": "C", "fields": [["NIF", "3"]], "sessionExpired": False},
        })
        browser = PlaywrightBrowser()
        browser._page = page
        details = browser.read_enterprise_details(
            SessionConfig(), [("k0", "https://evil.example/x"), ("k1", first), ("k2", second), ("k3", third)]
        )
        self.assertEqual([detail["key"] for detail in details], ["k0", "k1", "k2"])
        self.assertIn("error", details[0])
        self.assertEqual(details[1]["fields"], [["NIF", "1"]])
        self.assertIn("Session expirée", details[2]["error"])
        self.assertNotIn(third, page.visited)


class DetailStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manager = TaskManager(Database(Path(self.temp_dir.name) / "d.sqlite3"))
        self.search = self.manager.create_controlled_search("Fiches", {"mode": "PERSONNE_MORALE"})

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_results_keep_page_and_row_order_beyond_ten_rows(self) -> None:
        rows = tuple((f"E{index}",) for index in range(12))
        self.manager.store_search_results(self.search.id, ("Nom",), rows, page=1)
        self.manager.store_search_results(self.search.id, ("Nom",), (("F0",),), page=2)
        values = [result["values"][0] for result in self.manager.get_search_results(self.search.id)]
        self.assertEqual(values, [f"E{index}" for index in range(12)] + ["F0"])

    def test_details_are_attached_to_their_row_and_exported(self) -> None:
        self.manager.store_search_results(
            self.search.id, ("Nom", "Wilaya"), (("SARL ABC", "Alger"), ("EURL DEF", "Oran")),
            links=(f"{PORTAL}/fiche?id=1", ""),
        )
        stored = self.manager.store_result_details(self.search.id, [
            {"key": "p1_r0", "title": "SARL ABC", "fields": [["NIF", "1"], ["Adresse", "Rue 1"], ["NIF", "2"]]},
            {"key": "p1_r1", "error": "Fiche illisible"},
            {"key": "inconnue", "fields": [["NIF", "9"]]},
        ])
        self.assertEqual(stored, 1)
        results = self.manager.get_search_results(self.search.id)
        self.assertEqual(results[0]["detail"]["title"], "SARL ABC")
        self.assertNotIn("detail", results[1])

        header, rows = build_export_table(results)
        self.assertEqual(header, ["Nom", "Wilaya", "Fiche — NIF", "Fiche — Adresse", "Lien fiche"])
        self.assertEqual(rows[0], ["SARL ABC", "Alger", "1 | 2", "Rue 1", f"{PORTAL}/fiche?id=1"])
        self.assertEqual(rows[1], ["EURL DEF", "Oran", "", "", ""])

    def test_export_without_details_keeps_only_result_columns(self) -> None:
        self.manager.store_search_results(self.search.id, ("Nom",), (("A",), ("B",)))
        header, rows = build_export_table(self.manager.get_search_results(self.search.id))
        self.assertEqual(header, ["Nom"])
        self.assertEqual(rows, [["A"], ["B"]])


if __name__ == "__main__":
    unittest.main()
