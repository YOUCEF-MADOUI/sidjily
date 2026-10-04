"""Modèles et statuts partagés par le cœur de l'application."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class Status(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRY = "retry"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class Search:
    id: str
    name: str
    criteria: dict[str, Any]
    status: Status
    created_at: str
    updated_at: str
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class SearchTask:
    id: str
    search_id: str
    parent_id: str | None
    task_type: str
    criteria: dict[str, Any]
    status: Status
    attempts: int
    result_count: int
    error: str | None
    created_at: str
    updated_at: str
