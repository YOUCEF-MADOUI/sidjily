"""Orchestration sûre des tests d'autocomplétion, sans recherche ni endpoint direct.

Les accès au navigateur sont délégués à un pilote injectable. Les tests unitaires
utilisent un pilote simulé; la mécanique DOM Playwright est isolée dans
``autocomplete_playwright.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Any, Protocol
from uuid import uuid4

from sidjily.sidjilcom.criteria import ComponentType, SIDJILCOM_CONTROL_MAP


AUTOCOMPLETE_FIELD_CRITERIA: dict[str, tuple[str, ...]] = {
    "activite": ("activite",),
    "commune_wilaya": ("commune_wilaya",),
    "nationalite": ("physique.nationalite", "morale.nationalite"),
}
AUTOCOMPLETE_FIELD_LABELS: dict[str, str] = {
    "activite": "Activité (activi)",
    "commune_wilaya": "Commune/Wilaya d'inscription (wilcom)",
    "nationalite": "Nationalité (nation)",
}


class AutocompleteTestError(RuntimeError):
    """Erreur de test avec texte fixe; aucune valeur saisie n'est incluse."""


class AutocompleteFieldNotMapped(AutocompleteTestError):
    def __init__(self) -> None:
        super().__init__("Champ autocomplete absent du mapping central.")


class AutocompleteControlNotFound(AutocompleteTestError):
    def __init__(self) -> None:
        super().__init__("Composant autocomplete absent de la page courante.")


class AutocompleteControlAmbiguous(AutocompleteTestError):
    def __init__(self) -> None:
        super().__init__("Plusieurs composants autocomplete visibles correspondent au mapping.")


class AutocompleteControlDisabled(AutocompleteTestError):
    def __init__(self) -> None:
        super().__init__("Le composant autocomplete est désactivé ou non modifiable.")


class AutocompleteFieldNotEmpty(AutocompleteTestError):
    def __init__(self) -> None:
        super().__init__("Le champ doit être vide avant le test; effacez-le dans le navigateur puis réessayez.")


class AutocompletePageChanged(AutocompleteTestError):
    def __init__(self) -> None:
        super().__init__("La page a changé pendant le test; aucune autre action n'a été effectuée.")


class AutocompleteSuggestionStale(AutocompleteTestError):
    def __init__(self) -> None:
        super().__init__("Les suggestions ont changé; relancez le test avant de sélectionner.")


class AutocompleteSuggestionUnsafe(AutocompleteTestError):
    def __init__(self) -> None:
        super().__init__("La suggestion ne peut pas être sélectionnée sans risque par le contrôle observé.")


class AutocompleteTestExpired(AutocompleteTestError):
    def __init__(self) -> None:
        super().__init__("Le test autocomplete n'est plus actif; relancez-le.")


class AutocompleteTestStatus(str, Enum):
    SUGGESTIONS = "suggestions"
    NO_SUGGESTIONS = "no_suggestions"
    TIMEOUT = "timeout"


@dataclass(frozen=True, slots=True)
class AutocompleteControlInfo:
    field_id: str
    label: str
    suffix: str
    tag_name: str
    element_id: str | None
    class_names: tuple[str, ...]
    role: str | None
    aria_autocomplete: str | None
    aria_haspopup: str | None
    aria_controls: str | None
    aria_owns: str | None
    aria_activedescendant: str | None
    aria_expanded: str | None
    autocomplete_attribute: str | None
    frame_label: str
    enabled: bool
    yui_global_available: bool


@dataclass(frozen=True, slots=True)
class AutocompleteContainerInfo:
    tag_name: str
    element_id: str | None
    class_names: tuple[str, ...]
    role: str | None
    aria_expanded: str | None
    visible: bool
    relation: str
    selector: str = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class AutocompleteSuggestion:
    index: int
    text: str
    tag_name: str
    class_names: tuple[str, ...]
    role: str | None
    aria_selected: str | None
    data_attribute_names: tuple[str, ...]
    safe_to_select: bool
    selector: str = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class AutocompleteSnapshot:
    control: AutocompleteControlInfo
    containers: tuple[AutocompleteContainerInfo, ...]
    suggestions: tuple[AutocompleteSuggestion, ...]
    page_marker: str
    nearby_controls: tuple[str, ...] = ()

    @property
    def suggestions_visible(self) -> bool:
        return any(container.visible for container in self.containers)


