"""Tests simulés du cycle autocomplete; aucun test n'ouvre une session Sidjilcom réelle."""

from __future__ import annotations

import time
import unittest

from sidjily.sidjilcom.autocomplete import (
    AUTOCOMPLETE_FIELD_LABELS,
    AutocompleteContainerInfo,
    AutocompleteControlAmbiguous,
    AutocompleteControlDisabled,
    AutocompleteControlInfo,
    AutocompleteControlNotFound,
    AutocompleteFieldNotEmpty,
    AutocompleteFieldNotMapped,
    AutocompleteObservation,
    AutocompletePageChanged,
    AutocompleteSelectionResult,
    AutocompleteSuggestionUnsafe,
    AutocompleteSnapshot,
    AutocompleteSuggestion,
    AutocompleteTestStatus,
    AutocompleteTester,
)


class FakeAutocompleteDriver:
    """Pilote de DOM entièrement simulé pour tests unitaires hors session réelle."""

    def __init__(self):
        self.marker = "https://sidjilcom.cnrc.dz/fr/group/sidjilcom/repertoire-des-commercants"
        self.value = ""
        self.enabled = True
        self.missing = False
        self.ambiguous = False
        self.last_field = ""
        self.last_suffix = ""
        self.typed: list[str] = []
        self.cleared = 0
        self.waited = 0
        self.clicked: list[int] = []
        self.snapshots: list[AutocompleteSnapshot] = []
        self.after_click: AutocompleteSnapshot | None = None
        self.info = AutocompleteControlInfo(
            field_id="activite",
            label=AUTOCOMPLETE_FIELD_LABELS["activite"],
            suffix="_activi",
            tag_name="input",
            element_id="generated-control-id",
            class_names=("yui3-aclist-input",),
            role="combobox",
            aria_autocomplete="list",
            aria_haspopup="listbox",
            aria_controls="suggestions",
            aria_owns=None,
            aria_activedescendant=None,
            aria_expanded="true",
            autocomplete_attribute="off",
            frame_label="Document principal",
            enabled=True,
            yui_global_available=True,
        )

    def locate_control(self, field_id: str, suffix: str):
        self.last_field = field_id
        self.last_suffix = suffix
        if self.missing:
            raise AutocompleteControlNotFound()
        if self.ambiguous:
            raise AutocompleteControlAmbiguous()
        info = self.info
        if not self.enabled:
            from dataclasses import replace
            info = replace(info, enabled=False)
        return _Resolved(info, self)

    def page_marker(self) -> str:
        return self.marker

    def current_value(self, _handle) -> str:
        return self.value

    def type_sequentially(self, _handle, text: str) -> None:
        self.typed.append(text)
        self.value = text

    def inspect(self, _control) -> AutocompleteSnapshot:
        if self.after_click is not None and self.clicked:
            return self.after_click
        if self.snapshots:
            return self.snapshots.pop(0)
        return self.snapshot(visible=False, suggestions=())

    def wait(self, milliseconds: int) -> None:
        self.waited += milliseconds
        time.sleep(min(milliseconds, 10) / 1000)

    def click_suggestion(self, _control, suggestion: AutocompleteSuggestion) -> None:
        self.clicked.append(suggestion.index)
        self.value = suggestion.text

    def clear(self, _control) -> None:
        self.value = ""
        self.cleared += 1

    def snapshot(
        self,
        *,
        visible: bool,
        suggestions: tuple[AutocompleteSuggestion, ...],
    ) -> AutocompleteSnapshot:
        containers = (
            AutocompleteContainerInfo(
                tag_name="ul",
                element_id="suggestions",
                class_names=("yui3-aclist-list",),
                role="listbox",
                aria_expanded="true" if visible else "false",
                visible=visible,
                relation="référencé par aria-controls/owns/activedescendant",
                selector="body > ul:nth-of-type(1)",
            ),
        ) if visible else ()
        return AutocompleteSnapshot(
            control=self.info,
            containers=containers,
            suggestions=suggestions,
            page_marker=self.marker,
            nearby_controls=("input|text||Activité||enabled|Activité",),
        )


class _Resolved:
    def __init__(self, info, handle):
        self.info = info
        self.handle = handle
        self.frame = None


def _suggestion(index: int, text: str, safe: bool = True) -> AutocompleteSuggestion:
    return AutocompleteSuggestion(
        index=index,
        text=text,
        tag_name="li",
        class_names=("yui3-aclist-item",),
        role="option",
        aria_selected="false",
        data_attribute_names=("data-yui3-aclist-item",),
        safe_to_select=safe,
        selector=f"ul > li:nth-of-type({index + 1})",
    )


