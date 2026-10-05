from __future__ import annotations

import unittest

from sidjily.sidjilcom.autocomplete import (
    AutocompleteControlInfo,
    AutocompleteObservation,
    AutocompleteSelectionResult,
    AutocompleteSuggestion,
    AutocompleteTestStatus,
)
from sidjily.sidjilcom.criteria import (
    ComponentType,
    CriteriaValidationError,
    MappedCriterion,
    SearchCriteria,
    SearchMode,
    criteria_from_mapping,
    map_criteria_to_controls,
)
from sidjily.sidjilcom.search import (
    SearchControl,
    SearchExecutionError,
    SearchExecutor,
    SearchFormSnapshot,
    SearchObservation,
    SearchSessionExpired,
    SearchResultsObservationError,
    SearchSuggestionMissing,
    SearchStep,
    validate_first_controlled_search,
)


class FakeSearchDriver:
    def __init__(self) -> None:
        self.marker = "https://sidjilcom.cnrc.dz/fr/group/sidjilcom/repertoire-des-commercants"
        self.mode: SearchMode | None = None
        self.controls: tuple[SearchControl, ...] = ()
        self.fill_calls: list[tuple[str, str]] = []
        self.selected_mode: list[SearchMode] = []
        self.submissions = 0
        self.steps: list[SearchStep] = []
        self.autocomplete_suggestions: tuple[str, ...] | None = None
        self.autocomplete_suggestions_by_field: dict[str, tuple[str, ...]] = {}
        self.autocomplete_status = AutocompleteTestStatus.SUGGESTIONS
        self.autocomplete_token = "token"
        self.autocomplete_selected: list[int] = []
        self.autocomplete_resets: list[str] = []
        self._last_suggestions: tuple[str, ...] = ()
        self._last_autocomplete_field = ""
        self.filled_values: dict[str, str] = {}
        self.selection_accepted = True
        self.marker_change_after_fill = False
        self.missing_control: str | None = None
        self.ambiguous_form = False
        self.all_empty = True
        self.required_invalid_controls: tuple[str, ...] = ()
        self.parent_portlet_confirmed = True
        self.form_method = "POST"
        self.button_count = 1
        self.final_value_overrides: dict[str, str] = {}
        self.button_enabled = True
        self.observation_error = False
        self.result = SearchObservation(
            title="Résultats de recherche",
            sanitized_url=self.marker,
            result_count=17,
            table_count=1,
            row_count=17,
            columns=("Dénomination", "Wilaya", "État"),
            pagination_visible=True,
            no_results=False,
            errors=(),
            session_expired=False,
        )

    def select_mode(self, mode: SearchMode) -> None:
        self.mode = mode
        self.selected_mode.append(mode)

    def page_marker(self) -> str:
        return self.marker

    def inspect_form(self, mode: SearchMode, criteria: tuple[MappedCriterion, ...]) -> SearchFormSnapshot:
        if self.ambiguous_form:
            raise SearchExecutionError("ambiguous form")
        self.controls = tuple(
            SearchControl(
                criterion=item.criterion,
                name=f"form{index}{item.criterion.split('.')[-1]}{self._suffix(item)}",
                tag_name="input" if item.component_type is not ComponentType.SELECT else "select",
                input_type=self._input_type(item),
                enabled=True,
                visible=True,
                empty=True,
                class_names=("yui3-aclist-input",) if item.component_type is ComponentType.AUTOCOMPLETE else (),
                aria_autocomplete="list" if item.component_type is ComponentType.AUTOCOMPLETE else None,
                option_labels=(),
                handle=item.criterion,
            )
            for index, item in enumerate(criteria)
            if item.criterion != self.missing_control
        )
        return SearchFormSnapshot(
            mode=mode,
            page_marker=self.marker,
            controls=self.controls,
            all_visible_controls_empty=self.all_empty,
            search_button_count=self.button_count,
            search_button_enabled=self.button_enabled,
            handle="form",
            form_id="legal-search",
            form_class="recherche-commercant",
            form_role="search",
            form_method=self.form_method,
            form_action="https://sidjilcom.cnrc.dz/fr/group/sidjilcom/repertoire-des-commercants",
            parent_portlet="dz_cnrc_sidjilcom_recherchedetaillee_portlet_RechercheDetailleePortlet",
            parent_portlet_confirmed=self.parent_portlet_confirmed,
            required_invalid_controls=self.required_invalid_controls,
        )

    @staticmethod
    def _suffix(item: MappedCriterion) -> str:
        from sidjily.sidjilcom.criteria import SIDJILCOM_CONTROL_MAP
        return SIDJILCOM_CONTROL_MAP[item.criterion].name_suffix or ""

    @staticmethod
    def _input_type(item: MappedCriterion) -> str:
        if item.component_type is ComponentType.DATE:
            return "date"
        if item.component_type is ComponentType.SELECT:
            return "select-one"
        return "text"

    def fill_text(self, control: SearchControl, value: str) -> None:
        self.fill_calls.append((control.criterion, value))
        self.filled_values[control.criterion] = value
        if self.marker_change_after_fill:
            self.marker += "/unexpected"

    def read_control_value(self, control: SearchControl) -> str:
        return self.final_value_overrides.get(
            control.criterion, self.filled_values.get(control.criterion, "")
        )

    def failure_diagnostic(self) -> dict[str, object]:
        return {"page": {"url": self.marker, "section": "Trouver une entreprise", "title": "Recherche Commerçant"}}

    def fill_date(self, control: SearchControl, value: str) -> None:
        self.fill_calls.append((control.criterion, value))
        self.filled_values[control.criterion] = value

    def fill_select(self, control: SearchControl, value: str) -> None:
        self.fill_calls.append((control.criterion, value))
        self.filled_values[control.criterion] = value

    def prepare_autocomplete(self, field_id: str, value: str) -> AutocompleteObservation:
        self.fill_calls.append((field_id, value))
        observed_suggestions = self.autocomplete_suggestions_by_field.get(
            field_id, self.autocomplete_suggestions or (value,)
        )
        self._last_suggestions = observed_suggestions
        self._last_autocomplete_field = "commune_wilaya" if field_id == "commune_wilaya" else field_id
        suggestions = tuple(self._suggestion(index, text) for index, text in enumerate(observed_suggestions))
        control = AutocompleteControlInfo(
            field_id=field_id,
            label=field_id,
            suffix="_wilcom",
            tag_name="input",
            element_id="wilaya",
            class_names=("yui3-aclist-input",),
            role=None,
            aria_autocomplete="list",
            aria_haspopup=None,
            aria_controls=None,
            aria_owns=None,
            aria_activedescendant=None,
            aria_expanded="true",
            autocomplete_attribute=None,
            frame_label="Document principal",
            enabled=True,
            yui_global_available=True,
        )
        return AutocompleteObservation(
            token=self.autocomplete_token,
            field_id=field_id,
            status=self.autocomplete_status,
            control=control,
            containers=(),
            suggestions=suggestions,
            timed_out=False,
        )

    @staticmethod
    def _suggestion(index: int, text: str) -> AutocompleteSuggestion:
        return AutocompleteSuggestion(
            index=index,
            text=text,
            tag_name="li",
            class_names=("yui3-aclist-item",),
            role="option",
            aria_selected="false",
            data_attribute_names=(),
            safe_to_select=True,
            selector=f"ul > li:nth-of-type({index + 1})",
        )

    def select_autocomplete(self, token: str, index: int) -> AutocompleteSelectionResult:
        self.autocomplete_selected.append(index)
        value = self._last_suggestions[index]
        self.filled_values[self._last_autocomplete_field] = value
        return AutocompleteSelectionResult(
            field_id=self._last_autocomplete_field,
            suggestion_text=value,
            accepted=self.selection_accepted,
            input_matches_suggestion=self.selection_accepted,
            suggestions_disappeared=self.selection_accepted,
            nearby_controls_before=(),
            nearby_controls_after=(),
        )

    def reset_autocomplete(self, token: str) -> None:
        self.autocomplete_resets.append(token)

    def submit_search(self, form: SearchFormSnapshot, *, on_attempt=None) -> None:
        if on_attempt is not None:
            on_attempt()
        self.submissions += 1

    def observe_results(self) -> SearchObservation:
        if self.observation_error:
            raise RuntimeError("simulated observation interruption")
        return self.result


