from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace

from sidjily.sidjilcom.browser import DOM_SNAPSHOT_SCRIPT
from sidjily.sidjilcom.search_playwright import PlaywrightSearchDriver
from sidjily.sidjilcom.diagnostics import (
    build_form_diagnostics,
    format_diagnostic,
    sanitize_diagnostic_url,
)
from sidjily.sidjilcom.search_form_diagnostics import (
    PreflightButton,
    PreflightForm,
    SearchPreflightSnapshot,
    analyze_preflight,
    compare_signatures,
    expected_field_rows,
    format_real_form_report,
    snapshot_signature,
)


def preflight_form(
    *,
    form_id: str = "company-form",
    expected: int = 1,
    nonempty: int = 0,
    search: tuple[PreflightButton, ...] | None = None,
    method: str = "POST",
    action_is_portal: bool = True,
    visible: bool = True,
) -> PreflightForm:
    return PreflightForm(
        frame_name="Document principal",
        form_id=form_id,
        form_name="",
        title="Recherche commerciale",
        action="https://sidjilcom.cnrc.dz/fr/search",
        method=method,
        class_name="search-form",
        role="search",
        visible=visible,
        css_selector=f'form[id="{form_id}"]',
        expected_control_count=expected,
        expected_controls=("input/text · name=prefix_wilcom",) if expected else (),
        visible_control_count=5,
        nonempty_control_count=nonempty,
        nonempty_controls=("input/text · name=prefix_wilcom",) if nonempty else (),
        search_buttons=search if search is not None else (
            PreflightButton("Rechercher", True, True, True, f'form[id="{form_id}"] button'),
        ),
        reset_buttons=(PreflightButton("Réinitialiser", True, True, True),),
        action_is_portal=action_is_portal,
    )


def preflight_snapshot(forms: tuple[PreflightForm, ...], *, global_matches: int = 1, route: bool = True) -> SearchPreflightSnapshot:
    return SearchPreflightSnapshot(
        route_matches=route,
        page_section="Trouver une entreprise",
        expected_suffix="_wilcom",
        visible_form_count=sum(form.visible for form in forms),
        global_expected_control_count=global_matches,
        global_expected_controls=("Document principal · input/text · name=prefix_wilcom",) if global_matches else (),
        forms=forms,
    )


class _FakeCollection:
    def __init__(self, items):
        self.items = list(items)

    def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]


class _FakeControl:
    def __init__(self):
        self.secret_value = "TOP-SECRET-PRIVATE-COMPANY"

    def get_attribute(self, name):
        return {
            "name": "prefix_wilcom",
            "id": "wilcom",
            "type": "text",
            "class": "yui3-aclist-input",
            "aria-autocomplete": "list",
        }.get(name)

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def evaluate(self, _script):
        return "input"

    def input_value(self, **_kwargs):
        return self.secret_value


class _FakeSearchButton:
    def get_attribute(self, name):
        return {"type": "submit", "id": "search"}.get(name)

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def evaluate(self, _script):
        return "button"

    def inner_text(self, **_kwargs):
        return "Rechercher"


class _FakeSearchForm:
    def __init__(self, control, button):
        self.control = control
        self.button = button

    def is_visible(self):
        return True

    def get_attribute(self, name):
        return {
            "id": "moral-search",
            "name": "moral_search",
            "aria-label": "Recherche commerciale",
            "class": "search-form",
            "role": "search",
            "method": "POST",
            "action": "/fr/group/sidjilcom/repertoire-des-commercants/search",
        }.get(name)

    def locator(self, selector):
        if selector in ("input[name], select[name], textarea[name]", "input, select, textarea"):
            return _FakeCollection([self.control])
        if selector.startswith("button,"):
            return _FakeCollection([self.button])
        raise AssertionError(f"Unexpected selector: {selector}")


class _FakeSearchFrame:
    name = ""

    def __init__(self, form):
        self.form = form
        self.control = form.control

    def locator(self, selector):
        if selector == "form":
            return _FakeCollection([self.form])
        if selector == "input[name], select[name], textarea[name]":
            return _FakeCollection([self.control])
        raise AssertionError(f"Unexpected frame selector: {selector}")


