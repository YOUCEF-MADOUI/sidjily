"""Exécution prudente d'une seule recherche Sidjilcom confirmée par l'utilisateur.

Le module orchestre des interactions visibles et injectables. Il ne sait ni collecter
les lignes, ni paginer, ni exporter; le pilote ne renvoie que la structure des résultats.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
import unicodedata
from typing import Any, Callable, Protocol

from sidjily.sidjilcom.autocomplete import (
    AutocompleteObservation,
    AutocompleteSelectionResult,
    AutocompleteTestStatus,
)
from sidjily.sidjilcom.criteria import (
    ComponentType,
    CriteriaValidationError,
    MappedCriterion,
    SearchCriteria,
    SearchMode,
    SIDJILCOM_CONTROL_MAP,
    map_criteria_to_controls,
    resolve_control_name,
    validate_search_criteria,
)


class SearchStep(str, Enum):
    PREPARED = "prepared"
    MODE_SELECTION = "mode_selection"
    FORM_VALIDATION = "form_validation"
    FILLING_CRITERIA = "filling_criteria"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    OBSERVING_RESULTS = "observing_results"
    RESULTS_DETECTED = "results_detected"
    NO_RESULTS = "no_results"
    COMPLETED = "completed"
    FAILED = "failed"


class SearchExecutionError(RuntimeError):
    """Erreur à message fixe; les valeurs des critères et détails de page sont omis."""

    def __init__(self, message: str, *, code: str = "search_stopped") -> None:
        super().__init__(message)
        self.code = code


class SearchNotConfirmed(SearchExecutionError):
    def __init__(self) -> None:
        super().__init__("La recherche réelle exige une confirmation explicite.", code="not_confirmed")


class SearchCriteriaNotSupported(SearchExecutionError):
    def __init__(self) -> None:
        super().__init__("Un critère n'est pas automatisable selon le mapping et la structure confirmés.", code="unsupported_criterion")


class SearchFormUnsafe(SearchExecutionError):
    def __init__(self) -> None:
        super().__init__("Le formulaire de recherche est absent, ambigu, prérempli ou a changé; aucune soumission n'a été effectuée.", code="unsafe_form")


class SearchSuggestionMissing(SearchExecutionError):
    def __init__(self) -> None:
        super().__init__("La suggestion exacte attendue est absente ou ambiguë; la recherche a été arrêtée.", code="suggestion_missing")


class SearchSessionExpired(SearchExecutionError):
    def __init__(self) -> None:
        super().__init__("La session Sidjilcom a expiré; aucune nouvelle action n'a été tentée.", code="session_expired")


class SearchNavigationUnexpected(SearchExecutionError):
    def __init__(self) -> None:
        super().__init__("La page a changé de façon inattendue; aucune nouvelle action n'a été tentée.", code="navigation_unexpected")


class SearchResultsObservationError(SearchExecutionError):
    def __init__(self) -> None:
        super().__init__("La structure des résultats n'a pas pu être observée sans risque.", code="results_observation_error")


@dataclass(frozen=True, slots=True)
class SearchControl:
    criterion: str
    name: str
    tag_name: str
    input_type: str
    enabled: bool
    visible: bool
    empty: bool
    class_names: tuple[str, ...] = ()
    aria_autocomplete: str | None = None
    option_labels: tuple[str, ...] = ()
    handle: Any = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class SearchFormSnapshot:
    mode: SearchMode
    page_marker: str
    controls: tuple[SearchControl, ...]
    all_visible_controls_empty: bool
    search_button_count: int
    search_button_enabled: bool
    handle: Any = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class SearchObservation:
    title: str
    sanitized_url: str
    result_count: int | None
    table_count: int
    row_count: int
    columns: tuple[str, ...]
    pagination_visible: bool
    no_results: bool
    errors: tuple[str, ...]
    session_expired: bool
    result_zone_found: bool = False
    result_zone_tag: str = ""

    def to_mapping(self) -> dict[str, object]:
        return {
            "title": self.title,
            "url": self.sanitized_url,
            "result_count": self.result_count,
            "table_count": self.table_count,
            "row_count": self.row_count,
            "columns": list(self.columns),
            "pagination_visible": self.pagination_visible,
            "no_results": self.no_results,
            "errors": list(self.errors),
            "session_expired": self.session_expired,
            "result_zone_found": self.result_zone_found,
            "result_zone_tag": self.result_zone_tag,
            "details_opened": False,
            "automatic_pagination": False,
            "company_rows_collected": False,
        }


class SearchDriver(Protocol):
    """Port de navigateur; toute interaction réelle reste dans l'interface normale."""

    def select_mode(self, mode: SearchMode) -> None: ...

    def page_marker(self) -> str: ...

    def inspect_form(self, mode: SearchMode, criteria: tuple[MappedCriterion, ...]) -> SearchFormSnapshot: ...

    def fill_text(self, control: SearchControl, value: str) -> None: ...

    def fill_date(self, control: SearchControl, value: str) -> None: ...

    def fill_select(self, control: SearchControl, value: str) -> None: ...

    def prepare_autocomplete(self, field_id: str, value: str) -> AutocompleteObservation: ...

    def select_autocomplete(self, token: str, index: int) -> AutocompleteSelectionResult: ...

    def reset_autocomplete(self, token: str) -> None: ...

    def submit_search(self, form: SearchFormSnapshot) -> None: ...

    def observe_results(self) -> SearchObservation: ...


