"""Exécution prudente d'une seule recherche Sidjilcom confirmée par l'utilisateur.

Le module orchestre des interactions visibles et injectables. Il ne sait ni collecter
les lignes, ni paginer, ni exporter; le pilote ne renvoie que la structure des résultats.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
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

    def __init__(
        self, message: str, *, code: str = "search_stopped",
        diagnostic: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.diagnostic = diagnostic


class SearchNotConfirmed(SearchExecutionError):
    def __init__(self) -> None:
        super().__init__("La recherche réelle exige une confirmation explicite.", code="not_confirmed")


class SearchCriteriaNotSupported(SearchExecutionError):
    def __init__(self) -> None:
        super().__init__("Un critère n'est pas automatisable selon le mapping et la structure confirmés.", code="unsupported_criterion")


class SearchFormUnsafe(SearchExecutionError):
    def __init__(
        self, message: str = "Le formulaire de recherche est absent, ambigu, prérempli ou a changé; aucune soumission n'a été effectuée.",
        *, code: str = "unsafe_form", diagnostic: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, diagnostic=diagnostic)


class SearchSuggestionMissing(SearchExecutionError):
    def __init__(self, *, diagnostic: dict[str, object] | None = None) -> None:
        super().__init__("La suggestion exacte attendue est absente ou ambiguë; la recherche a été arrêtée.", code="suggestion_missing", diagnostic=diagnostic)


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
    element_id: str | None = None
    label: str = ""
    role: str | None = None
    required: bool = False
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
    form_id: str = ""
    form_name: str = ""
    form_class: str = ""
    form_role: str = ""
    form_method: str = ""
    form_action: str = ""
    parent_portlet: str = ""
    parent_portlet_confirmed: bool = False
    button_id: str = ""
    button_name: str = ""
    button_role: str = ""
    button_type: str = ""
    button_class: str = ""
    required_invalid_controls: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    values_verified: bool = False
    verified_values: tuple[tuple[str, str], ...] = ()
    page_url: str = ""
    page_title: str = ""
    page_section: str = ""


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

    @property
    def has_results_page(self) -> bool:
        """Un tableau identifiable, un compteur explicite ou un message aucun résultat."""
        if self.no_results:
            return True
        if self.errors:
            return False
        return bool(
            (self.result_count is not None and self.result_count >= 0)
            or (self.table_count > 0 and self.row_count > 0 and bool(self.columns))
        )

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

    def read_control_value(self, control: SearchControl) -> str: ...

    def failure_diagnostic(self) -> dict[str, object]: ...

    def fill_date(self, control: SearchControl, value: str) -> None: ...

    def fill_select(self, control: SearchControl, value: str) -> None: ...

    def prepare_autocomplete(self, field_id: str, value: str) -> AutocompleteObservation: ...

    def select_autocomplete(self, token: str, index: int) -> AutocompleteSelectionResult: ...

    def reset_autocomplete(self, token: str) -> None: ...

    def submit_search(
        self, form: SearchFormSnapshot, *, on_attempt: Callable[[], None] | None = None
    ) -> None: ...

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
        self._execution_started = False
        self._phase = "created"
        self._criteria_context: tuple[MappedCriterion, ...] = ()
        self._current_criterion: str | None = None
        self._form_context: SearchFormSnapshot | None = None

    def execute(
        self,
        criteria: SearchCriteria,
        *,
        confirmed: bool = False,
        on_step: Callable[[SearchStep], None] | None = None,
        on_pre_submit: Callable[[dict[str, object]], None] | None = None,
    ) -> SearchObservation:
        if self._execution_started:
            raise SearchExecutionError(
                "Cette tentative a déjà été utilisée; aucune seconde soumission n'est autorisée.",
                code="duplicate_execution",
            )
        self._execution_started = True
        self._phase = "validation"
        self._criteria_context: tuple[MappedCriterion, ...] = ()
        self._current_criterion: str | None = None
        self._form_context: SearchFormSnapshot | None = None
        self._value_checks: dict[str, bool] = {}
        self._active_search_criteria = criteria
        try:
            return self._execute_once(
                criteria, confirmed=confirmed, on_step=on_step, on_pre_submit=on_pre_submit
            )
        except SearchExecutionError as exc:
            safe_context = self._failure_diagnostic(criteria, exc)
            if exc.diagnostic is not None:
                safe_context.update(exc.diagnostic)
            safe_context.update({
                "kind": "sidjilcom_controlled_search_failure",
                "stage": self._phase,
                "mode": criteria.mode.value,
                "reason_code": exc.code,
                "current_field": self._current_criterion or "",
            })
            exc.diagnostic = safe_context
            raise
        except Exception:
            error = SearchExecutionError(
                "Le remplissage ou le contrôle du formulaire a échoué; aucune nouvelle tentative n'a été faite.",
                code="form_interaction_failed" if self._phase != "submitting" else "submit_uncertain",
                diagnostic=self._failure_diagnostic(
                    criteria,
                    SearchExecutionError("Échec du contrôle de formulaire.", code="form_interaction_failed"),
                ),
            )
            raise error from None

    def _execute_once(
        self,
        criteria: SearchCriteria,
        *,
        confirmed: bool,
        on_step: Callable[[SearchStep], None] | None,
        on_pre_submit: Callable[[dict[str, object]], None] | None,
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
        self._criteria_context = mapped

        self._phase = "mode_selection"
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

        self._phase = "form_inspection"
        self._emit(on_step, SearchStep.FORM_VALIDATION)
        try:
            form = self._driver.inspect_form(criteria.mode, mapped)
        except SearchExecutionError:
            raise
        except Exception:
            raise SearchFormUnsafe() from None
        self._form_context = form
        if (
            form.mode is not criteria.mode
            or form.page_marker != marker
            or not form.all_visible_controls_empty
            or form.search_button_count != 1
            or not form.search_button_enabled
            or not form.parent_portlet_confirmed
            or form.form_method.casefold() != "post"
            or form.blockers
        ):
            raise SearchFormUnsafe(
                "Le formulaire Commerçant attendu ou l'un de ses contrôles n'a pas passé le précontrôle; aucun clic effectué.",
                diagnostic=self._failure_diagnostic(criteria, SearchFormUnsafe()),
            )
        controls_by_criterion = {control.criterion: control for control in form.controls}
        if len(controls_by_criterion) != len(form.controls):
            raise SearchFormUnsafe(diagnostic=self._failure_diagnostic(criteria, SearchFormUnsafe()))
        try:
            names = tuple(control.name for control in form.controls)
            for item in mapped:
                self._current_criterion = item.criterion
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
                    raise SearchFormUnsafe(
                        f"Le champ {item.label} est absent, ambigu, prérempli ou désactivé; aucun clic effectué.",
                        diagnostic=self._failure_diagnostic(criteria, SearchFormUnsafe()),
                    )
                self._validate_control_type(mapping.component_type, mapping, control, item.value)
        except SearchExecutionError:
            raise
        except Exception:
            raise SearchFormUnsafe(diagnostic=self._failure_diagnostic(criteria, SearchFormUnsafe())) from None

        self._phase = "filling_criteria"
        self._emit(on_step, SearchStep.FILLING_CRITERIA)
        expected_dom_values: dict[str, str] = {}
        for item in mapped:
            self._current_criterion = item.criterion
            if self._driver.page_marker() != marker:
                raise SearchNavigationUnexpected()
            control = controls_by_criterion[item.criterion]
            if item.component_type is ComponentType.TEXT:
                self._driver.fill_text(control, item.value)
                expected_dom_values[item.criterion] = item.value
            elif item.component_type is ComponentType.AUTOCOMPLETE:
                expected_dom_values[item.criterion] = self._fill_autocomplete(item)
            elif item.component_type is ComponentType.DATE:
                self._driver.fill_date(control, item.value)
                expected_dom_values[item.criterion] = item.value
            elif item.component_type is ComponentType.SELECT:
                self._driver.fill_select(control, item.value)
                expected_dom_values[item.criterion] = item.value
            else:
                raise SearchCriteriaNotSupported()

        if self._driver.page_marker() != marker:
            raise SearchNavigationUnexpected()
        # Re-résout le DOM après les autocomplétions : la vérification porte sur
        # les valeurs réellement présentes, pas sur les chaînes envoyées au clavier.
        self._phase = "post_fill_verification"
        form = self._driver.inspect_form(criteria.mode, mapped)
        self._form_context = form
        controls_by_criterion = {control.criterion: control for control in form.controls}
        value_checks: dict[str, bool] = {}
        for item in mapped:
            self._current_criterion = item.criterion
            control = controls_by_criterion.get(item.criterion)
            if control is None or not control.visible or not control.enabled:
                value_checks[item.criterion] = False
                raise SearchFormUnsafe(
                    f"Le champ {item.label} n'est plus présent ou activé après le remplissage.",
                    code="post_fill_control_missing",
                    diagnostic=self._failure_diagnostic(criteria, SearchFormUnsafe()),
                )
            actual = self._driver.read_control_value(control)
            expected_widget_value = expected_dom_values[item.criterion]
            matches_widget = actual.strip() == expected_widget_value.strip()
            matches_criterion = (
                self._suggestion_matches(item.value, actual)
                if item.component_type is ComponentType.AUTOCOMPLETE
                else actual.strip() == item.value.strip()
            )
            value_checks[item.criterion] = matches_widget and matches_criterion
            self._value_checks[item.criterion] = value_checks[item.criterion]
            if not value_checks[item.criterion]:
                raise SearchFormUnsafe(
                    f"La valeur présente dans le champ {item.label} ne correspond pas au critère confirmé.",
                    code="filled_value_mismatch",
                    diagnostic=self._failure_diagnostic(criteria, SearchFormUnsafe()),
                )
        if (
            form.mode is not criteria.mode
            or form.page_marker != marker
            or form.search_button_count != 1
            or not form.search_button_enabled
            or not form.parent_portlet_confirmed
            or form.form_method.casefold() != "post"
        ):
            raise SearchFormUnsafe(
                "Le formulaire ou le bouton Rechercher a changé après le remplissage.",
                code="post_fill_form_changed",
                diagnostic=self._failure_diagnostic(criteria, SearchFormUnsafe()),
            )
        if form.required_invalid_controls:
            raise SearchFormUnsafe(
                "Un ou plusieurs champs obligatoires du formulaire Commerçant sont absents ou invalides.",
                code="required_field_invalid",
                diagnostic=self._failure_diagnostic(criteria, SearchFormUnsafe()),
            )

        verified_values = tuple(
            (controls_by_criterion[item.criterion].name, expected_dom_values[item.criterion])
            for item in mapped
        )
        form = replace(form, values_verified=True, verified_values=verified_values)
        self._form_context = form
        if on_pre_submit is not None:
            try:
                on_pre_submit(self._pre_submit_diagnostic(criteria.mode, form, value_checks))
            except Exception:
                raise SearchExecutionError(
                    "Le diagnostic préalable n'a pas pu être enregistré; la recherche n'a pas été soumise.",
                    code="pre_submit_diagnostic_failed",
                    diagnostic=self._failure_diagnostic(
                        criteria, SearchExecutionError("Diagnostic préalable non enregistré.", code="pre_submit_diagnostic_failed")
                    ),
                ) from None
        self._phase = "preparing_submission"
        try:
            # Le pilote finit tous les contrôles DOM avant d'émettre SUBMITTING;
            # le callback est exécuté immédiatement avant son unique appel click().
            self._driver.submit_search(
                form,
                on_attempt=lambda: self._mark_submitting(on_step),
            )
            self.submission_count += 1
            self._emit(on_step, SearchStep.SUBMITTED)
        except SearchExecutionError:
            raise
        except Exception:
            raise SearchExecutionError("Le bouton Rechercher n'a pas pu être activé.", code="submit_error") from None

        self._phase = "observing_results"
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
        if not result.has_results_page:
            raise SearchResultsObservationError()
        self._phase = "results_detected"
        self._emit(
            on_step,
            SearchStep.NO_RESULTS if result.no_results or result.result_count == 0
            else SearchStep.RESULTS_DETECTED,
        )
        return result

    def _mark_submitting(self, on_step: Callable[[SearchStep], None] | None) -> None:
        self._emit(on_step, SearchStep.SUBMITTING)
        self._phase = "submitting"

    def _failure_diagnostic(self, criteria: SearchCriteria, error: SearchExecutionError) -> dict[str, object]:
        driver_diagnostic: dict[str, object] = {}
        get_diagnostic = getattr(self._driver, "failure_diagnostic", None)
        if callable(get_diagnostic):
            try:
                observed = get_diagnostic()
                if isinstance(observed, dict):
                    driver_diagnostic = dict(observed)
            except Exception:
                driver_diagnostic = {}
        fields = [
            {
                "criterion": item.criterion,
                "suffix": SIDJILCOM_CONTROL_MAP[item.criterion].name_suffix or "",
                "label": item.label,
            }
            for item in self._criteria_context
        ]
        diagnostic_fields = driver_diagnostic.get("fields")
        if isinstance(diagnostic_fields, list):
            for item in diagnostic_fields:
                if isinstance(item, dict) and isinstance(item.get("criterion"), str):
                    criterion = item["criterion"]
                    if criterion in self._value_checks:
                        item["value_matches_criterion"] = self._value_checks[criterion]
        diagnostic_forms = driver_diagnostic.get("forms")
        if isinstance(diagnostic_forms, list):
            for candidate in diagnostic_forms:
                if not isinstance(candidate, dict) or not isinstance(candidate.get("fields"), list):
                    continue
                for item in candidate["fields"]:
                    if isinstance(item, dict) and isinstance(item.get("criterion"), str):
                        criterion = item["criterion"]
                        if criterion in self._value_checks:
                            item["value_matches_criterion"] = self._value_checks[criterion]
        driver_diagnostic.update({
            "kind": "sidjilcom_controlled_search_failure",
            "stage": self._phase,
            "mode": criteria.mode.value,
            "expected_fields": fields,
            "current_field": self._current_criterion or "",
            "reason_code": error.code,
        })
        return driver_diagnostic

    @staticmethod
    def _pre_submit_diagnostic(
        mode: SearchMode, form: SearchFormSnapshot, value_checks: dict[str, bool]
    ) -> dict[str, object]:
        """Métadonnées structurelles et conformité booléenne; aucune valeur DOM n'est conservée."""
        return {
            "kind": "sidjilcom_pre_submit_diagnostic",
            "mode": mode.value,
            "page_marker": form.page_marker,
            "form": {
                "id": form.form_id,
                "name": form.form_name,
                "class": form.form_class,
                "role": form.form_role,
                "method": form.form_method,
                "action": form.form_action,
                "parent_portlet": form.parent_portlet,
                "parent_portlet_confirmed": form.parent_portlet_confirmed,
            },
            "page": {"url": form.page_url, "title": form.page_title, "section": form.page_section},
            "criteria": [control.criterion for control in form.controls],
            "controls": [
                {
                    "criterion": control.criterion,
                    "suffix": SIDJILCOM_CONTROL_MAP[control.criterion].name_suffix or "",
                    "name": control.name,
                    "id": control.element_id or "",
                    "label": control.label,
                    "role": control.role or "",
                    "tag": control.tag_name,
                    "type": control.input_type,
                    "classes": list(control.class_names),
                    "visible": control.visible,
                    "enabled": control.enabled,
                    "required": control.required,
                    "empty_before_fill": True,
                    "value_matches_criterion": bool(value_checks.get(control.criterion, False)),
                }
                for control in form.controls
            ],
            "all_visible_controls_empty_before_fill": True,
            "search_button_count": form.search_button_count,
            "search_button_enabled": form.search_button_enabled,
            "button": {
                "label": "Rechercher",
                "id": form.button_id,
                "name": form.button_name,
                "role": form.button_role,
                "type": form.button_type,
                "class": form.button_class,
                "visible": form.search_button_count == 1,
                "enabled": form.search_button_enabled,
                "candidate_count": form.search_button_count,
                "associated": form.handle is not None,
            },
            "required_invalid_controls": list(form.required_invalid_controls),
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

    def _fill_autocomplete(self, item: MappedCriterion) -> str:
        observation = self._driver.prepare_autocomplete(_autocomplete_field_id(item.criterion), item.value)
        exact = [
            option for option in observation.suggestions
            if self._suggestion_matches(item.value, option.text)
        ]
        if (
            observation.status is not AutocompleteTestStatus.SUGGESTIONS
            or observation.token is None
            or len(exact) != 1
            or not exact[0].safe_to_select
        ):
            if observation.token:
                self._driver.reset_autocomplete(observation.token)
            diagnostic = self._failure_diagnostic(
                self._current_criteria_model(),
                SearchSuggestionMissing(),
            )
            diagnostic["autocomplete"] = {
                "field": item.criterion,
                "suffix": item.name_suffix or "",
                "control": {
                    "tag": observation.control.tag_name,
                    "id": observation.control.element_id or "",
                    "role": observation.control.role or "",
                    "type": "text",
                    "classes": list(observation.control.class_names),
                    "visible": True,
                    "enabled": observation.control.enabled,
                },
                "suggestion_count": len(observation.suggestions),
                "exact_code_match_count": len(exact),
                "selection_required": True,
            }
            raise SearchSuggestionMissing(diagnostic=diagnostic)
        selected = self._driver.select_autocomplete(observation.token, exact[0].index)
        if (
            not selected.accepted
            or not selected.input_matches_suggestion
            or not self._suggestion_matches(item.value, selected.suggestion_text)
        ):
            diagnostic = self._failure_diagnostic(
                self._current_criteria_model(), SearchSuggestionMissing()
            )
            diagnostic["autocomplete"] = {
                "field": item.criterion,
                "suffix": item.name_suffix or "",
                "suggestion_count": len(observation.suggestions),
                "exact_code_match_count": len(exact),
                "selected_value_matches": False,
                "selection_required": True,
            }
            raise SearchSuggestionMissing(diagnostic=diagnostic)
        return selected.suggestion_text

    def _current_criteria_model(self) -> SearchCriteria:
        # Le contexte public existe déjà dans _execute_once; cette sentinelle sert
        # uniquement au diagnostic précoce et n'expose aucune valeur.
        return getattr(self, "_active_search_criteria", SearchCriteria(mode=SearchMode.PERSONNE_MORALE))

    @staticmethod
    def _suggestion_matches(expected: str, observed: str) -> bool:
        """Correspondance exacte ou code exact suivi d'un séparateur d'intitulé officiel."""
        target = _normalized_suggestion(expected)
        candidate = _normalized_suggestion(observed)
        if candidate == target:
            return True
        if not target.isdigit() or not candidate.startswith(target) or len(candidate) <= len(target):
            return False
        tail = candidate[len(target):].strip()
        # N'accepte ni préfixe numérique voisin ni libellé libre; le code doit être
        # suivi d'un séparateur de libellé explicite (ex. « 34000 : Wilaya »).
        return bool(tail and tail[0] in ":-|–—([" and tail[1:].strip())


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