class _FakeSearchPage:
    def __init__(self, frame):
        self.url = "https://sidjilcom.cnrc.dz/fr/group/sidjilcom/repertoire-des-commercants"
        self.frames = [frame]

    def is_closed(self):
        return False


class ReadOnlyPreflightTests(unittest.TestCase):
    def test_playwright_reader_keeps_only_boolean_prefill_state_not_field_value(self) -> None:
        control = _FakeControl()
        form = _FakeSearchForm(control, _FakeSearchButton())
        page = _FakeSearchPage(_FakeSearchFrame(form))
        driver = PlaywrightSearchDriver(page, "https://sidjilcom.cnrc.dz/", lambda _mode: None)
        snapshot = driver.diagnose_preflight()
        bundle = build_form_diagnostics({"forms": [], "fields": [], "buttons": []})
        report = format_real_form_report(
            SimpleNamespace(section="Trouver une entreprise", title="Recherche", url=page.url, portlet_scope_found=False, report=""),
            bundle,
            "after_navigation",
            snapshot,
        )
        self.assertEqual(snapshot.forms[0].nonempty_control_count, 1)
        self.assertNotIn(control.secret_value, repr(snapshot))
        self.assertNotIn(control.secret_value, report)
        self.assertIn("PRÉREMPLI", report)

    def test_unique_form_with_expected_field_and_associated_button_passes_current_checks(self) -> None:
        findings = analyze_preflight(preflight_snapshot((preflight_form(),)))
        self.assertEqual(len(findings), 1)
        self.assertIn("AUCUN ÉCHEC REPRODUIT", findings[0])

    def test_absent_and_multiple_visible_forms_are_distinguished(self) -> None:
        absent = analyze_preflight(preflight_snapshot((), global_matches=0))
        self.assertTrue(any("ABSENT" in finding for finding in absent))
        multiple = analyze_preflight(preflight_snapshot((preflight_form(), preflight_form(form_id="second"))))
        self.assertTrue(any("AMBIGUÏTÉ" in finding for finding in multiple))

    def test_expected_field_absent_or_outside_form_is_distinguished(self) -> None:
        missing = analyze_preflight(preflight_snapshot((preflight_form(expected=0),), global_matches=0))
        self.assertTrue(any("CHAMP ABSENT" in finding for finding in missing))
        outside = analyze_preflight(preflight_snapshot((preflight_form(expected=0),), global_matches=1))
        self.assertTrue(any("ASSOCIATION" in finding for finding in outside))

    def test_prefilled_controls_and_missing_associated_search_button_are_reported_without_values(self) -> None:
        prefilled = preflight_form(nonempty=2, search=())
        findings = analyze_preflight(preflight_snapshot((prefilled,)))
        rendered = "\n".join(findings)
        self.assertIn("PRÉREMPLI", rendered)
        self.assertIn("BOUTON", rendered)
        self.assertNotIn("VALUE-SECRET", rendered)
        self.assertNotIn("input.value", rendered)

    def test_disabled_or_unassociated_search_button_is_not_accepted(self) -> None:
        button = PreflightButton("Rechercher", True, False, False)
        findings = analyze_preflight(preflight_snapshot((preflight_form(search=(button,)),)))
        self.assertTrue(any("aucun bouton Rechercher visible et activé n'est associé" in finding for finding in findings))

    def test_route_action_and_method_are_reported_as_preflight_conditions(self) -> None:
        snapshot = preflight_snapshot((preflight_form(method="GET", action_is_portal=False),), route=False)
        findings = "\n".join(analyze_preflight(snapshot))
        self.assertIn("ROUTE", findings)
        self.assertIn("MÉTHODE", findings)
        self.assertIn("ACTION", findings)