class SimulatedAutocompleteTests(unittest.TestCase):
    def test_locates_field_by_central_mapping_and_waits_for_suggestions(self) -> None:
        driver = FakeAutocompleteDriver()
        empty = driver.snapshot(visible=False, suggestions=())
        visible = driver.snapshot(visible=True, suggestions=(_suggestion(0, "Activité test"),))
        driver.snapshots = [empty, visible]
        tester = AutocompleteTester(driver)

        result = tester.prepare("activite", "activite de test", timeout_ms=700)

        self.assertEqual(driver.last_field, "activite")
        self.assertEqual(driver.last_suffix, "_activi")
        self.assertEqual(driver.typed, ["activite de test"])
        self.assertGreaterEqual(driver.waited, 100)
        self.assertEqual(result.status, AutocompleteTestStatus.SUGGESTIONS)
        self.assertEqual([item.text for item in result.suggestions], ["Activité test"])
        self.assertIsNotNone(result.token)

    def test_exposes_multiple_suggestions_and_selects_the_chosen_one(self) -> None:
        driver = FakeAutocompleteDriver()
        options = (_suggestion(0, "Activité alpha"), _suggestion(1, "Activité bêta"))
        visible_snapshot = driver.snapshot(visible=True, suggestions=options)
        driver.snapshots = [visible_snapshot, visible_snapshot]
        driver.after_click = driver.snapshot(visible=False, suggestions=())
        tester = AutocompleteTester(driver)

        observed = tester.prepare("activite", "activite", timeout_ms=600)
        self.assertEqual(len(observed.suggestions), 2)
        selected = tester.select(observed.token or "", 1)

        self.assertEqual(driver.clicked, [1])
        self.assertEqual(driver.value, "Activité bêta")
        self.assertIsInstance(selected, AutocompleteSelectionResult)
        self.assertTrue(selected.accepted)
        self.assertTrue(selected.input_matches_suggestion)
        self.assertTrue(selected.suggestions_disappeared)
        self.assertEqual(selected.suggestion_text, "Activité bêta")

    def test_visible_empty_list_is_reported_as_no_suggestions_and_cleared(self) -> None:
        driver = FakeAutocompleteDriver()
        driver.snapshots = [driver.snapshot(visible=True, suggestions=())]
        result = AutocompleteTester(driver).prepare("commune_wilaya", "valeur introuvable")

        self.assertEqual(result.status, AutocompleteTestStatus.NO_SUGGESTIONS)
        self.assertEqual(driver.last_suffix, "_wilcom")
        self.assertEqual(driver.value, "")
        self.assertEqual(driver.cleared, 1)

    def test_timeout_is_distinct_from_an_empty_visible_suggestion_list(self) -> None:
        driver = FakeAutocompleteDriver()
        tester = AutocompleteTester(driver)
        result = tester.prepare("nationalite", "valeur absente", timeout_ms=500)

        self.assertEqual(result.status, AutocompleteTestStatus.TIMEOUT)
        self.assertTrue(result.timed_out)
        self.assertEqual(driver.last_suffix, "_nation")
        self.assertEqual(driver.value, "")
        self.assertGreaterEqual(driver.cleared, 1)

    def test_missing_or_disabled_component_fails_without_typing(self) -> None:
        missing_driver = FakeAutocompleteDriver()
        missing_driver.missing = True
        with self.assertRaises(AutocompleteControlNotFound):
            AutocompleteTester(missing_driver).prepare("activite", "test")
        self.assertEqual(missing_driver.typed, [])

        disabled_driver = FakeAutocompleteDriver()
        disabled_driver.enabled = False
        with self.assertRaises(AutocompleteControlDisabled):
            AutocompleteTester(disabled_driver).prepare("activite", "test")
        self.assertEqual(disabled_driver.typed, [])

        ambiguous_driver = FakeAutocompleteDriver()
        ambiguous_driver.ambiguous = True
        with self.assertRaises(AutocompleteControlAmbiguous):
            AutocompleteTester(ambiguous_driver).prepare("activite", "test")
        self.assertEqual(ambiguous_driver.typed, [])

    def test_nonempty_field_is_preserved_and_refused(self) -> None:
        driver = FakeAutocompleteDriver()
        driver.value = "critère déjà présent"
        with self.assertRaises(AutocompleteFieldNotEmpty):
            AutocompleteTester(driver).prepare("activite", "valeur de test")
        self.assertEqual(driver.value, "critère déjà présent")
        self.assertEqual(driver.typed, [])
        self.assertEqual(driver.cleared, 0)

    def test_page_navigation_during_wait_stops_the_test(self) -> None:
        driver = FakeAutocompleteDriver()
        driver.snapshots = [driver.snapshot(visible=False, suggestions=())]
        original_wait = driver.wait

        def navigate(_milliseconds: int) -> None:
            original_wait(_milliseconds)
            driver.marker = "https://sidjilcom.cnrc.dz/web/sidjilcom/login"

        driver.wait = navigate  # type: ignore[method-assign]
        with self.assertRaises(AutocompletePageChanged):
            AutocompleteTester(driver).prepare("activite", "test", timeout_ms=700)
        self.assertEqual(driver.cleared, 0)

    def test_unsafe_option_cannot_be_clicked_and_test_can_be_reset(self) -> None:
        driver = FakeAutocompleteDriver()
        options = (_suggestion(0, "Lien non sûr", safe=False),)
        visible_snapshot = driver.snapshot(visible=True, suggestions=options)
        driver.snapshots = [visible_snapshot, visible_snapshot]
        tester = AutocompleteTester(driver)
        observation = tester.prepare("nationalite", "test")
        self.assertFalse(observation.suggestions[0].safe_to_select)
        with self.assertRaises(AutocompleteSuggestionUnsafe):
            tester.select(observation.token or "", 0)
        self.assertEqual(driver.clicked, [])
        # Le faux pilote confirme l'action de nettoyage; aucun clic n'est simulé.
        reset = tester.reset(observation.token or "")
        self.assertTrue(reset.cleared)
        self.assertEqual(driver.value, "")
        self.assertEqual(driver.clicked, [])

    def test_unknown_field_id_is_rejected_before_dom_access(self) -> None:
        driver = FakeAutocompleteDriver()
        with self.assertRaises(AutocompleteFieldNotMapped):
            AutocompleteTester(driver).prepare("unknown", "test")
        self.assertEqual(driver.last_field, "")


if __name__ == "__main__":
    unittest.main()
