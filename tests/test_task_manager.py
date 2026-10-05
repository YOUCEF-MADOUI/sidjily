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
            {"mode": "PERSONNE_MORALE", "activite": "442102", "commune_wilaya": "34000"},
        )
        self.assertEqual(search.step, "prepared")
        self.assertIsNone(search.result_summary)
        self.assertIsNone(self.manager.start_next_task(search.id))
        task = self.manager.start_controlled_search(search.id)
        self.assertEqual(task.attempts, 1)
        with self.assertRaises(InvalidTransition):
            self.manager.start_controlled_search(search.id)
        self.manager.update_controlled_search_step(search.id, "filling_criteria")
        self.assertEqual(self.manager.get_search(search.id).step, "filling_criteria")
        self.manager.update_controlled_search_pre_submit_diagnostic(search.id, {
            "kind": "sidjilcom_pre_submit_diagnostic",
            "mode": "PERSONNE_MORALE",
            "criteria": ["activite", "commune_wilaya"],
            "controls": [
                {"criterion": "activite", "tag": "input", "type": "text", "visible": True,
                 "enabled": True, "empty_before_fill": True}
            ],
            "all_visible_controls_empty_before_fill": True,
            "search_button_count": 1,
            "search_button_enabled": True,
            "sensitive_values_saved": False,
            "cookies_saved": False,
            "tokens_saved": False,
            "session_identifiers_saved": False,
            "untrusted_extra": "must be discarded",
        })
        self.assertEqual(self.manager.get_search(search.id).result_summary["pre_submit_diagnostic"]["kind"], "sidjilcom_pre_submit_diagnostic")
        self.assertNotIn("untrusted_extra", self.manager.get_search(search.id).result_summary["pre_submit_diagnostic"])
        self.manager.complete_controlled_search(search.id, {
            "title": "Résultats",
            "url": "https://sidjilcom.cnrc.dz/fr/group/sidjilcom/repertoire-des-commercants",
            "result_count": 4,
            "no_results": False,
            "table_count": 1,
            "row_count": 4,
            "columns": ["Dénomination"],
            "details_opened": False,
            "company_rows_collected": False,
            "automatic_pagination": False,
        })
        restored = self.manager.get_search(search.id)
        self.assertEqual(restored.status, Status.COMPLETED)
        self.assertEqual(restored.step, "results_detected")
        self.assertEqual(restored.result_summary["row_count"], 4)
        self.assertIn("pre_submit_diagnostic", restored.result_summary)
        self.assertEqual(self.manager.list_tasks(search.id)[0].result_count, 4)
        historical = self.manager.create_controlled_search("Historique après soumission", search.criteria)
        self.assertEqual(historical.status, Status.PENDING)
        with self.assertRaises(InvalidTransition):
            self.manager.start_controlled_search(historical.id)
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
        self.assertEqual(restored.step, "failed_before_submission")
        self.assertEqual(restored.last_error, "Suggestion exacte absente.")
        self.assertEqual(task.error, "Suggestion exacte absente.")
        with self.assertRaises(InvalidTransition):
            self.manager.resume_search(search.id)
        with self.assertRaises(InvalidTransition):
            self.manager.start_controlled_search(search.id)
        next_search = self.manager.create_controlled_search(
            "Nouvel essai manuel après échec pré-soumission", {"mode": "PERSONNE_MORALE"}
        )
        self.assertEqual(self.manager.controlled_submission_state(), "available")
        self.manager.start_controlled_search(next_search.id)
        self.assertEqual(self.manager.controlled_submission_state(), "reserved")
        # Les deux tentatives restent visibles dans l'historique.
        self.assertEqual(len(self.manager.list_searches()), 2)

    def test_controlled_submission_failure_is_result_unknown_and_follow_up_is_read_only(self) -> None:
        search = self.manager.create_controlled_search(
            "Test inconnu", {"mode": "PERSONNE_MORALE", "activite": "442102", "commune_wilaya": "34000"}
        )
        self.manager.start_controlled_search(search.id)
        self.manager.update_controlled_search_step(search.id, "submitting")
        self.manager.update_controlled_search_step(search.id, "submitted")
        self.manager.update_controlled_search_step(search.id, "observing_results")
        self.manager.fail_controlled_search(search.id, "Diagnostic des résultats impossible.")
        failed = self.manager.get_search(search.id)
        self.assertEqual(failed.step, "result_unknown")
        self.assertIn("résultat est inconnu", failed.last_error)
        with self.assertRaises(InvalidTransition):
            self.manager.resume_search(search.id)
        with self.assertRaises(InvalidTransition):
            self.manager.start_controlled_search(search.id)
        another = self.manager.create_controlled_search("Historique bloqué", search.criteria)
        with self.assertRaises(InvalidTransition):
            self.manager.start_controlled_search(another.id)
        self.assertEqual(self.manager.controlled_submission_state(), "submitted")

        diagnostic = {
            "title": "Résultats",
            "url": "https://sidjilcom.cnrc.dz/fr/group/sidjilcom/repertoire-des-commercants?token=secret",
            "table_count": 1,
            "company_name": "must not persist",
        }
        self.manager.store_unknown_search_diagnostic(search.id, diagnostic)
        restored = self.manager.get_search(search.id)
        self.assertEqual(restored.step, "result_unknown")
        self.assertEqual(restored.result_summary["follow_up_structural_diagnostic"]["title"], "Résultats")
        self.assertNotIn("company_name", restored.result_summary["follow_up_structural_diagnostic"])
        self.assertNotIn("token=secret", restored.result_summary["follow_up_structural_diagnostic"]["url"])
        self.assertEqual(self.manager.list_tasks(search.id)[0].attempts, 1)
        self.assertIn("aucune nouvelle recherche soumise", self.manager.list_events(search.id)[-1]["message"])

    def test_interrupted_controlled_search_is_never_requeued_and_submit_stage_becomes_unknown(self) -> None:
        search = self.manager.create_controlled_search("Interrompue", {"mode": "PERSONNE_MORALE"})
        self.manager.start_controlled_search(search.id)
        self.manager.update_controlled_search_step(search.id, "submitting")
        restarted = TaskManager(Database(self.database_path))
        self.assertEqual(restarted.recover_interrupted(), 1)
        restored = restarted.get_search(search.id)
        task = restarted.list_tasks(search.id)[0]
        self.assertEqual(restored.status, Status.FAILED)
        self.assertEqual(restored.step, "result_unknown")
        self.assertEqual(task.status, Status.FAILED)
        self.assertEqual(task.attempts, 1)
        with self.assertRaises(InvalidTransition):
            restarted.resume_search(search.id)
        with self.assertRaises(InvalidTransition):
            restarted.start_controlled_search(search.id)
        blocked = restarted.create_controlled_search("Ne pas relancer", search.criteria)
        with self.assertRaises(InvalidTransition):
            restarted.start_controlled_search(blocked.id)
        self.assertEqual(restarted.controlled_submission_state(), "submitted")

    def test_zero_result_observation_persists_no_results_terminal_step(self) -> None:
        search = self.manager.create_controlled_search("Aucun résultat", {"mode": "PERSONNE_MORALE"})
        self.manager.start_controlled_search(search.id)
        self.manager.update_controlled_search_step(search.id, "submitted")
        self.manager.complete_controlled_search(search.id, {
            "title": "Recherche", "url": "https://sidjilcom.cnrc.dz/fr/group/sidjilcom/repertoire-des-commercants",
            "result_count": 0, "table_count": 0, "row_count": 0, "columns": [],
            "pagination_visible": False, "no_results": True, "errors": [], "session_expired": False,
        })
        self.assertEqual(self.manager.get_search(search.id).step, "no_results")
        blocked = self.manager.create_controlled_search("Historique protégé", {"mode": "PERSONNE_MORALE"})
        with self.assertRaises(InvalidTransition):
            self.manager.start_controlled_search(blocked.id)

    def test_confirmed_draft_is_promoted_atomically_into_single_attempt(self) -> None:
        criteria = {"mode": "PERSONNE_MORALE", "activite": "442102", "commune_wilaya": "34000"}
        draft = self.manager.create_draft("Draft", criteria, {"submitted": False})
        promoted = self.manager.promote_draft_to_controlled_search(draft.id, "Test confirmé", criteria)
        self.assertEqual(promoted.id, draft.id)
        self.assertFalse(self.manager.is_draft(draft.id))
        self.assertEqual(promoted.criteria, criteria)
        self.assertIsNone(promoted.result_summary)
        self.manager.start_controlled_search(promoted.id)
        with self.assertRaises(KeyError):
            self.manager.promote_draft_to_controlled_search(draft.id, "Encore", criteria)
        another = self.manager.create_draft("Deuxième brouillon", criteria, {"submitted": False})
        prepared = self.manager.promote_draft_to_controlled_search(another.id, "Tentative en trop", criteria)
        self.assertTrue(self.manager.is_prepared_controlled_search(prepared.id))
        with self.assertRaises(InvalidTransition):
            self.manager.start_controlled_search(prepared.id)

    def test_abandoned_pre_submit_reservation_is_released_on_recovery_and_history_is_kept(self) -> None:
        abandoned = self.manager.create_controlled_search("Abandonnée avant soumission", {"mode": "PERSONNE_MORALE"})
        self.manager.start_controlled_search(abandoned.id)

        restarted = TaskManager(Database(self.database_path))
        self.assertEqual(restarted.recover_interrupted(), 1)
        restored = restarted.get_search(abandoned.id)
        self.assertEqual(restored.step, "failed_before_submission")
        self.assertEqual(restarted.controlled_submission_state(), "available")

        fresh = restarted.create_controlled_search("Premier vrai test", {
            "mode": "PERSONNE_MORALE", "activite": "442102", "commune_wilaya": "34000",
        })
        restarted.start_controlled_search(fresh.id)
        self.assertEqual(restarted.controlled_submission_state(), "reserved")
        searches = {search.id for search in restarted.list_searches()}
        self.assertEqual(searches, {abandoned.id, fresh.id})

    def test_controlled_failure_diagnostic_keeps_structure_but_drops_values_and_url_secrets(self) -> None:
        search = self.manager.create_controlled_search("Diagnostic", {
            "mode": "PERSONNE_MORALE", "activite": "442102", "commune_wilaya": "34000",
        })
        self.manager.start_controlled_search(search.id)
        self.manager.store_controlled_search_failure_diagnostic(search.id, {
            "kind": "sidjilcom_controlled_search_failure",
            "stage": "post_fill_verification",
            "mode": "PERSONNE_MORALE",
            "reason_code": "filled_value_mismatch",
            "current_field": "activite",
            "page": {
                "url": "https://sidjilcom.cnrc.dz/fr/group/sidjilcom/repertoire-des-commercants?token=secret",
                "title": "Recherche Commerçant", "section": "Trouver une entreprise",
            },
            "form": {
                "id": "legal-search", "name": "", "class": "search-form", "role": "search",
                "method": "POST", "action": "https://sidjilcom.cnrc.dz/search?csrf=secret",
                "parent_portlet": "dz_cnrc_sidjilcom_recherchedetaillee_portlet_RechercheDetailleePortlet",
                "parent_portlet_confirmed": True,
            },
            "fields": [{
                "criterion": "activite", "suffix": "_activi", "name": "portlet_activi",
                "id": "activity", "label": "Activité", "role": "combobox", "tag": "input",
                "type": "text", "class": "yui3-aclist-input", "visible": True,
                "disabled": False, "required": False, "has_value": True,
                "value_matches_criterion": False, "value": "SECRET VALUE MUST NOT PERSIST",
            }],
            "button": {"label": "Rechercher", "id": "search", "type": "submit", "visible": True,
                       "enabled": True, "associated": True, "candidate_count": 1},
            "sensitive_values_saved": False, "cookies_saved": False,
            "tokens_saved": False, "session_identifiers_saved": False,
        })
        saved = self.manager.get_search(search.id).result_summary["controlled_search_failure_diagnostic"]
        encoded = self.manager.database.encode_json(saved)
        self.assertEqual(saved["reason_code"], "filled_value_mismatch")
        self.assertEqual(saved["fields"][0]["name"], "portlet_activi")
        self.assertFalse(saved["fields"][0]["value_matches_criterion"])
        self.assertEqual(saved["page"]["url"], "https://sidjilcom.cnrc.dz/fr/group/sidjilcom/repertoire-des-commercants")
        self.assertNotIn("token=secret", encoded)
        self.assertNotIn("csrf=secret", encoded)
        self.assertNotIn("SECRET VALUE", encoded)
        self.assertNotIn('"value":', encoded)
        self.manager.fail_controlled_search(search.id, "Valeur DOM non conforme.")
        self.assertEqual(self.manager.get_search(search.id).step, "failed_before_submission")

    def test_submission_event_keeps_lock_even_if_current_state_was_overwritten(self) -> None:
        search = self.manager.create_controlled_search("Trace historique", {"mode": "PERSONNE_MORALE"})
        task = self.manager.start_controlled_search(search.id)
        self.manager.update_controlled_search_step(search.id, "submitting")
        with self.manager.database.connection(immediate=True) as connection:
            connection.execute(
                "UPDATE searches SET status = ?, step = 'failed_before_submission' WHERE id = ?",
                (Status.FAILED, search.id),
            )
            connection.execute(
                "UPDATE tasks SET status = ? WHERE id = ?", (Status.FAILED, task.id)
            )
        self.assertEqual(self.manager.controlled_submission_state(), "submitted")
        stale = self.manager.create_controlled_search("À ne pas soumettre", {"mode": "PERSONNE_MORALE"})
        with self.assertRaises(InvalidTransition):
            self.manager.start_controlled_search(stale.id)

    def test_submission_event_marks_failure_unknown_even_if_step_was_rewound(self) -> None:
        search = self.manager.create_controlled_search("Étape réécrite", {"mode": "PERSONNE_MORALE"})
        self.manager.start_controlled_search(search.id)
        self.manager.update_controlled_search_step(search.id, "submitting")
        with self.manager.database.connection(immediate=True) as connection:
            connection.execute(
                "UPDATE searches SET step = 'form_validation' WHERE id = ?", (search.id,)
            )
        self.manager.fail_controlled_search(search.id, "Interruption après tentative de soumission.")
        failed = self.manager.get_search(search.id)
        self.assertEqual(failed.step, "result_unknown")
        self.assertEqual(self.manager.controlled_submission_state(), "submitted")

    def test_prepared_controlled_search_can_be_restored_and_updated_without_reservation(self) -> None:
        criteria = {"mode": "PERSONNE_MORALE", "activite": "442102", "commune_wilaya": "34000"}
        prepared = self.manager.create_controlled_search("Préparée", criteria)
        self.assertEqual(self.manager.controlled_submission_state(), "available")
        self.assertTrue(self.manager.is_prepared_controlled_search(prepared.id))
        self.assertEqual(self.manager.list_prepared_controlled_searches()[0].id, prepared.id)
        updated = self.manager.update_prepared_controlled_search(
            prepared.id, "Préparée restaurée", criteria, {"submitted": False}
        )
        self.assertEqual(updated.name, "Préparée restaurée")
        self.assertEqual(updated.result_summary, {"submitted": False})
        self.assertEqual(self.manager.controlled_submission_state(), "available")
        started = self.manager.start_controlled_search(prepared.id)
        self.assertEqual(started.attempts, 1)

    def test_v2_migration_only_seeds_claim_from_possible_submission_evidence(self) -> None:
        failed = self.manager.create_controlled_search("Ancien échec", {"mode": "PERSONNE_MORALE"})
        submitted = self.manager.create_controlled_search("Soumission possible", {"mode": "PERSONNE_MORALE"})
        with self.manager.database.connection(immediate=True) as connection:
            failed_task = connection.execute("SELECT id FROM tasks WHERE search_id = ?", (failed.id,)).fetchone()["id"]
            submitted_task = connection.execute("SELECT id FROM tasks WHERE search_id = ?", (submitted.id,)).fetchone()["id"]
            connection.execute(
                "UPDATE searches SET status = ?, step = 'failed' WHERE id = ?", (Status.FAILED, failed.id)
            )
            connection.execute(
                "UPDATE tasks SET status = ?, attempts = 1 WHERE id = ?", (Status.FAILED, failed_task)
            )
            connection.execute(
                "UPDATE searches SET status = ?, step = 'failed' WHERE id = ?", (Status.FAILED, submitted.id)
            )
            connection.execute(
                "UPDATE tasks SET status = ?, attempts = 1 WHERE id = ?", (Status.FAILED, submitted_task)
            )
            self.manager.database.add_event(
                connection, "Étape de recherche contrôlée : submitting.", search_id=submitted.id, task_id=submitted_task
            )
            connection.execute("DROP TABLE controlled_search_claim")
            connection.execute("PRAGMA user_version = 2")

        migrated = Database(self.database_path)
        migrated.initialize()
        with migrated.connection() as connection:
            claim = connection.execute("SELECT search_id FROM controlled_search_claim WHERE claim_id = 1").fetchone()
            history_count = connection.execute("SELECT COUNT(*) FROM searches").fetchone()[0]
        self.assertEqual(claim["search_id"], submitted.id)
        self.assertEqual(history_count, 2)
        manager = TaskManager(migrated)
        fresh = manager.create_controlled_search("Bloquée après migration", {"mode": "PERSONNE_MORALE"})
        with self.assertRaises(InvalidTransition):
            manager.start_controlled_search(fresh.id)

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
        self.assertEqual(version, 3)
        self.assertTrue({"step", "result_summary_json"}.issubset(columns))
        self.assertEqual(tuple(preserved), ("legacy-id", "created", None))


if __name__ == "__main__":
    unittest.main()