class StructuralSnapshotTests(unittest.TestCase):
    @staticmethod
    def bundle(class_name: str = "field"):
        fields = []
        for index in range(1, 6):
            fields.append({
                "label": f"nrc{index}",
                "associated_text": f"nrc{index}",
                "frame_name": "Document principal",
                "frame_url": "https://sidjilcom.cnrc.dz/fr/search",
                "html_type": "select" if index in (2, 5) else "text",
                "tag_name": "select" if index in (2, 5) else "input",
                "name": f"company_nrc{index}",
                "id": f"company-nrc{index}",
                "class_name": class_name,
                "form_id": "company-form",
                "form_title": "Recherche commerciale",
                "form_name": "company-search",
                "form_action": "https://sidjilcom.cnrc.dz/fr/search?token=PRIVATE-TOKEN",
                "form_method": "POST",
                "visible": True,
                "container": {
                    "tag": "fieldset",
                    "id": "company-details",
                    "class_name": "fieldset-company",
                    "role": "group",
                    "heading": "Rechercher par l'information de la société",
                    "visible": True,
                },
                "option_count": 3 if index in (2, 5) else 0,
                "options": [],
            })
        raw = {
            "frames": [{"name": "Document principal", "url": "https://sidjilcom.cnrc.dz/fr/search", "accessible": True, "form_count": 1, "control_count": 5}],
            "form_entries": [{
                "form_id": "company-form",
                "form_title": "Recherche commerciale",
                "form_name": "company-search",
                "action": "https://sidjilcom.cnrc.dz/fr/search?token=PRIVATE-TOKEN&record=12345678",
                "method": "POST",
                "class_name": "search-form",
                "role": "search",
                "visible": True,
                "frame_name": "Document principal",
                "frame_url": "https://sidjilcom.cnrc.dz/fr/search",
            }],
            "fields": fields,
            "buttons": [{
                "text": "Rechercher", "html_type": "submit", "tag_name": "button",
                "form_id": "company-form", "form_title": "Recherche commerciale",
                "form_action": "https://sidjilcom.cnrc.dz/fr/search?token=PRIVATE-TOKEN",
                "form_method": "POST", "visible": True,
            }],
            "clickables": [],
        }
        return build_form_diagnostics(raw)

    def test_nrc1_to_nrc5_are_reported_as_structure_without_assigning_meaning(self) -> None:
        bundle = self.bundle()
        rows = "\n".join(expected_field_rows(bundle))
        for index in range(1, 6):
            self.assertIn(f"nrc{index}", rows)
        self.assertIn("sans interprétation", rows)
        self.assertIn("select", rows)
        self.assertIn("input", rows)
        self.assertIn("Rechercher par l'information de la société", bundle.forms[0].fields[0].container_label)

    def test_real_report_detects_target_sections_and_never_includes_secret_or_endpoint_data(self) -> None:
        bundle = self.bundle()
        page = SimpleNamespace(
            title="Recherche commerciale",
            section="Trouver une entreprise",
            url="https://sidjilcom.cnrc.dz/fr/search?session=PRIVATE-SESSION",
            portlet_scope_found=True,
            portlet_scopes=(("Document principal", "div", "search-portlet", "company-portlet"),),
            report="",
        )
        report = format_real_form_report(
            page, bundle, "after_navigation", preflight_snapshot((preflight_form(),))
        )
        self.assertIn("DIAGNOSTIC STRUCTUREL RÉEL", report)
        self.assertIn("SECTION « Rechercher par l'information de la société »", report)
        self.assertIn("nrc5", report)
        self.assertIn("Capture explicitement demandée : Après navigation", report)
        self.assertNotIn("PRIVATE-TOKEN", report)
        self.assertNotIn("PRIVATE-SESSION", report)
        self.assertNotIn("12345678", report)
        self.assertNotIn("clickables", report.casefold())
        self.assertNotIn("data-api", report.casefold())

    def test_split_associate_name_labels_are_reported_without_rewriting_combined_mapping(self) -> None:
        raw = {
            "form_entries": [{"form_id": "moral", "form_title": "Recherche commerciale", "method": "POST"}],
            "fields": [
                {"label": "Nom de l'associé", "associated_text": "Nom de l'associé", "name": "prefix_nom", "html_type": "text", "tag_name": "input", "form_id": "moral", "container": {"tag": "fieldset", "heading": "Informations de l'associé"}},
                {"label": "Prénom de l'associé", "associated_text": "Prénom de l'associé", "name": "prefix_prenom", "html_type": "text", "tag_name": "input", "form_id": "moral", "container": {"tag": "fieldset", "heading": "Informations de l'associé"}},
            ],
        }
        rows = "\n".join(expected_field_rows(build_form_diagnostics(raw)))
        self.assertIn("Nom de l'associé", rows)
        self.assertIn("Prénom de l'associé", rows)
        self.assertIn("mappage central combiné", rows)

    def test_dom_mutation_between_captures_is_detected_from_metadata_only(self) -> None:
        first = self.bundle("field-a")
        second = self.bundle("field-b")
        preflight = preflight_snapshot((preflight_form(),))
        diff = "\n".join(compare_signatures(
            snapshot_signature(first, preflight), snapshot_signature(second, preflight)
        ))
        self.assertIn("différence", diff)
        self.assertIn("field-a", diff)
        self.assertIn("field-b", diff)

    def test_snapshot_comparison_masks_nonstandard_button_text(self) -> None:
        bundle = self.bundle()
        private_button_bundle = replace(
            bundle,
            buttons=(replace(bundle.buttons[0], text="PRIVATE COMPANY PERSON"),),
        )
        signature = snapshot_signature(private_button_bundle, preflight_snapshot((preflight_form(),)))
        comparison = "\n".join(compare_signatures(
            snapshot_signature(bundle, preflight_snapshot((preflight_form(),))), signature
        ))
        self.assertNotIn("PRIVATE COMPANY PERSON", repr(signature))
        self.assertNotIn("PRIVATE COMPANY PERSON", comparison)

    def test_report_assimilates_action_url_and_never_emits_values_or_api_metadata(self) -> None:
        bundle = self.bundle()
        page = SimpleNamespace(
            title="Recherche commerciale",
            section="Trouver une entreprise",
            url="https://sidjilcom.cnrc.dz/fr/search?session=PRIVATE-SESSION",
            portlet_scopes=(),
        )
        report = format_diagnostic(page, bundle, include_endpoint_metadata=False)
        for secret in ("PRIVATE-TOKEN", "PRIVATE-SESSION", "12345678", "input.value", "document.cookie"):
            self.assertNotIn(secret, report)
        self.assertIn("/fr/search", report)
        self.assertIn("Endpoint/API direct : non exposé", report)
        self.assertNotIn("PRIVATE-TOKEN", repr(bundle))

    def test_secret_like_path_and_query_are_removed_from_urls(self) -> None:
        safe = sanitize_diagnostic_url(
            "https://sidjilcom.cnrc.dz/fr/search/session-PRIVATE-SESSION?token=PRIVATE-TOKEN"
        )
        self.assertEqual(safe, "https://sidjilcom.cnrc.dz/[chemin masqué]")
        self.assertNotIn("PRIVATE", safe)

    def test_dom_snapshot_does_not_read_field_values_or_network_state(self) -> None:
        self.assertIn("options.formOnly", DOM_SNAPSHOT_SCRIPT)
        self.assertNotIn("element.value", DOM_SNAPSHOT_SCRIPT)
        self.assertNotIn("option.value", DOM_SNAPSHOT_SCRIPT)
        self.assertNotIn("getAttribute('value')", DOM_SNAPSHOT_SCRIPT)
        self.assertNotIn("document.cookie", DOM_SNAPSHOT_SCRIPT)
        self.assertNotIn("localStorage", DOM_SNAPSHOT_SCRIPT)
        self.assertNotIn("fetch(", DOM_SNAPSHOT_SCRIPT)
        self.assertNotIn("XMLHttpRequest", DOM_SNAPSHOT_SCRIPT)
        self.assertNotIn(".click(", DOM_SNAPSHOT_SCRIPT)


if __name__ == "__main__":
    unittest.main()
