from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