@dataclass(frozen=True, slots=True)
class AutocompleteObservation:
    token: str | None
    field_id: str
    status: AutocompleteTestStatus
    control: AutocompleteControlInfo
    containers: tuple[AutocompleteContainerInfo, ...]
    suggestions: tuple[AutocompleteSuggestion, ...]
    timed_out: bool
    nearby_controls: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AutocompleteSelectionResult:
    field_id: str
    suggestion_text: str
    accepted: bool
    input_matches_suggestion: bool
    suggestions_disappeared: bool
    nearby_controls_before: tuple[str, ...]
    nearby_controls_after: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AutocompleteResetResult:
    field_id: str
    cleared: bool


@dataclass(frozen=True, slots=True)
class ResolvedAutocompleteControl:
    info: AutocompleteControlInfo
    handle: Any = field(repr=False, compare=False)
    frame: Any = field(default=None, repr=False, compare=False)


class AutocompleteDriver(Protocol):
    """Port injectable; aucune méthode ne contacte un endpoint applicatif directement."""

    def locate_control(self, field_id: str, suffix: str) -> ResolvedAutocompleteControl: ...

    def page_marker(self) -> str: ...

    def current_value(self, handle: Any) -> str: ...

    def type_sequentially(self, handle: Any, text: str) -> None: ...

    def inspect(self, control: ResolvedAutocompleteControl) -> AutocompleteSnapshot: ...

    def wait(self, milliseconds: int) -> None: ...

    def click_suggestion(self, control: ResolvedAutocompleteControl, suggestion: AutocompleteSuggestion) -> None: ...

    def clear(self, control: ResolvedAutocompleteControl) -> None: ...


@dataclass(slots=True)
class _PendingTest:
    field_id: str
    control: ResolvedAutocompleteControl
    page_marker: str
    snapshot: AutocompleteSnapshot
    selection_attempted: bool = False


