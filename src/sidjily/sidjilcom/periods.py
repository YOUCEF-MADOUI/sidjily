"""Découpage d'une recherche refusée (« Résultat de la Recherche Trop Grand ») par période d'inscription.

Le portail refuse d'afficher plus de 300 résultats. On relance alors la même recherche avec le filtre
« Période d'inscription : Du / Au », par fenêtres de plus en plus courtes : 10 ans, puis — seulement
pour une fenêtre encore refusée — 5 ans, 2 ans, 1 an et enfin 6 mois. Les fenêtres vont de la plus
récente à la plus ancienne, sans chevauchement (chaque fenêtre finit la veille du début de la suivante).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Callable

from sidjily.sidjilcom.criteria import SearchCriteria

# Borne basse demandée : les inscriptions antérieures au 1er janvier 1990 ne sont pas parcourues.
DEFAULT_START = date(1990, 1, 1)
# Durées successives en mois : 10 ans, 5 ans, 2 ans, 1 an, 6 mois.
SPLIT_STEPS_MONTHS: tuple[int, ...] = (120, 60, 24, 12, 6)


def _minus_months(day: date, months: int) -> date:
    year, month = divmod(day.year * 12 + (day.month - 1) - months, 12)
    month += 1
    # 29-31 du mois : on recule au dernier jour existant du mois visé.
    for candidate in (day.day, 30, 29, 28):
        try:
            return date(year, month, min(day.day, candidate))
        except ValueError:
            continue
    raise ValueError("date impossible")


def split_window(start: date, end: date, months: int) -> list[tuple[date, date]]:
    """Fenêtres [début, fin] de `months` mois couvrant [start, end], de la plus récente à la plus ancienne.

    Exemple (1 an) : 2025-11-28 → 2026-11-27, puis 2024-11-28 → 2025-11-27, …
    """
    if start > end:
        return []
    windows: list[tuple[date, date]] = []
    upper = end
    while upper >= start:
        lower = _minus_months(upper, months) + timedelta(days=1)
        lower = max(lower, start)
        windows.append((lower, upper))
        upper = lower - timedelta(days=1)
    return windows


def with_period(criteria: SearchCriteria, start: date, end: date) -> SearchCriteria:
    common = replace(
        criteria.common,
        date_inscription_du=start.isoformat(),
        date_inscription_au=end.isoformat(),
    )
    return replace(criteria, common=common)


@dataclass
class PeriodOutcome:
    start: date
    end: date
    status: str  # « collected », « no_results », « split », « skipped », « error »
    rows: int = 0
    detail: str = ""

    def describe(self) -> str:
        period = f"{self.start:%d/%m/%Y} → {self.end:%d/%m/%Y}"
        if self.status == "collected":
            return f"{period} : {self.rows} ligne(s)" + (f" — {self.detail}" if self.detail else "")
        if self.status == "no_results":
            return f"{period} : aucun résultat"
        if self.status == "split":
            return f"{period} : trop grand, découpée"
        if self.status == "error":
            return f"{period} : erreur, découpage interrompu" + (f" ({self.detail})" if self.detail else "")
        return f"{period} : trop grand même sur 6 mois — ignorée"


@dataclass
class PeriodPlan:
    """Parcourt les fenêtres en profondeur : une fenêtre refusée est remplacée par ses sous-fenêtres."""

    start: date
    end: date
    outcomes: list[PeriodOutcome] = field(default_factory=list)

    def run(
        self,
        run_window: Callable[[date, date], tuple[str, int]],
        *,
        on_progress: Callable[[str], None] | None = None,
        refused: set[tuple[date, date]] | None = None,
    ) -> list[PeriodOutcome]:
        """`run_window(début, fin)` renvoie (« too_large » | « collected » | « no_results » | « error », lignes).

        `refused` : périodes déjà refusées (« Trop Grand »). Une fenêtre identique à une période refusée
        — période plus courte que la durée du niveau — est découpée sans être soumise à nouveau.
        """
        known_refused = set(refused or ())
        detail = ""
        pending: list[tuple[date, date, int]] = [
            (lower, upper, 0) for lower, upper in split_window(self.start, self.end, SPLIT_STEPS_MONTHS[0])
        ]
        while pending:
            lower, upper, level = pending.pop(0)
            already_refused = (lower, upper) in known_refused
            if already_refused:
                status, rows = "too_large", 0
            else:
                if on_progress is not None:
                    on_progress(f"Période {lower:%d/%m/%Y} → {upper:%d/%m/%Y}…")
                result = run_window(lower, upper)
                status, rows = result[0], result[1]
                detail = str(result[2]) if len(result) > 2 else ""
            if status == "too_large":
                known_refused.add((lower, upper))
                if level + 1 < len(SPLIT_STEPS_MONTHS):
                    if not already_refused:
                        self.outcomes.append(PeriodOutcome(lower, upper, "split"))
                    children = split_window(lower, upper, SPLIT_STEPS_MONTHS[level + 1])
                    pending[0:0] = [(child_lower, child_upper, level + 1) for child_lower, child_upper in children]
                else:
                    self.outcomes.append(PeriodOutcome(lower, upper, "skipped"))
                continue
            if status == "error":
                self.outcomes.append(PeriodOutcome(lower, upper, status, rows, detail))
                break
            self.outcomes.append(PeriodOutcome(lower, upper, status, rows, detail))
        return self.outcomes