class SearchExecutorTests(unittest.TestCase):
    def test_first_real_search_is_exactly_personne_morale_activity_and_joint_commune_wilaya(self) -> None:
        criteria = criteria_from_mapping({
            "mode": "PERSONNE_MORALE",
            "activite": "442102",
            "commune_wilaya": "34000",
        })
        validate_first_controlled_search(criteria)
        driver = FakeSearchDriver()
        executor = SearchExecutor(driver)

        diagnostics: list[dict[str, object]] = []
        result = executor.execute(
            criteria, confirmed=True, on_step=driver.steps.append, on_pre_submit=diagnostics.append
        )

        self.assertEqual(driver.selected_mode, [SearchMode.PERSONNE_MORALE])
        self.assertEqual(driver.autocomplete_selected, [0, 0])
        self.assertEqual(driver.submissions, 1)
        self.assertEqual(executor.submission_count, 1)
        self.assertEqual(result.result_count, 17)
        self.assertEqual(result.columns, ("Dénomination", "Wilaya", "État"))
        self.assertIn(SearchStep.SUBMITTING, driver.steps)
        self.assertIn(SearchStep.SUBMITTED, driver.steps)
        self.assertIn(SearchStep.OBSERVING_RESULTS, driver.steps)
        self.assertIn(SearchStep.RESULTS_DETECTED, driver.steps)
        self.assertEqual(set(diagnostics[0]["criteria"]), {"activite", "commune_wilaya"})
        self.assertFalse(diagnostics[0]["sensitive_values_saved"])
        self.assertFalse(diagnostics[0]["cookies_saved"])
        self.assertFalse(diagnostics[0]["tokens_saved"])

    def test_personne_physique_and_morale_text_modes_use_existing_mapping(self) -> None:
        cases = (
            (
                {"mode": "PERSONNE_PHYSIQUE", "nom": "Test local"},
                SearchMode.PERSONNE_PHYSIQUE,
                "physique.nom",
            ),
            (
                {"mode": "PERSONNE_MORALE", "raison_sociale": "Test local"},
                SearchMode.PERSONNE_MORALE,
                "morale.raison_sociale",
            ),
        )
        for payload, expected_mode, expected_criterion in cases:
            with self.subTest(mode=expected_mode):
                driver = FakeSearchDriver()
                criteria = criteria_from_mapping(payload)
                result = SearchExecutor(driver).execute(criteria, confirmed=True)
                self.assertEqual(driver.selected_mode, [expected_mode])
                self.assertEqual(driver.fill_calls, [(expected_criterion, "Test local")])
                self.assertEqual(driver.submissions, 1)
                self.assertEqual(result.table_count, 1)

    def test_combined_criteria_are_filled_through_mapping_and_autocomplete(self) -> None:
        criteria = criteria_from_mapping({
            "mode": "PERSONNE_MORALE",
            "commune_wilaya": "34000 : BORDJ BOU ARRERIDJ",
            "raison_sociale": "Test local",
        })
        mapped = map_criteria_to_controls(criteria)
        self.assertEqual({item.criterion for item in mapped}, {"commune_wilaya", "morale.raison_sociale"})
        driver = FakeSearchDriver()
        SearchExecutor(driver).execute(criteria, confirmed=True)
        self.assertEqual(driver.autocomplete_selected, [0])
        self.assertIn(("morale.raison_sociale", "Test local"), driver.fill_calls)
        self.assertEqual(driver.submissions, 1)

    def test_dates_are_filled_only_when_live_control_is_input_date(self) -> None:
        criteria = criteria_from_mapping({
            "mode": "PERSONNE_PHYSIQUE",
            "date_inscription_du": "2025-01-02",
        })
        driver = FakeSearchDriver()
        SearchExecutor(driver).execute(criteria, confirmed=True)
        self.assertEqual(driver.fill_calls, [("date_inscription_du", "2025-01-02")])
        self.assertEqual(driver.submissions, 1)

        class TextDateDriver(FakeSearchDriver):
            def inspect_form(self, mode, mapped):
                snapshot = super().inspect_form(mode, mapped)
                controls = tuple(
                    SearchControl(
                        **{
                            field: getattr(control, field)
                            for field in SearchControl.__dataclass_fields__
                            if field not in {"handle", "input_type"}
                        },
                        input_type="text",
                        handle=control.handle,
                    )
                    for control in snapshot.controls
                )
                return SearchFormSnapshot(
                    mode, snapshot.page_marker, controls, True, 1, True, handle="form",
                    form_method="POST", parent_portlet_confirmed=True,
                )

        text_driver = TextDateDriver()
        with self.assertRaises(SearchExecutionError):
            SearchExecutor(text_driver).execute(criteria, confirmed=True)
        self.assertEqual(text_driver.submissions, 0)

    def test_selects_with_unknown_options_and_unconfirmed_radio_like_fields_fail_closed(self) -> None:
        with self.assertRaises(CriteriaValidationError):
            criteria_from_mapping({"mode": "PERSONNE_MORALE", "forme_juridique": "option inconnue"})
        with self.assertRaises(CriteriaValidationError):
            criteria_from_mapping({"mode": "PERSONNE_MORALE", "presume": "oui"})

    def test_confirmation_is_required_before_mode_or_form_interactions(self) -> None:
        criteria = criteria_from_mapping({"mode": "PERSONNE_PHYSIQUE", "nom": "Test"})
        for invalid_confirmation in (False, None, 1, "yes"):
            with self.subTest(confirmed=invalid_confirmation):
                driver = FakeSearchDriver()
                with self.assertRaises(SearchExecutionError):
                    SearchExecutor(driver).execute(criteria, confirmed=invalid_confirmation)
                self.assertEqual(driver.selected_mode, [])
                self.assertEqual(driver.submissions, 0)

    def test_missing_or_non_exact_autocomplete_suggestion_stops_before_submit(self) -> None:
        criteria = criteria_from_mapping({
            "mode": "PERSONNE_MORALE",
            "commune_wilaya": "34000",
        })
        driver = FakeSearchDriver()
        driver.autocomplete_suggestions = ("34001 : AUTRE WILAYA",)
        with self.assertRaises(SearchSuggestionMissing) as caught:
            SearchExecutor(driver).execute(criteria, confirmed=True)
        self.assertEqual(driver.submissions, 0)
        self.assertEqual(driver.autocomplete_resets, ["token"])
        self.assertEqual(caught.exception.diagnostic["autocomplete"]["exact_code_match_count"], 0)
        self.assertEqual(caught.exception.diagnostic["current_field"], "commune_wilaya")

    def test_ambiguous_duplicate_exact_autocomplete_suggestions_stop_without_submit(self) -> None:
        criteria = criteria_from_mapping({
            "mode": "PERSONNE_MORALE", "commune_wilaya": "34000"
        })
        driver = FakeSearchDriver()
        driver.autocomplete_suggestions = ("34000", "34000")
        with self.assertRaises(SearchSuggestionMissing):
            SearchExecutor(driver).execute(criteria, confirmed=True)
        self.assertEqual(driver.submissions, 0)
        self.assertEqual(driver.autocomplete_resets, ["token"])

    def test_autocomplete_timeout_and_empty_suggestion_list_stop_without_submit(self) -> None:
        criteria = criteria_from_mapping({
            "mode": "PERSONNE_MORALE",
            "commune_wilaya": "34000 : BORDJ BOU ARRERIDJ",
        })
        for status in (AutocompleteTestStatus.TIMEOUT, AutocompleteTestStatus.NO_SUGGESTIONS):
            with self.subTest(status=status):
                driver = FakeSearchDriver()
                driver.autocomplete_status = status
                if status is AutocompleteTestStatus.NO_SUGGESTIONS:
                    driver.autocomplete_suggestions = ()
                with self.assertRaises(SearchSuggestionMissing):
                    SearchExecutor(driver).execute(criteria, confirmed=True)
                self.assertEqual(driver.submissions, 0)

    def test_form_ambiguity_existing_values_and_unexpected_navigation_stop_safely(self) -> None:
        criteria = criteria_from_mapping({"mode": "PERSONNE_MORALE", "raison_sociale": "Test"})
        for setup in ("nonempty", "button_absent", "button_ambiguous", "disabled_button", "navigation"):
            with self.subTest(setup=setup):
                driver = FakeSearchDriver()
                if setup == "nonempty":
                    driver.all_empty = False
                elif setup == "button_absent":
                    driver.button_count = 0
                elif setup == "button_ambiguous":
                    driver.button_count = 2
                elif setup == "disabled_button":
                    driver.button_enabled = False
                elif setup == "navigation":
                    driver.marker_change_after_fill = True
                with self.assertRaises(SearchExecutionError):
                    SearchExecutor(driver).execute(criteria, confirmed=True)
                self.assertEqual(driver.submissions, 0)

    def test_missing_criterion_control_and_ambiguous_form_stop_without_submit(self) -> None:
        criteria = criteria_from_mapping({
            "mode": "PERSONNE_MORALE", "activite": "442102", "commune_wilaya": "34000"
        })
        for setup in ("missing_control", "ambiguous_form"):
            with self.subTest(setup=setup):
                driver = FakeSearchDriver()
                if setup == "missing_control":
                    driver.missing_control = "activite"
                else:
                    driver.ambiguous_form = True
                with self.assertRaises(SearchExecutionError):
                    SearchExecutor(driver).execute(criteria, confirmed=True)
                self.assertEqual(driver.submissions, 0)

    def test_pre_submit_diagnostic_failure_aborts_before_click(self) -> None:
        criteria = criteria_from_mapping({
            "mode": "PERSONNE_MORALE", "activite": "442102", "commune_wilaya": "34000"
        })
        driver = FakeSearchDriver()
        with self.assertRaises(SearchExecutionError):
            SearchExecutor(driver).execute(
                criteria, confirmed=True,
                on_pre_submit=lambda _diagnostic: (_ for _ in ()).throw(RuntimeError("disk failure")),
            )
        self.assertEqual(driver.submissions, 0)

    def test_structural_diagnostic_covers_no_results_pagination_columns_and_errors_only(self) -> None:
        criteria = criteria_from_mapping({
            "mode": "PERSONNE_MORALE",
            "commune_wilaya": "34000 : BORDJ BOU ARRERIDJ",
        })
        driver = FakeSearchDriver()
        driver.result = SearchObservation(
            title="Recherche",
            sanitized_url=driver.marker,
            result_count=0,
            table_count=1,
            row_count=0,
            columns=("Dénomination", "Wilaya"),
            pagination_visible=False,
            no_results=True,
            errors=("Erreur de simulation",),
            session_expired=False,
            result_zone_found=True,
            result_zone_tag="main",
        )
        result = SearchExecutor(driver).execute(criteria, confirmed=True)
        report = result.to_mapping()
        self.assertEqual(report["result_count"], 0)
        self.assertEqual(report["row_count"], 0)
        self.assertEqual(report["columns"], ["Dénomination", "Wilaya"])
        self.assertTrue(report["no_results"])
        self.assertEqual(report["errors"], ["Erreur de simulation"])
        self.assertTrue(report["result_zone_found"])
        self.assertEqual(report["result_zone_tag"], "main")
        self.assertFalse(report["automatic_pagination"])
        self.assertFalse(report["company_rows_collected"])
        self.assertFalse(report["details_opened"])

    def test_expired_session_after_submission_is_reported_without_retry(self) -> None:
        criteria = criteria_from_mapping({
            "mode": "PERSONNE_MORALE",
            "commune_wilaya": "34000 : BORDJ BOU ARRERIDJ",
        })
        driver = FakeSearchDriver()
        driver.result = SearchObservation(
            title="Connexion",
            sanitized_url="https://sidjilcom.cnrc.dz/fr/login",
            result_count=None,
            table_count=0,
            row_count=0,
            columns=(),
            pagination_visible=False,
            no_results=False,
            errors=("Session expirée",),
            session_expired=True,
        )
        executor = SearchExecutor(driver)
        with self.assertRaises(SearchSessionExpired):
            executor.execute(criteria, confirmed=True)
        self.assertEqual(driver.submissions, 1)
        self.assertEqual(executor.submission_count, 1)

    def test_code_label_suggestions_are_selected_only_when_the_code_token_is_exact(self) -> None:
        criteria = criteria_from_mapping({
            "mode": "PERSONNE_MORALE", "activite": "442102", "commune_wilaya": "34000",
        })
        driver = FakeSearchDriver()
        driver.autocomplete_suggestions_by_field = {
            "activite": ("442102 : Libellé officiel de l'activité",),
            "commune_wilaya": ("34000 : Libellé officiel commune/wilaya",),
        }
        result = SearchExecutor(driver).execute(criteria, confirmed=True)
        self.assertEqual(driver.autocomplete_selected, [0, 0])
        self.assertEqual(driver.submissions, 1)
        self.assertEqual(result.result_count, 17)

    def test_post_fill_dom_mismatch_aborts_before_submitting(self) -> None:
        criteria = criteria_from_mapping({
            "mode": "PERSONNE_MORALE", "activite": "442102", "commune_wilaya": "34000",
        })
        driver = FakeSearchDriver()
        driver.final_value_overrides["activite"] = "442103 : autre activité"
        with self.assertRaises(SearchExecutionError) as caught:
            SearchExecutor(driver).execute(criteria, confirmed=True)
        self.assertEqual(driver.submissions, 0)
        self.assertEqual(caught.exception.code, "filled_value_mismatch")
        self.assertEqual(caught.exception.diagnostic["current_field"], "activite")
        self.assertNotIn("442103", str(caught.exception.diagnostic))

    def test_required_field_missing_or_invalid_stops_before_click(self) -> None:
        criteria = criteria_from_mapping({
            "mode": "PERSONNE_MORALE", "activite": "442102", "commune_wilaya": "34000",
        })
        driver = FakeSearchDriver()
        driver.required_invalid_controls = ("Forme juridique",)
        steps: list[SearchStep] = []
        with self.assertRaises(SearchExecutionError) as caught:
            SearchExecutor(driver).execute(criteria, confirmed=True, on_step=steps.append)
        self.assertEqual(driver.submissions, 0)
        self.assertNotIn(SearchStep.SUBMITTING, steps)
        self.assertEqual(caught.exception.code, "required_field_invalid")

    def test_absent_search_button_or_wrong_parent_portlet_stops_before_click(self) -> None:
        criteria = criteria_from_mapping({
            "mode": "PERSONNE_MORALE", "activite": "442102", "commune_wilaya": "34000",
        })
        for attribute, value in (("button_count", 0), ("button_count", 2), ("parent_portlet_confirmed", False)):
            with self.subTest(attribute=attribute):
                driver = FakeSearchDriver()
                setattr(driver, attribute, value)
                steps: list[SearchStep] = []
                with self.assertRaises(SearchExecutionError):
                    SearchExecutor(driver).execute(criteria, confirmed=True, on_step=steps.append)
                self.assertEqual(driver.submissions, 0)
                self.assertNotIn(SearchStep.SUBMITTING, steps)

    def test_one_executor_cannot_submit_twice_after_double_confirmation(self) -> None:
        criteria = criteria_from_mapping({
            "mode": "PERSONNE_MORALE", "activite": "442102", "commune_wilaya": "34000",
        })
        driver = FakeSearchDriver()
        executor = SearchExecutor(driver)
        executor.execute(criteria, confirmed=True)
        with self.assertRaises(SearchExecutionError) as caught:
            executor.execute(criteria, confirmed=True)
        self.assertEqual(caught.exception.code, "duplicate_execution")
        self.assertEqual(driver.submissions, 1)
        self.assertEqual(executor.submission_count, 1)

    def test_blank_post_submit_page_is_result_unknown_not_success(self) -> None:
        criteria = criteria_from_mapping({
            "mode": "PERSONNE_MORALE", "activite": "442102", "commune_wilaya": "34000",
        })
        driver = FakeSearchDriver()
        driver.result = SearchObservation(
            title="Recherche Commerçant", sanitized_url=driver.marker,
            result_count=None, table_count=0, row_count=0, columns=(),
            pagination_visible=False, no_results=False, errors=(), session_expired=False,
        )
        steps: list[SearchStep] = []
        with self.assertRaises(SearchExecutionError) as caught:
            SearchExecutor(driver).execute(criteria, confirmed=True, on_step=steps.append)
        self.assertEqual(driver.submissions, 1)
        self.assertEqual(caught.exception.code, "results_observation_error")
        self.assertIn(SearchStep.SUBMITTED, steps)
        self.assertNotIn(SearchStep.RESULTS_DETECTED, steps)

    def test_header_only_table_without_count_or_no_results_is_not_success(self) -> None:
        criteria = criteria_from_mapping({
            "mode": "PERSONNE_MORALE", "activite": "442102", "commune_wilaya": "34000",
        })
        driver = FakeSearchDriver()
        driver.result = SearchObservation(
            title="Recherche Commerçant", sanitized_url=driver.marker,
            result_count=None, table_count=1, row_count=0, columns=("Dénomination", "Wilaya"),
            pagination_visible=False, no_results=False, errors=(), session_expired=False,
        )
        with self.assertRaises(SearchResultsObservationError):
            SearchExecutor(driver).execute(criteria, confirmed=True)
        self.assertEqual(driver.submissions, 1)

    def test_result_signals_with_a_portal_error_remain_uncertain(self) -> None:
        criteria = criteria_from_mapping({
            "mode": "PERSONNE_MORALE", "activite": "442102", "commune_wilaya": "34000",
        })
        driver = FakeSearchDriver()
        driver.result = SearchObservation(
            title="Recherche Commerçant", sanitized_url=driver.marker,
            result_count=17, table_count=1, row_count=17, columns=("Dénomination",),
            pagination_visible=False, no_results=False, errors=("Erreur de chargement",),
            session_expired=False,
        )
        with self.assertRaises(SearchResultsObservationError):
            SearchExecutor(driver).execute(criteria, confirmed=True)
        self.assertEqual(driver.submissions, 1)

    def test_first_search_policy_accepts_only_exact_personne_morale_pair(self) -> None:
        allowed = criteria_from_mapping({
            "mode": "PERSONNE_MORALE", "activite": "442102", "commune_wilaya": "34000"
        })
        validate_first_controlled_search(allowed)
        outside_scope = (
            criteria_from_mapping({
                "mode": "PERSONNE_MORALE", "activite": "442102", "commune_wilaya": "34000",
                "raison_sociale": "Test",
            }),
            criteria_from_mapping({
                "mode": "PERSONNE_PHYSIQUE", "activite": "442102", "commune_wilaya": "34000",
            }),
            criteria_from_mapping({
                "mode": "PERSONNE_MORALE", "activite": "442102", "commune_wilaya": "34000 : BORDJ BOU ARRERIDJ",
            }),
            criteria_from_mapping({
                "mode": "PERSONNE_MORALE", "activite": "442103", "commune_wilaya": "34000",
            }),
            criteria_from_mapping({"mode": "PERSONNE_MORALE", "commune_wilaya": "34000"}),
        )
        for criteria in outside_scope:
            with self.subTest(criteria=criteria):
                with self.assertRaises(SearchExecutionError):
                    validate_first_controlled_search(criteria)

    def test_post_submit_observation_failure_is_not_retried(self) -> None:
        criteria = criteria_from_mapping({
            "mode": "PERSONNE_MORALE", "activite": "442102", "commune_wilaya": "34000"
        })
        driver = FakeSearchDriver()
        driver.observation_error = True
        executor = SearchExecutor(driver)
        with self.assertRaises(SearchExecutionError):
            executor.execute(criteria, confirmed=True)
        self.assertEqual(driver.submissions, 1)
        self.assertEqual(executor.submission_count, 1)


if __name__ == "__main__":
    unittest.main()
