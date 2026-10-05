"""Accès SQLite et schéma initial versionné."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


SCHEMA_VERSION = 2


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    """Fabrique de connexions SQLite; une connexion courte par opération."""

    def __init__(self, path: Path | str):
        self._memory = str(path) == ":memory:"
        if self._memory:
            # Une URI partagée conserve la base mémoire entre les connexions courtes.
            self.path: Path | str = f"file:sidjily-{uuid.uuid4().hex}?mode=memory&cache=shared"
            self._keeper = sqlite3.connect(self.path, uri=True, check_same_thread=False)
        else:
            self.path = Path(path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._keeper = None

    @contextmanager
    def connection(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10, uri=self._memory)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            if immediate:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        """Crée le schéma initial sans écraser les données existantes."""
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"Base trop récente (version {version}).")
            if version == 0:
                connection.executescript(
                    """
                    CREATE TABLE searches (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        criteria_json TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (status IN
                            ('pending','running','completed','failed','retry','suspended','cancelled')),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        last_error TEXT,
                        step TEXT NOT NULL DEFAULT 'created',
                        result_summary_json TEXT
                    );
                    CREATE TABLE tasks (
                        id TEXT PRIMARY KEY,
                        search_id TEXT NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
                        parent_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
                        task_type TEXT NOT NULL,
                        criteria_json TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (status IN
                            ('pending','running','completed','failed','retry','suspended','cancelled')),
                        attempts INTEGER NOT NULL DEFAULT 0,
                        result_count INTEGER NOT NULL DEFAULT 0,
                        error TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        started_at TEXT,
                        completed_at TEXT
                    );
                    CREATE INDEX tasks_search_status_idx ON tasks(search_id, status);
                    CREATE INDEX tasks_parent_idx ON tasks(parent_id);
                    CREATE TABLE results (
                        id TEXT PRIMARY KEY,
                        search_id TEXT NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
                        source_task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
                        dedupe_key TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE(search_id, dedupe_key)
                    );
                    CREATE INDEX results_search_idx ON results(search_id);
                    CREATE TABLE events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        search_id TEXT REFERENCES searches(id) ON DELETE CASCADE,
                        task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
                        level TEXT NOT NULL,
                        message TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX events_search_idx ON events(search_id, id);
                    CREATE TABLE settings (
                        key TEXT PRIMARY KEY,
                        value_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    PRAGMA user_version = 2;
                    """
                )
            elif version == 1:
                connection.execute(
                    "ALTER TABLE searches ADD COLUMN step TEXT NOT NULL DEFAULT 'created'"
                )
                connection.execute("ALTER TABLE searches ADD COLUMN result_summary_json TEXT")
                connection.execute("PRAGMA user_version = 2")

    @staticmethod
    def add_event(
        connection: sqlite3.Connection,
        message: str,
        *,
        level: str = "INFO",
        search_id: str | None = None,
        task_id: str | None = None,
    ) -> None:
        connection.execute(
            "INSERT INTO events(search_id, task_id, level, message, created_at) VALUES (?, ?, ?, ?, ?)",
            (search_id, task_id, level, message, utc_now()),
        )

    @staticmethod
    def encode_json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
