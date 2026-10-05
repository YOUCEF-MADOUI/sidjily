"""Gestion persistante des recherches et de leurs sous-tâches."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sidjily.database import Database, utc_now
from sidjily.models import Search, SearchTask, Status


class InvalidTransition(ValueError):
    """Une opération ne correspond pas à l'état courant de la recherche."""


def _safe_structural_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Conserve uniquement les métadonnées structurelles de résultats explicitement autorisées."""
    def bounded_count(key: str) -> int | None:
        value = summary.get(key)
        return value if type(value) is int and 0 <= value <= 1_000_000_000 else None

    def safe_text(value: object, limit: int) -> str:
        return " ".join(str(value).split())[:limit]

    def safe_url(value: object) -> str:
        try:
            parsed = urlsplit(str(value))
            if parsed.scheme.casefold() != "https" or not parsed.hostname:
                return ""
            host = parsed.hostname.casefold()
            trusted = host == "sidjilcom.cnrc.dz" or host.endswith(".sidjilcom.cnrc.dz")
            netloc = host
            if parsed.port and parsed.port not in (80, 443):
                netloc = f"{host}:{parsed.port}"
            path = parsed.path if trusted else "/[chemin masqué]"
            return urlunsplit(("https", netloc, path or "/", "", ""))[:500]
        except (TypeError, ValueError):
            return ""

    columns = summary.get("columns", [])
    errors = summary.get("errors", [])
    if not isinstance(columns, (list, tuple)):
        columns = []
    if not isinstance(errors, (list, tuple)):
        errors = []
    return {
        "title": safe_text(summary.get("title", ""), 160),
        "url": safe_url(summary.get("url", "")),
        "result_count": bounded_count("result_count"),
        "table_count": bounded_count("table_count") or 0,
        "row_count": bounded_count("row_count") or 0,
        "columns": [safe_text(item, 120) for item in columns[:40] if isinstance(item, str)],
        "pagination_visible": bool(summary.get("pagination_visible", False)),
        "no_results": bool(summary.get("no_results", False)),
        "errors": [safe_text(item, 200) for item in errors[:8] if isinstance(item, str)],
        "session_expired": bool(summary.get("session_expired", False)),
        "result_zone_found": bool(summary.get("result_zone_found", False)),
        "result_zone_tag": safe_text(summary.get("result_zone_tag", ""), 24),
        "details_opened": False,
        "automatic_pagination": False,
        "company_rows_collected": False,
    }


class TaskManager:
    """Persiste chaque étape pour permettre une reprise sans perdre la progression."""

    def __init__(self, database: Database):
        self.database = database
        self.database.initialize()

    def create_search(self, name: str, criteria: dict[str, Any]) -> Search:
        return self._create_search(name, criteria, task_type="search", step="created")

    def create_draft(
        self, name: str, criteria: dict[str, Any], summary: dict[str, Any]
    ) -> Search:
        """Persiste un brouillon local sans tâche exécutable ni interaction navigateur."""
        return self._create_search(
            name, criteria, task_type="sidjily_search_draft", step="draft",
            result_summary=summary,
        )

    def update_draft(
        self, search_id: str, name: str, criteria: dict[str, Any], summary: dict[str, Any]
    ) -> Search:
        """Met à jour un brouillon SIDJILY sans le faire progresser vers une soumission."""
        now = utc_now()
        with self.database.connection(immediate=True) as connection:
            task = connection.execute(
                "SELECT id, status FROM tasks WHERE search_id = ? AND task_type = 'sidjily_search_draft'",
                (search_id,),
            ).fetchone()
            if task is None:
                raise KeyError(f"Brouillon introuvable : {search_id}")
            if task["status"] != Status.PENDING:
                raise InvalidTransition("Seul un brouillon en attente peut être modifié.")
            connection.execute(
                "UPDATE searches SET name = ?, criteria_json = ?, result_summary_json = ?, "
                "updated_at = ?, step = 'draft' WHERE id = ?",
                (
                    name.strip() or "Recherche sans titre",
                    self.database.encode_json(criteria),
                    self.database.encode_json(summary),
                    now,
                    search_id,
                ),
            )
            connection.execute(
                "UPDATE tasks SET criteria_json = ?, updated_at = ? WHERE id = ?",
                (self.database.encode_json(criteria), now, task["id"]),
            )
            self.database.add_event(
                connection,
                "Brouillon de recherche SIDJILY mis à jour; aucune recherche Sidjilcom envoyée.",
                search_id=search_id,
                task_id=task["id"],
            )
        return self.get_search(search_id)

    def promote_draft_to_controlled_search(
        self, search_id: str, name: str, criteria: dict[str, Any]
    ) -> Search:
        """Convertit atomiquement un brouillon confirmé en tentative réelle unique."""
        now = utc_now()
        with self.database.connection(immediate=True) as connection:
            task = connection.execute(
                "SELECT id, status FROM tasks WHERE search_id = ? AND task_type = 'sidjily_search_draft'",
                (search_id,),
            ).fetchone()
            if task is None:
                raise KeyError(f"Brouillon introuvable : {search_id}")
            if task["status"] != Status.PENDING:
                raise InvalidTransition("Seul un brouillon en attente peut être lancé.")
            claimed = connection.execute(
                "SELECT 1 FROM controlled_search_claim WHERE claim_id = 1"
            ).fetchone()
            if claimed is not None:
                raise InvalidTransition("La recherche contrôlée unique a déjà été réservée; ce brouillon ne peut pas être soumis.")
            connection.execute(
                "UPDATE tasks SET task_type = 'sidjilcom_controlled_search', criteria_json = ?, "
                "updated_at = ? WHERE id = ?",
                (self.database.encode_json(criteria), now, task["id"]),
            )
            connection.execute(
                "UPDATE searches SET name = ?, criteria_json = ?, status = ?, step = 'prepared', "
                "updated_at = ?, last_error = NULL, result_summary_json = NULL WHERE id = ?",
                (
                    name.strip() or "Recherche sans titre", self.database.encode_json(criteria),
                    Status.PENDING, now, search_id,
                ),
            )
            connection.execute(
                "INSERT INTO controlled_search_claim(claim_id, search_id, claimed_at) VALUES (1, ?, ?)",
                (search_id, now),
            )
            self.database.add_event(
                connection,
                "Brouillon explicitement confirmé et converti en recherche contrôlée; aucune reprise automatique.",
                search_id=search_id,
                task_id=task["id"],
            )
        return self.get_search(search_id)

    def list_drafts(self, limit: int = 100) -> list[Search]:
        """Retourne les brouillons préparatoires, du plus récent au plus ancien."""
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT s.* FROM searches AS s JOIN tasks AS t ON t.search_id = s.id "
                "WHERE t.task_type = 'sidjily_search_draft' AND t.parent_id IS NULL "
                "ORDER BY s.created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._search_from_row(row) for row in rows]

    def is_draft(self, search_id: str) -> bool:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM tasks WHERE search_id = ? AND task_type = 'sidjily_search_draft' LIMIT 1",
                (search_id,),
            ).fetchone()
        return row is not None

    def create_controlled_search(self, name: str, criteria: dict[str, Any]) -> Search:
        """Crée une recherche réelle réservée au flux confirmé, sans chemin de reprise."""
        return self._create_search(
            name, criteria, task_type="sidjilcom_controlled_search", step="prepared"
        )

    def _create_search(
        self, name: str, criteria: dict[str, Any], *, task_type: str, step: str,
        result_summary: dict[str, Any] | None = None,
    ) -> Search:
        search_id, task_id = str(uuid.uuid4()), str(uuid.uuid4())
        now = utc_now()
        with self.database.connection(immediate=True) as connection:
            if task_type == "sidjilcom_controlled_search":
                claimed = connection.execute(
                    "SELECT 1 FROM controlled_search_claim WHERE claim_id = 1"
                ).fetchone()
                if claimed is not None:
                    raise InvalidTransition("La recherche contrôlée unique a déjà été réservée; aucune nouvelle soumission n'est autorisée.")
            connection.execute(
                "INSERT INTO searches(id, name, criteria_json, status, created_at, updated_at, step, result_summary_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    search_id, name.strip() or "Recherche sans titre",
                    self.database.encode_json(criteria), Status.PENDING, now, now, step,
                    self.database.encode_json(result_summary) if result_summary is not None else None,
                ),
            )
            connection.execute(
                "INSERT INTO tasks(id, search_id, task_type, criteria_json, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task_id, search_id, task_type, self.database.encode_json(criteria), Status.PENDING, now, now),
            )
            if task_type == "sidjilcom_controlled_search":
                connection.execute(
                    "INSERT INTO controlled_search_claim(claim_id, search_id, claimed_at) VALUES (1, ?, ?)",
                    (search_id, now),
                )
            message = (
                "Recherche Sidjilcom contrôlée créée; étape préparée, aucune recherche soumise."
                if task_type == "sidjilcom_controlled_search"
                else "Brouillon SIDJILY créé; aucune requête Sidjilcom envoyée."
                if task_type == "sidjily_search_draft"
                else "Recherche créée et mise en attente."
            )
            self.database.add_event(connection, message, search_id=search_id, task_id=task_id)
        return self.get_search(search_id)

    def start_controlled_search(self, search_id: str, *, step: str = "mode_selection") -> SearchTask:
        """Démarre une fois seulement; une tâche déjà tentée ne peut pas être relancée."""
        now = utc_now()
        with self.database.connection(immediate=True) as connection:
            task = connection.execute(
                "SELECT * FROM tasks WHERE search_id = ? AND task_type = 'sidjilcom_controlled_search'",
                (search_id,),
            ).fetchone()
            if task is None:
                raise KeyError(f"Recherche Sidjilcom introuvable : {search_id}")
            if task["status"] != Status.PENDING or task["attempts"] != 0:
                raise InvalidTransition("Cette recherche contrôlée a déjà commencé et ne peut pas être relancée.")
            connection.execute(
                "UPDATE tasks SET status = ?, attempts = 1, updated_at = ?, started_at = ? WHERE id = ?",
                (Status.RUNNING, now, now, task["id"]),
            )
            connection.execute(
                "UPDATE searches SET status = ?, step = ?, updated_at = ?, last_error = NULL WHERE id = ?",
                (Status.RUNNING, step, now, search_id),
            )
            self.database.add_event(
                connection, f"Étape de recherche contrôlée : {step}.",
                search_id=search_id, task_id=task["id"],
            )
            updated = connection.execute("SELECT * FROM tasks WHERE id = ?", (task["id"],)).fetchone()
        return self._task_from_row(updated)

    def update_controlled_search_step(self, search_id: str, step: object) -> None:
        """Persiste l'étape avant chaque interaction du navigateur."""
        step_value = str(getattr(step, "value", step))
        allowed = {
            "prepared", "mode_selection", "form_validation", "filling_criteria",
            "submitting", "submitted", "observing_results", "results_detected",
            "no_results", "completed", "failed", "result_unknown",
        }
        if step_value not in allowed:
            raise ValueError("Étape de recherche contrôlée inconnue.")
        now = utc_now()
        with self.database.connection(immediate=True) as connection:
            task = connection.execute(
                "SELECT id, status FROM tasks WHERE search_id = ? AND task_type = 'sidjilcom_controlled_search'",
                (search_id,),
            ).fetchone()
            if task is None:
                raise KeyError(f"Recherche Sidjilcom introuvable : {search_id}")
            if task["status"] != Status.RUNNING:
                raise InvalidTransition("Seule une recherche contrôlée en cours peut avancer.")
            connection.execute(
                "UPDATE searches SET step = ?, updated_at = ? WHERE id = ?",
                (step_value, now, search_id),
            )
            connection.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now, task["id"]))
            self.database.add_event(
                connection, f"Étape de recherche contrôlée : {step_value}.",
                search_id=search_id, task_id=task["id"],
            )

    def update_controlled_search_pre_submit_diagnostic(
        self, search_id: str, diagnostic: dict[str, Any]
    ) -> None:
        """Enregistre un contrôle de formulaire dénué de valeurs et secrets, avant le clic."""
        if diagnostic.get("kind") != "sidjilcom_pre_submit_diagnostic":
            raise ValueError("Diagnostic préalable invalide.")
        if any(diagnostic.get(key) is not False for key in (
            "sensitive_values_saved", "cookies_saved", "tokens_saved", "session_identifiers_saved",
        )):
            raise ValueError("Un diagnostic préalable ne peut pas contenir de secrets.")
        safe_controls = []
        controls = diagnostic.get("controls", [])
        criteria = diagnostic.get("criteria", [])
        if not isinstance(controls, list) or not isinstance(criteria, list):
            raise ValueError("Contrôles du diagnostic préalable invalides.")
        allowed_criteria = {"activite", "commune_wilaya"}
        if any(not isinstance(item, str) or item not in allowed_criteria for item in criteria):
            raise ValueError("Critères du diagnostic préalable invalides.")
        allowed_control_keys = {"criterion", "tag", "type", "visible", "enabled", "empty_before_fill"}
        for control in controls:
            if not isinstance(control, dict) or set(control) - allowed_control_keys:
                raise ValueError("Le diagnostic préalable contient un champ non autorisé.")
            if control.get("criterion") not in allowed_criteria:
                raise ValueError("Critère de contrôle préalable non autorisé.")
            if control.get("tag") not in {"input", "select", "textarea"}:
                raise ValueError("Type de contrôle préalable non autorisé.")
            if control.get("type") not in {"text", "date", "select-one"}:
                raise ValueError("Type d'entrée préalable non autorisé.")
            if any(type(control.get(key)) is not bool for key in ("visible", "enabled", "empty_before_fill")):
                raise ValueError("État de contrôle préalable invalide.")
            safe_controls.append({key: control.get(key) for key in allowed_control_keys if key in control})
        safe_diagnostic = {
            "kind": "sidjilcom_pre_submit_diagnostic",
            "mode": "PERSONNE_MORALE" if diagnostic.get("mode") == "PERSONNE_MORALE" else "",
            "criteria": list(criteria[:20]),
            "controls": safe_controls,
            "all_visible_controls_empty_before_fill": bool(diagnostic.get("all_visible_controls_empty_before_fill", False)),
            "search_button_count": max(0, min(int(diagnostic.get("search_button_count", 0)), 20)),
            "search_button_enabled": bool(diagnostic.get("search_button_enabled", False)),
            "sensitive_values_saved": False,
            "cookies_saved": False,
            "tokens_saved": False,
            "session_identifiers_saved": False,
            "captured_at": utc_now(),
        }
        now = utc_now()
        with self.database.connection(immediate=True) as connection:
            task = connection.execute(
                "SELECT id, status FROM tasks WHERE search_id = ? AND task_type = 'sidjilcom_controlled_search'",
                (search_id,),
            ).fetchone()
            if task is None:
                raise KeyError(f"Recherche Sidjilcom introuvable : {search_id}")
            if task["status"] != Status.RUNNING:
                raise InvalidTransition("Le diagnostic préalable exige une recherche contrôlée en cours.")
            connection.execute(
                "UPDATE searches SET result_summary_json = ?, updated_at = ? WHERE id = ?",
                (self.database.encode_json({"pre_submit_diagnostic": safe_diagnostic}), now, search_id),
            )
            connection.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now, task["id"]))
            self.database.add_event(
                connection,
                "Diagnostic structurel préalable enregistré sans valeurs, cookies, tokens ni identifiants.",
                search_id=search_id, task_id=task["id"],
            )

    def complete_controlled_search(self, search_id: str, summary: dict[str, Any]) -> None:
        now = utc_now()
        safe_summary = _safe_structural_summary(summary)
        count = safe_summary.get("result_count")
        result_count = count if isinstance(count, int) and count >= 0 else 0
        with self.database.connection(immediate=True) as connection:
            task = connection.execute(
                "SELECT t.id, t.status, s.result_summary_json FROM tasks AS t "
                "JOIN searches AS s ON s.id = t.search_id "
                "WHERE t.search_id = ? AND t.task_type = 'sidjilcom_controlled_search'",
                (search_id,),
            ).fetchone()
            if task is None:
                raise KeyError(f"Recherche Sidjilcom introuvable : {search_id}")
            if task["status"] != Status.RUNNING:
                raise InvalidTransition("Seule une recherche contrôlée en cours peut se terminer.")
            connection.execute(
                "UPDATE tasks SET status = ?, result_count = ?, updated_at = ?, completed_at = ?, error = NULL WHERE id = ?",
                (Status.COMPLETED, result_count, now, now, task["id"]),
            )
            terminal_step = "no_results" if safe_summary.get("no_results") or count == 0 else "results_detected"
            previous = json.loads(task["result_summary_json"]) if task["result_summary_json"] else {}
            persisted_summary = dict(safe_summary)
            for key in ("pre_submit_diagnostic", "follow_up_structural_diagnostic"):
                if key in previous:
                    persisted_summary[key] = previous[key]
            connection.execute(
                "UPDATE searches SET status = ?, step = ?, updated_at = ?, last_error = NULL, result_summary_json = ? WHERE id = ?",
                (Status.COMPLETED, terminal_step, now, self.database.encode_json(persisted_summary), search_id),
            )
            self.database.add_event(
                connection, "Recherche contrôlée terminée; seule la structure des résultats a été observée.",
                search_id=search_id, task_id=task["id"],
            )

    def fail_controlled_search(self, search_id: str, error: str) -> None:
        safe_error = error.strip()[:240] or "Erreur de recherche contrôlée."
        now = utc_now()
        with self.database.connection(immediate=True) as connection:
            task = connection.execute(
                "SELECT t.id, t.status, s.step FROM tasks AS t JOIN searches AS s ON s.id = t.search_id "
                "WHERE t.search_id = ? AND t.task_type = 'sidjilcom_controlled_search'",
                (search_id,),
            ).fetchone()
            if task is None:
                raise KeyError(f"Recherche Sidjilcom introuvable : {search_id}")
            if task["status"] != Status.RUNNING:
                return
            terminal_step = (
                "result_unknown"
                if task["step"] in {
                    "submitting", "submitted", "observing_results", "results_detected", "no_results",
                }
                else "failed"
            )
            message = (
                "La soumission a pu avoir lieu; le résultat est inconnu. Ne relancez pas cette recherche."
                if terminal_step == "result_unknown" else safe_error
            )
            connection.execute(
                "UPDATE tasks SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                (Status.FAILED, message, now, task["id"]),
            )
            connection.execute(
                "UPDATE searches SET status = ?, step = ?, last_error = ?, updated_at = ? WHERE id = ?",
                (Status.FAILED, terminal_step, message, now, search_id),
            )
            if terminal_step == "failed":
                # Autorise une future tentative seulement si l'état prouve qu'aucun clic
                # n'a commencé; les tâches existantes ne sont jamais reprises.
                connection.execute(
                    "DELETE FROM controlled_search_claim WHERE claim_id = 1 AND search_id = ?",
                    (search_id,),
                )
            self.database.add_event(
                connection, f"Recherche contrôlée arrêtée : {message}", level="ERROR",
                search_id=search_id, task_id=task["id"],
            )

    def store_unknown_search_diagnostic(self, search_id: str, summary: dict[str, Any]) -> None:
        """Ajoute une observation en lecture seule à une recherche soumise de résultat inconnu."""
        now = utc_now()
        with self.database.connection(immediate=True) as connection:
            row = connection.execute(
                "SELECT s.step, s.result_summary_json, t.id AS task_id, t.status AS task_status "
                "FROM searches AS s JOIN tasks AS t ON t.search_id = s.id "
                "WHERE s.id = ? AND t.task_type = 'sidjilcom_controlled_search'",
                (search_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Recherche Sidjilcom introuvable : {search_id}")
            if row["step"] != "result_unknown" or row["task_status"] != Status.FAILED:
                raise InvalidTransition("Seule une recherche de résultat inconnu peut recevoir un diagnostic ultérieur.")
            current = json.loads(row["result_summary_json"]) if row["result_summary_json"] else {}
            current["follow_up_structural_diagnostic"] = _safe_structural_summary(summary)
            connection.execute(
                "UPDATE searches SET result_summary_json = ?, updated_at = ? WHERE id = ?",
                (self.database.encode_json(current), now, search_id),
            )
            self.database.add_event(
                connection,
                "Diagnostic structurel ultérieur enregistré en lecture seule; aucune nouvelle recherche soumise.",
                search_id=search_id, task_id=row["task_id"],
            )

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
                "SELECT * FROM tasks WHERE search_id = ? AND task_type NOT IN "
                "('sidjilcom_controlled_search', 'sidjily_search_draft') "
                "AND status IN (?, ?) ORDER BY created_at, rowid LIMIT 1",
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
            if task["task_type"] in {"sidjilcom_controlled_search", "sidjily_search_draft"}:
                raise InvalidTransition("Utilisez le cycle spécialisé correspondant à ce type de tâche.")
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
            if task["task_type"] in {"sidjilcom_controlled_search", "sidjily_search_draft"}:
                raise InvalidTransition("Utilisez le cycle spécialisé correspondant à ce type de tâche.")
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
            if parent["task_type"] == "sidjilcom_controlled_search":
                raise InvalidTransition("Une recherche contrôlée ne peut pas être subdivisée.")
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
            controlled = connection.execute(
                "SELECT 1 FROM tasks WHERE search_id = ? AND task_type = 'sidjilcom_controlled_search'",
                (search_id,),
            ).fetchone()
            if controlled:
                raise InvalidTransition("Une recherche réelle contrôlée ne peut pas être suspendue ou relancée.")
            draft = connection.execute(
                "SELECT 1 FROM tasks WHERE search_id = ? AND task_type = 'sidjily_search_draft'",
                (search_id,),
            ).fetchone()
            if draft:
                raise InvalidTransition("Un brouillon se modifie depuis le parcours de préparation.")
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
            controlled = connection.execute(
                "SELECT 1 FROM tasks WHERE search_id = ? AND task_type = 'sidjilcom_controlled_search'",
                (search_id,),
            ).fetchone()
            if controlled:
                raise InvalidTransition("Une recherche réelle contrôlée ne peut pas être relancée automatiquement.")
            draft = connection.execute(
                "SELECT 1 FROM tasks WHERE search_id = ? AND task_type = 'sidjily_search_draft'",
                (search_id,),
            ).fetchone()
            if draft:
                raise InvalidTransition("Un brouillon se restaure depuis le parcours de préparation.")
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
        """Récupère les tâches normales; une recherche contrôlée interrompue n'est jamais reprise."""
        with self.database.connection(immediate=True) as connection:
            running = connection.execute(
                "SELECT id, search_id, task_type FROM tasks WHERE status = ?", (Status.RUNNING,)
            ).fetchall()
            generic_running = [row for row in running if row["task_type"] != "sidjilcom_controlled_search"]
            controlled_running = [row for row in running if row["task_type"] == "sidjilcom_controlled_search"]
            now = utc_now()
            if generic_running:
                generic_ids = [row["id"] for row in generic_running]
                placeholders = ",".join("?" for _ in generic_ids)
                connection.execute(
                    f"UPDATE tasks SET status = ?, updated_at = ? WHERE id IN ({placeholders})",
                    (Status.PENDING, now, *generic_ids),
                )
                for search_id in {row["search_id"] for row in generic_running}:
                    connection.execute(
                        "UPDATE searches SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
                        (Status.SUSPENDED, now, search_id, Status.RUNNING),
                    )
                    self.database.add_event(
                        connection,
                        "Interruption détectée; tâche normale conservée pour reprise.",
                        level="WARNING", search_id=search_id,
                    )
            for row in controlled_running:
                search = connection.execute(
                    "SELECT step FROM searches WHERE id = ?", (row["search_id"],)
                ).fetchone()
                uncertain = search is not None and search["step"] in {
                    "submitting", "submitted", "observing_results", "results_detected", "no_results",
                }
                terminal_step = "result_unknown" if uncertain else "failed"
                message = (
                    "Interruption pendant ou après la soumission; le résultat est inconnu. "
                    "La recherche ne sera pas relancée automatiquement."
                    if uncertain else
                    "Interruption d'une recherche contrôlée; aucune reprise automatique n'est autorisée."
                )
                connection.execute(
                    "UPDATE tasks SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                    (Status.FAILED, message, now, row["id"]),
                )
                connection.execute(
                    "UPDATE searches SET status = ?, step = ?, last_error = ?, updated_at = ? WHERE id = ?",
                    (Status.FAILED, terminal_step, message, now, row["search_id"]),
                )
                if not uncertain:
                    connection.execute(
                        "DELETE FROM controlled_search_claim WHERE claim_id = 1 AND search_id = ?",
                        (row["search_id"],),
                    )
                self.database.add_event(
                    connection, message, level="WARNING",
                    search_id=row["search_id"], task_id=row["id"],
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
            last_error=row["last_error"], step=row["step"],
            result_summary=(
                json.loads(row["result_summary_json"])
                if row["result_summary_json"] is not None else None
            ),
        )

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> SearchTask:
        return SearchTask(
            id=row["id"], search_id=row["search_id"], parent_id=row["parent_id"],
            task_type=row["task_type"], criteria=json.loads(row["criteria_json"]),
            status=Status(row["status"]), attempts=row["attempts"], result_count=row["result_count"],
            error=row["error"], created_at=row["created_at"], updated_at=row["updated_at"],
        )
