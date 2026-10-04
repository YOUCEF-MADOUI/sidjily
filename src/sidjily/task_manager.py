"""Gestion persistante des recherches et de leurs sous-tâches."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable
from typing import Any

from sidjily.database import Database, utc_now
from sidjily.models import Search, SearchTask, Status


class InvalidTransition(ValueError):
    """Une opération ne correspond pas à l'état courant de la recherche."""


class TaskManager:
    """Persiste chaque étape pour permettre une reprise sans perdre la progression."""

    def __init__(self, database: Database):
        self.database = database
        self.database.initialize()

    def create_search(self, name: str, criteria: dict[str, Any]) -> Search:
        search_id, task_id = str(uuid.uuid4()), str(uuid.uuid4())
        now = utc_now()
        with self.database.connection(immediate=True) as connection:
            connection.execute(
                "INSERT INTO searches(id, name, criteria_json, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (search_id, name.strip() or "Recherche sans titre", self.database.encode_json(criteria), Status.PENDING, now, now),
            )
            connection.execute(
                "INSERT INTO tasks(id, search_id, task_type, criteria_json, status, created_at, updated_at) "
                "VALUES (?, ?, 'search', ?, ?, ?, ?)",
                (task_id, search_id, self.database.encode_json(criteria), Status.PENDING, now, now),
            )
            self.database.add_event(connection, "Recherche créée et mise en attente.", search_id=search_id)
        return self.get_search(search_id)

    def get_search(self, search_id: str) -> Search:
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM searches WHERE id = ?", (search_id,)).fetchone()
        if row is None:
            raise KeyError(f"Recherche introuvable : {search_id}")
        return self._search_from_row(row)

    def list_searches(self, limit: int = 100) -> list[Search]:
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM searches ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._search_from_row(row) for row in rows]

    def list_tasks(self, search_id: str) -> list[SearchTask]:
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks WHERE search_id = ? ORDER BY created_at, rowid", (search_id,)
            ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def list_events(self, search_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT level, message, created_at FROM events WHERE search_id = ? "
                "ORDER BY id DESC LIMIT ?", (search_id, limit)
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def start_next_task(self, search_id: str) -> SearchTask | None:
        """Réserve atomiquement la prochaine tâche en attente (aucun navigateur ici)."""
        now = utc_now()
        with self.database.connection(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE search_id = ? AND status IN (?, ?) "
                "ORDER BY created_at, rowid LIMIT 1",
                (search_id, Status.PENDING, Status.RETRY),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE tasks SET status = ?, attempts = attempts + 1, started_at = ?, updated_at = ?, error = NULL "
                "WHERE id = ?",
                (Status.RUNNING, now, now, row["id"]),
            )
            connection.execute(
                "UPDATE searches SET status = ?, updated_at = ?, last_error = NULL WHERE id = ?",
                (Status.RUNNING, now, search_id),
            )
            self.database.add_event(
                connection, "Tâche démarrée.", search_id=search_id, task_id=row["id"]
            )
            updated = connection.execute("SELECT * FROM tasks WHERE id = ?", (row["id"],)).fetchone()
        return self._task_from_row(updated)

    def complete_task(self, task_id: str, result_count: int = 0) -> None:
        if result_count < 0:
            raise ValueError("Le nombre de résultats ne peut pas être négatif.")
        with self.database.connection(immediate=True) as connection:
            task = self._require_task(connection, task_id)
            if task["status"] != Status.RUNNING:
                raise InvalidTransition("Seule une tâche en cours peut être terminée.")
            now = utc_now()
            connection.execute(
                "UPDATE tasks SET status = ?, result_count = ?, updated_at = ?, completed_at = ?, error = NULL "
                "WHERE id = ?",
                (Status.COMPLETED, result_count, now, now, task_id),
            )
            self.database.add_event(
                connection, f"Tâche terminée ({result_count} résultat(s)).", search_id=task["search_id"], task_id=task_id
            )
            self._refresh_search_status(connection, task["search_id"], now)

    def fail_task(self, task_id: str, error: str) -> None:
        with self.database.connection(immediate=True) as connection:
            task = self._require_task(connection, task_id)
            if task["status"] != Status.RUNNING:
                raise InvalidTransition("Seule une tâche en cours peut échouer.")
            now = utc_now()
            connection.execute(
                "UPDATE tasks SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                (Status.FAILED, error, now, task_id),
            )
            connection.execute(
                "UPDATE searches SET status = ?, last_error = ?, updated_at = ? WHERE id = ?",
                (Status.FAILED, error, now, task["search_id"]),
            )
            self.database.add_event(
                connection, f"Échec de la tâche : {error}", level="ERROR", search_id=task["search_id"], task_id=task_id
            )

    def add_subtasks(
        self,
        parent_task_id: str,
        children: Iterable[tuple[str, dict[str, Any]]],
        *,
        result_count: int = 0,
    ) -> list[str]:
        """Ajoute les sous-tâches avant de clôturer leur parent, dans une transaction."""
        if result_count < 0:
            raise ValueError("Le nombre de résultats ne peut pas être négatif.")
        child_specs = list(children)
        with self.database.connection(immediate=True) as connection:
            parent = self._require_task(connection, parent_task_id)
            if parent["status"] != Status.RUNNING:
                raise InvalidTransition("La tâche parente doit être en cours.")
            now = utc_now()
            child_ids = []
            for task_type, criteria in child_specs:
                child_id = str(uuid.uuid4())
                child_ids.append(child_id)
                connection.execute(
                    "INSERT INTO tasks(id, search_id, parent_id, task_type, criteria_json, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (child_id, parent["search_id"], parent_task_id, task_type, self.database.encode_json(criteria), Status.PENDING, now, now),
                )
            connection.execute(
                "UPDATE tasks SET status = ?, result_count = ?, updated_at = ?, completed_at = ? WHERE id = ?",
                (Status.COMPLETED, result_count, now, now, parent_task_id),
            )
            description = f"Tâche subdivisée en {len(child_ids)} sous-tâche(s)."
            self.database.add_event(
                connection, description, search_id=parent["search_id"], task_id=parent_task_id
            )
            self._refresh_search_status(connection, parent["search_id"], now)
        return child_ids

    def pause_search(self, search_id: str) -> None:
        with self.database.connection(immediate=True) as connection:
            search = connection.execute("SELECT status FROM searches WHERE id = ?", (search_id,)).fetchone()
            if search is None:
                raise KeyError(f"Recherche introuvable : {search_id}")
            if search["status"] not in (Status.PENDING, Status.RUNNING, Status.RETRY):
                raise InvalidTransition("Cette recherche ne peut pas être suspendue dans son état actuel.")
            now = utc_now()
            connection.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE search_id = ? AND status IN (?, ?, ?)",
                (Status.SUSPENDED, now, search_id, Status.PENDING, Status.RUNNING, Status.RETRY),
            )
            connection.execute(
                "UPDATE searches SET status = ?, updated_at = ? WHERE id = ?",
                (Status.SUSPENDED, now, search_id),
            )
            self.database.add_event(connection, "Recherche suspendue.", search_id=search_id)

    def resume_search(self, search_id: str) -> None:
        with self.database.connection(immediate=True) as connection:
            search = connection.execute("SELECT status FROM searches WHERE id = ?", (search_id,)).fetchone()
            if search is None:
                raise KeyError(f"Recherche introuvable : {search_id}")
            if search["status"] not in (Status.SUSPENDED, Status.FAILED, Status.RETRY):
                raise InvalidTransition("Seules les recherches suspendues ou en échec peuvent reprendre.")
            now = utc_now()
            connection.execute(
                "UPDATE tasks SET status = ?, error = NULL, updated_at = ? "
                "WHERE search_id = ? AND status IN (?, ?, ?)",
                (Status.PENDING, now, search_id, Status.SUSPENDED, Status.FAILED, Status.RETRY),
            )
            connection.execute(
                "UPDATE searches SET status = ?, last_error = NULL, updated_at = ? WHERE id = ?",
                (Status.PENDING, now, search_id),
            )
            self.database.add_event(connection, "Reprise demandée; les étapes terminées sont conservées.", search_id=search_id)

    def recover_interrupted(self) -> int:
        """Rend reprenables les tâches laissées en cours après un arrêt brutal."""
        with self.database.connection(immediate=True) as connection:
            running = connection.execute(
                "SELECT id, search_id FROM tasks WHERE status = ?", (Status.RUNNING,)
            ).fetchall()
            now = utc_now()
            connection.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE status = ?",
                (Status.PENDING, now, Status.RUNNING),
            )
            connection.execute(
                "UPDATE searches SET status = ?, updated_at = ? WHERE status = ?",
                (Status.SUSPENDED, now, Status.RUNNING),
            )
            for task in running:
                self.database.add_event(
                    connection,
                    "Interruption détectée; tâche conservée pour reprise.",
                    level="WARNING",
                    search_id=task["search_id"],
                    task_id=task["id"],
                )
        return len(running)

    @staticmethod
    def _require_task(connection: sqlite3.Connection, task_id: str) -> sqlite3.Row:
        task = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            raise KeyError(f"Tâche introuvable : {task_id}")
        return task

    @staticmethod
    def _refresh_search_status(connection: sqlite3.Connection, search_id: str, now: str) -> None:
        statuses = [row[0] for row in connection.execute(
            "SELECT status FROM tasks WHERE search_id = ?", (search_id,)
        ).fetchall()]
        if statuses and all(status == Status.COMPLETED for status in statuses):
            new_status = Status.COMPLETED
        elif any(status == Status.FAILED for status in statuses) and not any(
            status in (Status.PENDING, Status.RUNNING, Status.RETRY, Status.SUSPENDED) for status in statuses
        ):
            new_status = Status.FAILED
        else:
            new_status = Status.RUNNING
        connection.execute(
            "UPDATE searches SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, now, search_id),
        )

    @staticmethod
    def _search_from_row(row: sqlite3.Row) -> Search:
        return Search(
            id=row["id"], name=row["name"], criteria=json.loads(row["criteria_json"]),
            status=Status(row["status"]), created_at=row["created_at"], updated_at=row["updated_at"],
            last_error=row["last_error"],
        )

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> SearchTask:
        return SearchTask(
            id=row["id"], search_id=row["search_id"], parent_id=row["parent_id"],
            task_type=row["task_type"], criteria=json.loads(row["criteria_json"]),
            status=Status(row["status"]), attempts=row["attempts"], result_count=row["result_count"],
            error=row["error"], created_at=row["created_at"], updated_at=row["updated_at"],
        )
