from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from sidjily.database import Database
from sidjily.models import Status
from sidjily.task_manager import InvalidTransition, TaskManager


class TaskManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "sidjily.sqlite3"
        self.manager = TaskManager(Database(self.database_path))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_creation_is_persisted_with_root_task_and_event(self) -> None:
        search = self.manager.create_search(
            "Test local", {"type_personne": "Personne morale", "code_activite": "442102"}
        )
        restored = TaskManager(Database(self.database_path)).get_search(search.id)
        tasks = self.manager.list_tasks(search.id)
        events = self.manager.list_events(search.id)

        self.assertEqual(restored.criteria["code_activite"], "442102")
        self.assertEqual(restored.status, Status.PENDING)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].status, Status.PENDING)
        self.assertIn("créée", events[0]["message"])

    def test_split_pause_and_resume_keep_completed_work(self) -> None:
        search = self.manager.create_search("Recherche", {"code_activite": "442102"})
        root = self.manager.start_next_task(search.id)
        self.assertIsNotNone(root)
        child_ids = self.manager.add_subtasks(
            root.id, [("wilaya", {"wilaya": "01"}), ("wilaya", {"wilaya": "02"})]
        )
        self.assertEqual(self.manager.get_search(search.id).status, Status.RUNNING)
        first_child = self.manager.start_next_task(search.id)
        self.assertEqual(first_child.id, child_ids[0])
        self.manager.complete_task(first_child.id, result_count=12)
        self.manager.pause_search(search.id)

        tasks = self.manager.list_tasks(search.id)
        self.assertEqual(tasks[0].status, Status.COMPLETED)
        self.assertEqual(tasks[1].status, Status.COMPLETED)
        self.assertEqual(tasks[2].status, Status.SUSPENDED)

        self.manager.resume_search(search.id)
        resumed = self.manager.start_next_task(search.id)
        self.assertEqual(resumed.id, child_ids[1])
        self.manager.complete_task(resumed.id, result_count=5)
        self.assertEqual(self.manager.get_search(search.id).status, Status.COMPLETED)
        self.assertEqual(self.manager.list_tasks(search.id)[1].attempts, 1)

    def test_crash_recovery_requeues_only_running_task(self) -> None:
        search = self.manager.create_search("Reprise", {})
        root = self.manager.start_next_task(search.id)
        child_ids = self.manager.add_subtasks(root.id, [("commune", {"commune": "Alger Centre"})])
        child = self.manager.start_next_task(search.id)
        self.assertEqual(child.id, child_ids[0])

        restarted = TaskManager(Database(self.database_path))
        self.assertEqual(restarted.recover_interrupted(), 1)
        self.assertEqual(restarted.get_search(search.id).status, Status.SUSPENDED)
        self.assertEqual(restarted.list_tasks(search.id)[0].status, Status.COMPLETED)
        self.assertEqual(restarted.list_tasks(search.id)[1].status, Status.PENDING)
        restarted.resume_search(search.id)
        again = restarted.start_next_task(search.id)
        self.assertEqual(again.id, child.id)
        self.assertEqual(again.attempts, 2)

    def test_failed_task_can_be_retried_and_logs_error(self) -> None:
        search = self.manager.create_search("Erreur", {})
        task = self.manager.start_next_task(search.id)
        self.manager.fail_task(task.id, "Erreur réseau simulée")
        self.assertEqual(self.manager.get_search(search.id).status, Status.FAILED)
        self.manager.resume_search(search.id)
        retry = self.manager.start_next_task(search.id)
        self.assertEqual(retry.id, task.id)
        self.assertEqual(retry.attempts, 2)
        self.assertTrue(any("Erreur réseau simulée" in event["message"] for event in self.manager.list_events(search.id)))

    def test_invalid_transition_is_rejected(self) -> None:
        search = self.manager.create_search("État", {})
        task = self.manager.list_tasks(search.id)[0]
        with self.assertRaises(InvalidTransition):
            self.manager.complete_task(task.id)

    def test_shared_in_memory_database_survives_short_connections(self) -> None:
        manager = TaskManager(Database(":memory:"))
        search = manager.create_search("Mémoire", {"wilaya": "16"})
        self.assertEqual(manager.get_search(search.id).criteria["wilaya"], "16")

    def test_preparatory_draft_restores_criteria_summary_created_date_and_pending_state(self) -> None:
        criteria = {
            "mode": "PERSONNE_MORALE",
            "raison_sociale": "Atlas Conseil",
            "commune_wilaya": "Sétif",
        }
        summary = {
            "kind": "sidjily_preparation_draft",
            "mode": "PERSONNE_MORALE",
            "filled_criteria": 2,
            "summary": "Aucune recherche n'a encore été envoyée à Sidjilcom.",
            "submitted": False,
        }
        created = self.manager.create_draft("Brouillon Atlas", criteria, summary)
        restarted = TaskManager(Database(self.database_path))
        restored = restarted.get_search(created.id)

        self.assertTrue(restarted.is_draft(created.id))
        self.assertEqual(restored.criteria, criteria)
        self.assertEqual(restored.result_summary, summary)
        self.assertEqual(restored.created_at, created.created_at)
        self.assertEqual(restored.status, Status.PENDING)
        self.assertEqual(restored.step, "draft")
        self.assertEqual(restarted.list_drafts()[0].id, created.id)
        self.assertIsNone(restarted.start_next_task(created.id))
        with self.assertRaises(InvalidTransition):
            restarted.resume_search(created.id)

        updated = restarted.update_draft(
            created.id,
            "Brouillon Atlas modifié",
            {**criteria, "activite": "Conseil"},
            {**summary, "filled_criteria": 3},
        )
        self.assertEqual(updated.created_at, created.created_at)
        self.assertEqual(updated.criteria["activite"], "Conseil")
        self.assertEqual(updated.result_summary["filled_criteria"], 3)
        self.assertEqual(updated.status, Status.PENDING)

    def test_controlled_search_persists_step_state_error_free_summary_and_never_retries(self) -> None:
        search = self.manager.create_controlled_search(
            "Test structurel",
            {"mode": "PERSONNE_MORALE", "commune_wilaya": "34000 : BORDJ BOU ARRERIDJ"},
        )
        self.assertEqual(search.step, "prepared")
        self.assertIsNone(search.result_summary)
        self.assertIsNone(self.manager.start_next_task(search.id))
        task = self.manager.start_controlled_search(search.id)
        self.assertEqual(task.attempts, 1)
        self.manager.update_controlled_search_step(search.id, "filling_criteria")
        self.assertEqual(self.manager.get_search(search.id).step, "filling_criteria")
        self.manager.complete_controlled_search(search.id, {
            "title": "Résultats",
            "url": "https://sidjilcom.cnrc.dz/fr/group/sidjilcom/repertoire-des-commercants",
            "result_count": 4,
            "table_count": 1,
            "row_count": 4,
            "columns": ["Dénomination"],
            "details_opened": False,
            "company_rows_collected": False,
            "automatic_pagination": False,
        })
        restored = self.manager.get_search(search.id)
        self.assertEqual(restored.status, Status.COMPLETED)
        self.assertEqual(restored.step, "completed")
        self.assertEqual(restored.result_summary["row_count"], 4)
        self.assertEqual(self.manager.list_tasks(search.id)[0].result_count, 4)
        with self.assertRaises(InvalidTransition):
            self.manager.start_controlled_search(search.id)

    def test_failed_controlled_search_keeps_error_and_cannot_resume(self) -> None:
        search = self.manager.create_controlled_search("Test", {"mode": "PERSONNE_MORALE"})
        self.manager.start_controlled_search(search.id)
        self.manager.update_controlled_search_step(search.id, "form_validation")
        self.manager.fail_controlled_search(search.id, "Suggestion exacte absente.")
        restored = self.manager.get_search(search.id)
        task = self.manager.list_tasks(search.id)[0]
        self.assertEqual(restored.status, Status.FAILED)
        self.assertEqual(restored.step, "failed")
        self.assertEqual(restored.last_error, "Suggestion exacte absente.")
        self.assertEqual(task.error, "Suggestion exacte absente.")
        with self.assertRaises(InvalidTransition):
            self.manager.resume_search(search.id)
        with self.assertRaises(InvalidTransition):
            self.manager.start_controlled_search(search.id)

    def test_schema_v1_migration_adds_controlled_search_metadata_without_dropping_rows(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy.sqlite3"
        connection = sqlite3.connect(legacy_path)
        connection.execute(
            "CREATE TABLE searches (id TEXT PRIMARY KEY, name TEXT NOT NULL, criteria_json TEXT NOT NULL, "
            "status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_error TEXT)"
        )
        connection.execute(
            "INSERT INTO searches VALUES ('legacy-id', 'Legacy', '{}', 'pending', '2026-01-01', '2026-01-01', NULL)"
        )
        connection.execute("PRAGMA user_version = 1")
        connection.commit()
        connection.close()

        database = Database(legacy_path)
        database.initialize()
        with database.connection() as migrated:
            columns = {row["name"] for row in migrated.execute("PRAGMA table_info(searches)")}
            version = migrated.execute("PRAGMA user_version").fetchone()[0]
            preserved = migrated.execute("SELECT id, step, result_summary_json FROM searches").fetchone()
        self.assertEqual(version, 2)
        self.assertTrue({"step", "result_summary_json"}.issubset(columns))
        self.assertEqual(tuple(preserved), ("legacy-id", "created", None))


if __name__ == "__main__":
    unittest.main()
