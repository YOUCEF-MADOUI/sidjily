"""Lecture automatique de toutes les fiches après une recherche (application Tkinter, session simulée)."""

from __future__ import annotations

import tempfile
import time
import unittest
from concurrent.futures import Future
from pathlib import Path

from sidjily.database import Database
from sidjily.sidjilcom.session import SessionSnapshot, SessionState
from sidjily.task_manager import TaskManager

try:
    import tkinter as tk
    from sidjily.ui.app import SidjilyApp
except Exception:  # pragma: no cover - Tkinter absent
    tk = None


class FakeSession:
    def __init__(self, expire_after: int | None = None) -> None:
        self.snapshot = SessionSnapshot(SessionState.CONNECTED, "Session Sidjilcom active.")
        self.is_running = True
        self.batches: list[list[str]] = []
        self.expire_after = expire_after

    def read_enterprise_details(self, items):
        self.batches.append([key for key, _url in items])
        future: Future = Future()
        details = []
        for key, url in items:
            if self.expire_after is not None and sum(map(len, self.batches)) - len(items) + len(details) >= self.expire_after:
                details.append({"key": key, "error": "Session expirée; lecture des fiches interrompue."})
                break
            details.append({"key": key, "url": url, "title": f"Société {key}", "fields": [["Information Société — NIF", key]]})
        future.set_result(details)
        return future

    def disconnect(self) -> None:
        self.is_running = False

    def close(self, timeout: float = 0) -> bool:
        return True


@unittest.skipIf(tk is None, "Tkinter indisponible")
class AutoDetailsTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # pragma: no cover - pas d'affichage
            self.skipTest(f"Affichage indisponible : {exc}")
        self.root.withdraw()
        self.temp = tempfile.TemporaryDirectory()
        self.manager = TaskManager(Database(Path(self.temp.name) / "test.sqlite3"))
        search = self.manager.create_search("Test", {"activite": "442102"})
        self.search_id = search.id
        rows = tuple((f"34B{index:03d}", f"SOCIETE {index}") for index in range(7))
        links = tuple(f"sidjilcom-fiche:get_detailpm:34B{index:011d}" for index in range(7))
        self.manager.store_search_results(self.search_id, ("RC", "Raison sociale"), rows, page=1, links=links)

    def tearDown(self) -> None:
        self.root.destroy()
        self.temp.cleanup()

    def run_app(self, session: FakeSession, seconds: float = 6.0) -> SidjilyApp:
        app = SidjilyApp(self.root, self.manager, session)
        app.AUTO_DETAILS_BATCH = 3
        app._start_auto_details(self.search_id)
        deadline = time.time() + seconds
        while time.time() < deadline:
            self.root.update()
            time.sleep(0.05)
            results = self.manager.get_search_results(self.search_id)
            if all(result.get("detail") for result in results):
                break
        return app

    def test_every_fiche_is_read_in_successive_batches(self) -> None:
        session = FakeSession()
        self.run_app(session)
        self.assertEqual([len(batch) for batch in session.batches], [3, 3, 1])
        results = self.manager.get_search_results(self.search_id)
        self.assertTrue(all(result.get("detail", {}).get("fields") for result in results))

    def test_reading_stops_when_the_session_expires(self) -> None:
        session = FakeSession(expire_after=4)
        self.run_app(session, seconds=3.0)
        self.assertEqual(len(session.batches), 2)
        read = [result for result in self.manager.get_search_results(self.search_id) if result.get("detail", {}).get("fields")]
        self.assertEqual(len(read), 4)

    def test_unchecked_box_disables_automatic_reading(self) -> None:
        session = FakeSession()
        app = SidjilyApp(self.root, self.manager, session)
        app.auto_details.set(False)
        app._start_auto_details(self.search_id)
        self.root.update()
        self.assertEqual(session.batches, [])


if __name__ == "__main__":
    unittest.main()