class AutocompleteTester:
    """Cycle à étapes: taper et observer, sélectionner explicitement, puis arrêter."""

    def __init__(self, driver: AutocompleteDriver):
        self._driver = driver
        self._pending: dict[str, _PendingTest] = {}

    @staticmethod
    def _suffix_for(field_id: str) -> str:
        criterion_paths = AUTOCOMPLETE_FIELD_CRITERIA.get(field_id)
        if criterion_paths is None:
            raise AutocompleteFieldNotMapped()
        mappings = [SIDJILCOM_CONTROL_MAP.get(path) for path in criterion_paths]
        if any(
            mapping is None or mapping.component_type is not ComponentType.AUTOCOMPLETE
            for mapping in mappings
        ):
            raise AutocompleteFieldNotMapped()
        suffixes = {mapping.name_suffix for mapping in mappings if mapping is not None}
        if len(suffixes) != 1 or None in suffixes:
            raise AutocompleteFieldNotMapped()
        return next(iter(suffixes))  # type: ignore[return-value]

    def prepare(
        self,
        field_id: str,
        query: str,
        *,
        timeout_ms: int = 8_000,
    ) -> AutocompleteObservation:
        if not isinstance(query, str) or not query.strip() or len(query) > 120:
            raise AutocompleteTestError("Saisissez une valeur de test non vide de 120 caractères maximum.")
        if self._pending:
            raise AutocompleteTestError("Réinitialisez d'abord le test autocomplete déjà ouvert.")
        suffix = self._suffix_for(field_id)
        control = self._driver.locate_control(field_id, suffix)
        if not control.info.enabled:
            raise AutocompleteControlDisabled()
        # Lu uniquement pour protéger puis restaurer le champ ciblé; jamais affiché ni journalisé.
        if self._driver.current_value(control.handle):
            raise AutocompleteFieldNotEmpty()
        page_marker = self._driver.page_marker()
        if not page_marker:
            raise AutocompletePageChanged()
        try:
            self._driver.type_sequentially(control.handle, query.strip())
            deadline = time.monotonic() + max(500, min(timeout_ms, 15_000)) / 1000
            last_snapshot: AutocompleteSnapshot | None = None
            while time.monotonic() < deadline:
                self._ensure_page(page_marker)
                last_snapshot = self._driver.inspect(control)
                visible = last_snapshot.suggestions_visible or bool(last_snapshot.suggestions)
                if visible:
                    if not last_snapshot.suggestions:
                        self._driver.clear(control)
                        return self._observation(
                            None, field_id, AutocompleteTestStatus.NO_SUGGESTIONS,
                            last_snapshot, False,
                        )
                    token = uuid4().hex
                    self._pending[token] = _PendingTest(field_id, control, page_marker, last_snapshot)
                    return self._observation(
                        token, field_id, AutocompleteTestStatus.SUGGESTIONS,
                        last_snapshot, False,
                    )
                self._driver.wait(100)
            self._ensure_page(page_marker)
            if last_snapshot is None:
                last_snapshot = self._driver.inspect(control)
            self._driver.clear(control)
            return self._observation(
                None, field_id, AutocompleteTestStatus.TIMEOUT, last_snapshot, True,
            )
        except AutocompletePageChanged:
            raise
        except AutocompleteTestError:
            if self._driver.page_marker() == page_marker:
                try:
                    self._driver.clear(control)
                except Exception:
                    pass
            raise
        except Exception:
            # Échec de contrôle DOM: best effort de nettoyage uniquement si la page reste identique.
            if self._driver.page_marker() == page_marker:
                try:
                    self._driver.clear(control)
                except Exception:
                    pass
            raise AutocompleteTestError("Échec du test autocomplete; le détail DOM n'a pas été exposé.") from None

    def select(self, token: str, suggestion_index: int) -> AutocompleteSelectionResult:
        pending = self._pending.get(token)
        if pending is None or pending.selection_attempted:
            raise AutocompleteTestExpired()
        self._ensure_page(pending.page_marker)
        if suggestion_index < 0 or suggestion_index >= len(pending.snapshot.suggestions):
            raise AutocompleteSuggestionStale()
        expected = pending.snapshot.suggestions[suggestion_index]
        snapshot = self._driver.inspect(pending.control)
        if suggestion_index >= len(snapshot.suggestions):
            raise AutocompleteSuggestionStale()
        suggestion = snapshot.suggestions[suggestion_index]
        if suggestion.text != expected.text:
            raise AutocompleteSuggestionStale()
        if not suggestion.safe_to_select:
            raise AutocompleteSuggestionUnsafe()
        self._ensure_page(pending.page_marker)
        pending.selection_attempted = True
        try:
            self._driver.click_suggestion(pending.control, suggestion)
            deadline = time.monotonic() + 2.0
            latest = snapshot
            input_matches = False
            popup_disappeared = False
            aria_selected = False
            while time.monotonic() < deadline:
                self._ensure_page(pending.page_marker)
                latest = self._driver.inspect(pending.control)
                input_matches = self._driver.current_value(pending.control.handle).strip() == suggestion.text.strip()
                popup_disappeared = not latest.suggestions_visible
                aria_selected = any(
                    item.text == suggestion.text and (item.aria_selected or "").lower() == "true"
                    for item in latest.suggestions
                )
                if input_matches and (popup_disappeared or aria_selected):
                    break
                self._driver.wait(100)
            accepted = input_matches and (popup_disappeared or aria_selected)
            return AutocompleteSelectionResult(
                field_id=pending.field_id,
                suggestion_text=suggestion.text,
                accepted=accepted,
                input_matches_suggestion=input_matches,
                suggestions_disappeared=popup_disappeared,
                nearby_controls_before=pending.snapshot.nearby_controls,
                nearby_controls_after=latest.nearby_controls,
            )
        except AutocompleteTestError:
            raise
        except Exception:
            raise AutocompleteTestError("La sélection n'a pas pu être confirmée; aucune recherche n'a été lancée.") from None

    def reset(self, token: str) -> AutocompleteResetResult:
        pending = self._pending.get(token)
        if pending is None:
            raise AutocompleteTestExpired()
        self._ensure_page(pending.page_marker)
        try:
            self._driver.clear(pending.control)
        except Exception:
            raise AutocompleteTestError("Le champ de test n'a pas pu être réinitialisé.") from None
        del self._pending[token]
        return AutocompleteResetResult(field_id=pending.field_id, cleared=True)

    def _ensure_page(self, expected_marker: str) -> None:
        if self._driver.page_marker() != expected_marker:
            raise AutocompletePageChanged()

    @staticmethod
    def _observation(
        token: str | None,
        field_id: str,
        status: AutocompleteTestStatus,
        snapshot: AutocompleteSnapshot,
        timed_out: bool,
    ) -> AutocompleteObservation:
        return AutocompleteObservation(
            token=token,
            field_id=field_id,
            status=status,
            control=snapshot.control,
            containers=snapshot.containers,
            suggestions=snapshot.suggestions,
            timed_out=timed_out,
            nearby_controls=snapshot.nearby_controls,
        )