def _normalized_suggestion(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    plain = "".join(character for character in normalized if not unicodedata.combining(character))
    return re.sub(r"\s+", " ", plain).strip()


def _autocomplete_field_id(criterion: str) -> str:
    if criterion == "commune_wilaya":
        return "commune_wilaya"
    if criterion == "activite":
        return "activite"
    if criterion in {"physique.nationalite", "morale.nationalite"}:
        return "nationalite"
    raise SearchCriteriaNotSupported()


class SearchExecutor:
    """Remplit un unique formulaire, sélectionne les suggestions exactes, soumet une fois."""

    def __init__(self, driver: SearchDriver):
        self._driver = driver
        self.submission_count = 0

    def execute(
        self,
        criteria: SearchCriteria,
        *,
        confirmed: bool = False,
        on_step: Callable[[SearchStep], None] | None = None,
        on_pre_submit: Callable[[dict[str, object]], None] | None = None,
    ) -> SearchObservation:
        if confirmed is not True:
            raise SearchNotConfirmed()
        try:
            validate_search_criteria(criteria)
            mapped = map_criteria_to_controls(criteria)
        except (CriteriaValidationError, AttributeError, TypeError):
            raise SearchCriteriaNotSupported() from None
        if not mapped:
            raise SearchCriteriaNotSupported()
        self._emit(on_step, SearchStep.MODE_SELECTION)
        try:
            self._driver.select_mode(criteria.mode)
        except SearchExecutionError:
            raise
        except Exception:
            raise SearchNavigationUnexpected() from None
        marker = self._driver.page_marker()
        if not marker:
            raise SearchNavigationUnexpected()

        self._emit(on_step, SearchStep.FORM_VALIDATION)
        try:
            form = self._driver.inspect_form(criteria.mode, mapped)
        except SearchExecutionError:
            raise
        except Exception:
            raise SearchFormUnsafe() from None
        if (
            form.mode is not criteria.mode
            or form.page_marker != marker
            or not form.all_visible_controls_empty
            or form.search_button_count != 1
            or not form.search_button_enabled
        ):
            raise SearchFormUnsafe()
        controls_by_criterion = {control.criterion: control for control in form.controls}
        if len(controls_by_criterion) != len(form.controls):
            raise SearchFormUnsafe()
        try:
            names = tuple(control.name for control in form.controls)
            for item in mapped:
                resolved_name = resolve_control_name(criteria.mode, item.criterion, names)
                control = controls_by_criterion[item.criterion]
                mapping = SIDJILCOM_CONTROL_MAP[item.criterion]
                if (
                    control.name != resolved_name
                    or not control.visible
                    or not control.enabled
                    or not control.empty
                    or mapping.component_type is not item.component_type
                    or not mapping.input_semantics_confirmed
                ):
                    raise SearchFormUnsafe()
                self._validate_control_type(mapping.component_type, mapping, control, item.value)
        except SearchExecutionError:
            raise
        except Exception:
            raise SearchFormUnsafe() from None

        self._emit(on_step, SearchStep.FILLING_CRITERIA)
        for item in mapped:
            if self._driver.page_marker() != marker:
                raise SearchNavigationUnexpected()
            control = controls_by_criterion[item.criterion]
            mapping = SIDJILCOM_CONTROL_MAP[item.criterion]
            if item.component_type is ComponentType.TEXT:
                self._driver.fill_text(control, item.value)
            elif item.component_type is ComponentType.AUTOCOMPLETE:
                self._fill_autocomplete(item)
            elif item.component_type is ComponentType.DATE:
                self._driver.fill_date(control, item.value)
            elif item.component_type is ComponentType.SELECT:
                self._driver.fill_select(control, item.value)
            else:
                raise SearchCriteriaNotSupported()

        if self._driver.page_marker() != marker:
            raise SearchNavigationUnexpected()
        if on_pre_submit is not None:
            try:
                on_pre_submit(self._pre_submit_diagnostic(criteria.mode, form))
            except Exception:
                raise SearchExecutionError(
                    "Le diagnostic préalable n'a pas pu être enregistré; la recherche n'a pas été soumise.",
                    code="pre_submit_diagnostic_failed",
                ) from None
        self._emit(on_step, SearchStep.SUBMITTING)
        try:
            # C'est l'unique point de soumission; aucun retry n'existe dans l'orchestrateur.
            self._driver.submit_search(form)
            self.submission_count += 1
            self._emit(on_step, SearchStep.SUBMITTED)
        except SearchExecutionError:
            raise
        except Exception:
            raise SearchExecutionError("Le bouton Rechercher n'a pas pu être activé.", code="submit_error") from None

        self._emit(on_step, SearchStep.OBSERVING_RESULTS)
        try:
            result = self._driver.observe_results()
        except SearchExecutionError:
            raise
        except Exception:
            raise SearchResultsObservationError() from None
        if result.session_expired:
            raise SearchSessionExpired()
        if not result.sanitized_url.startswith("https://") or not result.sanitized_url:
            raise SearchNavigationUnexpected()
        self._emit(
            on_step,
            SearchStep.NO_RESULTS if result.no_results or result.result_count == 0
            else SearchStep.RESULTS_DETECTED,
        )
        return result

    @staticmethod
    def _pre_submit_diagnostic(mode: SearchMode, form: SearchFormSnapshot) -> dict[str, object]:
        """Métadonnées non sensibles uniquement; aucun champ, URL, cookie ou token."""
        return {
            "kind": "sidjilcom_pre_submit_diagnostic",
            "mode": mode.value,
            "criteria": [control.criterion for control in form.controls],
            "controls": [
                {
                    "criterion": control.criterion,
                    "tag": control.tag_name,
                    "type": control.input_type,
                    "visible": control.visible,
                    "enabled": control.enabled,
                    "empty_before_fill": control.empty,
                }
                for control in form.controls
            ],
            "all_visible_controls_empty_before_fill": form.all_visible_controls_empty,
            "search_button_count": form.search_button_count,
            "search_button_enabled": form.search_button_enabled,
            "sensitive_values_saved": False,
            "cookies_saved": False,
            "tokens_saved": False,
            "session_identifiers_saved": False,
        }

    @staticmethod
    def _emit(callback: Callable[[SearchStep], None] | None, step: SearchStep) -> None:
        if callback is not None:
            callback(step)

    @staticmethod
    def _validate_control_type(
        component_type: ComponentType,
        mapping: Any,
        control: SearchControl,
        value: str,
    ) -> None:
        if component_type is ComponentType.TEXT:
            if control.tag_name != "input" or control.input_type != "text":
                raise SearchCriteriaNotSupported()
        elif component_type is ComponentType.AUTOCOMPLETE:
            if (
                control.tag_name != "input"
                or control.input_type != "text"
                or (mapping.css_class and mapping.css_class not in control.class_names)
                or (mapping.aria_autocomplete and control.aria_autocomplete != mapping.aria_autocomplete)
            ):
                raise SearchCriteriaNotSupported()
        elif component_type is ComponentType.DATE:
            # Le type est accepté uniquement s'il est observé sur le contrôle réel.
            if control.tag_name != "input" or control.input_type != "date":
                raise SearchCriteriaNotSupported()
        elif component_type is ComponentType.SELECT:
            if (
                control.tag_name != "select"
                or not mapping.options
                or value not in mapping.options
                or value not in control.option_labels
            ):
                raise SearchCriteriaNotSupported()
        else:
            raise SearchCriteriaNotSupported()

    def _fill_autocomplete(self, item: MappedCriterion) -> None:
        observation = self._driver.prepare_autocomplete(_autocomplete_field_id(item.criterion), item.value)
        exact = [
            option for option in observation.suggestions
            if _normalized_suggestion(option.text) == _normalized_suggestion(item.value)
        ]
        if (
            observation.status is not AutocompleteTestStatus.SUGGESTIONS
            or observation.token is None
            or len(exact) != 1
            or not exact[0].safe_to_select
        ):
            if observation.token:
                self._driver.reset_autocomplete(observation.token)
            raise SearchSuggestionMissing()
        selected = self._driver.select_autocomplete(observation.token, exact[0].index)
        if not selected.accepted or not selected.input_matches_suggestion:
            raise SearchSuggestionMissing()


def validate_first_controlled_search(criteria: SearchCriteria) -> None:
    """Autorise uniquement le test initial personne morale : activité 442102 + commune/wilaya 34000."""
    try:
        validate_search_criteria(criteria)
        mapped = map_criteria_to_controls(criteria)
    except (CriteriaValidationError, AttributeError, TypeError):
        raise SearchCriteriaNotSupported() from None
    expected = {"activite": "442102", "commune_wilaya": "34000"}
    actual = {item.criterion: item.value for item in mapped}
    if (
        criteria.mode is not SearchMode.PERSONNE_MORALE
        or len(mapped) != len(expected)
        or actual != expected
    ):
        raise SearchExecutionError(
            "Le premier test contrôlé exige Personne morale, Activité = 442102 et Commune/Wilaya = 34000 uniquement.",
            code="outside_first_search_scope",
        )
