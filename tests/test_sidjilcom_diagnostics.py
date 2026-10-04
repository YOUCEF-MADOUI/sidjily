from __future__ import annotations

import inspect
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from sidjily.sidjilcom.browser import (
    SEARCH_MODE_CANDIDATES_SCRIPT,
    SEARCH_MODE_LABELS,
    PlaywrightBrowser,
    SearchModeAnalysisError,
    _SearchModeCandidate,
    normalize_search_mode_label,
)
from sidjily.sidjilcom.config import DEFAULT_SIDJILCOM_URL, SessionConfig
from sidjily.sidjilcom.selectors import DEFAULT_ENTERPRISE_SEARCH_ROUTE
from sidjily.sidjilcom.diagnostics import (
    diagnostics_from_html_fixture,
    format_diagnostic,
    format_search_mode_comparison,
)


FORM_FIXTURE = """
<form id="registry-search" aria-label="Recherche commerciale">
  <fieldset>
    <legend>Critères de recherche</legend>
    <label for="company">Raison sociale</label>
    <input id="company" name="company_name" type="text" placeholder="Saisir une raison sociale"
           value="ACME VALEUR PRIVEE 849302" data-testid="company-field">
    <label for="wilaya">Wilaya d'inscription</label>
    <select id="wilaya" name="wilaya" data-field="wilaya-field">
      <option value="01">Sétif</option><option value="02">Alger</option>
    </select>
    <label for="person">Personne morale</label>
    <input id="person" name="personne_morale" type="checkbox" checked value="DONNEE-CHECKBOX">
    <label for="individual">Personne physique</label>
    <input id="individual" name="person_type" type="radio" value="DONNEE-RADIO">
    <label for="birth">Date de naissance</label>
    <input id="birth" name="birth_date" type="date" value="2000-01-02">
    <label for="secret">Mot de passe</label>
    <input id="secret" name="password" type="password" value="PASSWORD-ULTRA-SECRET">
    <input name="csrf_token" type="hidden" value="TOKEN-ULTRA-SECRET">
    <button type="submit" id="search-button" name="search">Rechercher ACME PRIVEE</button>
    <input type="submit" name="submit_search" value="Rechercher VALEUR PRIVEE">
  </fieldset>
</form>
"""


class DiagnosticFixtureTests(unittest.TestCase):
    def test_fixture_reports_structure_but_never_field_values_or_secrets(self) -> None:
        bundle = diagnostics_from_html_fixture(FORM_FIXTURE)
        self.assertEqual(len(bundle.forms), 1)
        form = bundle.forms[0]
        self.assertEqual(form.title, "Recherche commerciale")
        fields = {field.element_id: field for field in form.fields}
        self.assertEqual(set(fields), {"company", "wilaya", "person", "individual", "birth"})
        self.assertEqual(fields["company"].label, "Raison sociale")
        self.assertEqual(fields["company"].html_type, "text")
        self.assertEqual(fields["company"].role, "textbox")
        self.assertEqual(fields["company"].data_attributes, (("data-testid", "company-field"),))
        self.assertEqual(fields["wilaya"].option_count, 2)
        self.assertEqual(fields["wilaya"].options, ("Sétif", "Alger"))
        self.assertEqual(fields["person"].role, "checkbox")
        self.assertEqual(fields["individual"].role, "radio")
        self.assertEqual(fields["birth"].html_type, "date")
        self.assertTrue(all(field.hierarchy == ("Critères de recherche",) for field in fields.values()))
        self.assertEqual(len(bundle.buttons), 2)
        self.assertEqual(bundle.buttons[0].text, "Rechercher")
        self.assertEqual(bundle.buttons[1].text, "Texte masqué (potentiellement sensible)")

        rendered = format_diagnostic(
            SimpleNamespace(
                title="Fixture de test",
                section="Formulaire de test",
                url="https://sidjilcom.cnrc.dz/test?token=TOKEN-SECRET",
            ),
            bundle,
        )
        for private in (
            "ACME VALEUR PRIVEE", "849302", "DONNEE-CHECKBOX", "DONNEE-RADIO",
            "2000-01-02", "PASSWORD-ULTRA-SECRET", "TOKEN-ULTRA-SECRET", "VALEUR PRIVEE",
        ):
            self.assertNotIn(private, rendered)
        self.assertNotIn("TOKEN-SECRET", rendered)
        self.assertNotIn("value", repr(bundle).casefold())

    def test_readonly_datalist_and_form_name_metadata_are_reported(self) -> None:
        bundle = diagnostics_from_html_fixture(
            '<form id="criteria-form" name="criteria_filter">'
            '<label for="activity">Activité</label>'
            '<input id="activity" name="activity" list="activity-options" readonly>'
            '<datalist id="activity-options"><option label="Commerce"></datalist></form>'
        )
        form = bundle.forms[0]
        field = form.fields[0]
        self.assertEqual(form.name, "criteria_filter")
        self.assertTrue(field.readonly)
        self.assertEqual(field.list_id, "activity-options")
        self.assertEqual(field.component_type, "input avec datalist")
        self.assertEqual(field.css_selector, '[id="activity"]')
        report = format_diagnostic(SimpleNamespace(title="Fixture", section="Test", url=DEFAULT_SIDJILCOM_URL), bundle)
        self.assertIn("Name : criteria_filter", report)
        self.assertIn("Readonly : oui", report)
        self.assertIn('Sélecteur CSS : [id="activity"]', report)

    def test_sensitive_select_option_text_is_withheld(self) -> None:
        bundle = diagnostics_from_html_fixture(
            '<form><label for="email">Adresse e-mail</label><select id="email" name="email">'
            '<option>personne@example.org</option></select></form>'
        )
        field = bundle.forms[0].fields[0]
        self.assertEqual(field.option_count, 1)
        self.assertEqual(field.options, ())
        self.assertTrue(field.options_redacted)
        self.assertNotIn("personne@example.org", str(bundle))

        mixed = diagnostics_from_html_fixture(
            '<form><label for="region-ref">Wilaya et numéro de dossier</label>'
            '<select id="region-ref" name="region_ref"><option>Région privée</option></select></form>'
        )
        mixed_field = mixed.forms[0].fields[0]
        self.assertEqual(mixed_field.options, ())
        self.assertTrue(mixed_field.options_redacted)
        self.assertNotIn("Région privée", str(mixed))


class CandidateDetectionSafetyTests(unittest.TestCase):
    def test_candidate_script_reads_no_form_values_or_credentials_and_excludes_submit_reset(self) -> None:
        self.assertNotIn("element.value", SEARCH_MODE_CANDIDATES_SCRIPT)
        self.assertNotIn("target.value", SEARCH_MODE_CANDIDATES_SCRIPT)
        self.assertNotIn("option.value", SEARCH_MODE_CANDIDATES_SCRIPT)
        self.assertNotIn("getAttribute('href')", SEARCH_MODE_CANDIDATES_SCRIPT)
        self.assertNotIn("target.search", SEARCH_MODE_CANDIDATES_SCRIPT)
        self.assertNotIn("target.password", SEARCH_MODE_CANDIDATES_SCRIPT)
        self.assertNotIn("document.cookie", SEARCH_MODE_CANDIDATES_SCRIPT)
        self.assertNotIn("localStorage", SEARCH_MODE_CANDIDATES_SCRIPT)
        self.assertNotIn(".click(", SEARCH_MODE_CANDIDATES_SCRIPT)
        self.assertIn("forbiddenFormButton", SEARCH_MODE_CANDIDATES_SCRIPT)
        self.assertIn("submit", SEARCH_MODE_CANDIDATES_SCRIPT)
        self.assertIn("reset", SEARCH_MODE_CANDIDATES_SCRIPT)
        self.assertIn("normalize('NFKC')", SEARCH_MODE_CANDIDATES_SCRIPT)
        self.assertIn("toLocaleUpperCase('fr')", SEARCH_MODE_CANDIDATES_SCRIPT)
        self.assertIn("innerText", SEARCH_MODE_CANDIDATES_SCRIPT)
        self.assertIn("textContent", SEARCH_MODE_CANDIDATES_SCRIPT)
        self.assertIn("aria-label", SEARCH_MODE_CANDIDATES_SCRIPT)


class _EmptyLinks:
    def count(self) -> int:
        return 0


class BrowserDiagnosticMockTests(unittest.TestCase):
    def test_startup_auth_probe_only_navigates_to_official_protected_page(self) -> None:
        page = Mock()
        browser = PlaywrightBrowser()
        browser._page = page
        config = SessionConfig(url=DEFAULT_SIDJILCOM_URL)

        browser.probe_authenticated_route(config)

        page.goto.assert_called_once_with(
            f"https://sidjilcom.cnrc.dz{DEFAULT_ENTERPRISE_SEARCH_ROUTE}",
            wait_until="domcontentloaded",
            timeout=config.navigation_timeout_ms,
        )
        page.click.assert_not_called()
        page.fill.assert_not_called()

    def test_page_and_accessible_iframe_are_inspected_without_values(self) -> None:
        main_snapshot = {
            "forms": [{"form_id": "search", "form_title": "Recherche principale"}],
            "fields": [
                {
                    "label": "Type de personne",
                    "associated_text": "Type de personne",
                    "html_type": "select",
                    "tag_name": "select",
                    "role": "combobox",
                    "name": "person_type",
                    "id": "type-personne",
                    "option_count": 2,
                    "options": ["Personne morale", "Personne physique"],
                    "form_id": "search",
                    "form_title": "Recherche principale",
                    "hierarchy": ["body", "main#search-area", "select#type-personne"],
                },
                {
                    "label": "Recherche hors formulaire",
                    "associated_text": "Recherche hors formulaire",
                    "html_type": "div",
                    "tag_name": "div",
                    "role": "textbox",
                    "aria_label": "Recherche hors formulaire",
                    "name": "outside_query",
                    "disabled": True,
                    "visible": True,
                    "form_id": "hors-formulaire",
                    "form_title": "Hors formulaire",
                },
            ],
            "buttons": [{"text": "Rechercher dossier privé", "html_type": "submit", "tag_name": "button"}],
            "clickables": (
                [
                    {"text": f"Lien navigation {index}", "tag_name": "a", "role": "link", "href": f"/fr/navigation/{index}"}
                    for index in range(1, 30)
                ]
                + [
                    {
                        "text": "Personne physique", "tag_name": "a", "role": "link",
                        "id": "entry-30", "class_name": "mode-choice physical-choice",
                        "aria_label": "Personne physique",
                        "href": "/fr/repertoire/personne-physique?token=PHYSICAL-TOKEN",
                    },
                    {
                        "text": "Personne morale", "tag_name": "a", "role": "link",
                        "id": "entry-31", "class_name": "mode-choice legal-choice",
                        "aria_label": "Personne morale",
                        "href": "/fr/repertoire/personne-morale?session=LEGAL-SESSION",
                    },
                ]
            ),
        }
        iframe_snapshot = {
            "forms": [],
            "fields": [
                {
                    "label": "Recherche dans frame",
                    "aria_label": "Recherche dans frame",
                    "html_type": "div",
                    "tag_name": "div",
                    "role": "textbox",
                    "id": "frame-query",
                    "form_id": "hors-formulaire",
                    "form_title": "Hors formulaire",
                }
            ],
            "buttons": [],
            "clickables": [],
        }
        main_frame = _MockFrame("", "https://sidjilcom.cnrc.dz/fr/group/sidjilcom/repertoire-des-commercants", main_snapshot)
        iframe = _MockFrame("registry-frame", "https://sidjilcom.cnrc.dz/fr/embedded?token=FRAME-SECRET", iframe_snapshot)
        page = Mock()
        page.url = "https://sidjilcom.cnrc.dz/fr/group/sidjilcom/repertoire-des-commercants?token=TOKEN-SECRET#private"
        page.title.return_value = "Trouver une entreprise"
        page.frames = [main_frame, iframe]
        page.get_by_role.return_value = _EmptyLinks()

        browser = PlaywrightBrowser()
        browser._page = page
        diagnostics = browser.diagnostics(SessionConfig(url=DEFAULT_SIDJILCOM_URL))

        self.assertEqual(len(diagnostics.form_diagnostics.frames), 2)
        self.assertTrue(all(frame.accessible for frame in diagnostics.form_diagnostics.frames))
        self.assertNotIn("token=", diagnostics.url)
        self.assertNotIn("TOKEN-SECRET", diagnostics.report)
        self.assertNotIn("FRAME-SECRET", diagnostics.report)
        self.assertNotIn("#private", diagnostics.report)
        self.assertIn("Recherche principale", diagnostics.report)
        self.assertIn("Recherche hors formulaire", diagnostics.report)
        self.assertIn("Recherche dans frame", diagnostics.report)
        self.assertIn("Personne morale", diagnostics.report)
        self.assertIn("main#search-area", diagnostics.report)
        self.assertIn("Rechercher", diagnostics.report)
        self.assertIn("FRAMES", diagnostics.report)
        self.assertIn("CONTRÔLES HORS FORMULAIRE", diagnostics.report)
        self.assertIn("ÉLÉMENTS POTENTIELLEMENT CLIQUABLES", diagnostics.report)
        self.assertIn("Élément cliquable 30", diagnostics.report)
        self.assertIn("Texte : Personne physique", diagnostics.report)
        self.assertIn("href : https://sidjilcom.cnrc.dz/fr/repertoire/personne-physique", diagnostics.report)
        self.assertIn("Class : mode-choice physical-choice", diagnostics.report)
        self.assertIn("Texte : Personne morale", diagnostics.report)
        self.assertNotIn("PHYSICAL-TOKEN", diagnostics.report)
        self.assertNotIn("LEGAL-SESSION", diagnostics.report)
        self.assertIn("SELECTS", diagnostics.report)
        self.assertIn("INPUTS", diagnostics.report)
        self.assertIn("TEXTAREAS", diagnostics.report)
        self.assertNotIn("dossier privé", diagnostics.report)
        script = main_frame.script
        self.assertNotIn("element.value", script)
        self.assertNotIn("getAttribute('value')", script)
        self.assertNotIn("option.value", script)
        self.assertIn("getAttribute('type')", script)
        self.assertIn("required: !!element.required", script)
        self.assertIn("readonly: !!element.readOnly", script)
        self.assertIn("list_id: listId", script)
        self.assertIn("dataList?.options.length", script)
        self.assertIn("aria-readonly", script)
        self.assertIn("form_name: form.name", script)
        self.assertIn("scope_info: scopeInfo", script)
        self.assertIn("form_action: form.action", script)
        self.assertIn("form.getAttribute('method')", script)
        self.assertIn("onclick_present", script)
        self.assertIn("mayReadOptionText(label)", script)
        self.assertIn("[role=", script)
        self.assertNotIn("fetch(", script)
        self.assertNotIn("XMLHttpRequest", script)
        self.assertNotIn("document.cookie", script)
        self.assertNotIn("localStorage", script)
        diagnostic_source = inspect.getsource(PlaywrightBrowser.diagnostics)
        self.assertNotIn(".click(", diagnostic_source)
        self.assertNotIn(".fill(", diagnostic_source)
        self.assertNotIn(".press(", diagnostic_source)
        self.assertNotIn("submit()", diagnostic_source)


# Ces candidats sont des fixtures simulées; aucun DOM réel ni compte Sidjilcom n'est utilisé.
class _ModeLocator:
    def __init__(self, frame: "_ModeFrame", selector: str):
        self.frame = frame
        self.selector = selector

    def _record(self) -> dict[str, object] | None:
        for label in self.frame.available_modes:
            for candidate in self.frame.mode_candidates[label]:
                if candidate["selector"] == self.selector:
                    return candidate
        return None

    def count(self) -> int:
        return int(self._record() is not None)

    def is_visible(self) -> bool:
        record = self._record()
        return bool(record and record["visible"])

    def is_enabled(self) -> bool:
        record = self._record()
        return bool(record and record["enabled"])

    def click(self, *, timeout: int) -> None:
        record = self._record()
        if record is None or not self.is_visible() or not self.is_enabled():
            raise RuntimeError("fixture locator not available")
        label = str(record["label"])
        self.frame.clicks.append(label)
        self.frame.mode = label


class _ModeFrame:
    name = ""
    url = f"https://sidjilcom.cnrc.dz{DEFAULT_ENTERPRISE_SEARCH_ROUTE}"

    @staticmethod
    def _candidate(label: str, index: int, **overrides: object) -> dict[str, object]:
        record: dict[str, object] = {
            "label": label,
            "text": label,
            "text_source": "texte visible",
            "tag_name": "a",
            "href": f"https://sidjilcom.cnrc.dz{DEFAULT_ENTERPRISE_SEARCH_ROUTE}",
            "href_allowed": True,
            "has_href_parameters": False,
            "visible": True,
            "enabled": True,
            "clickable": True,
            "selector": f"a:nth-of-type({index})",
            "parent": "nav.mode-choice",
            "context": "conteneur partagé nav.mode-choice",
            "paired_context": True,
            "in_portlet": False,
        }
        record.update(overrides)
        return record

    def __init__(self) -> None:
        self.clicks: list[str] = []
        self.stability_waits = 0
        self.portlet_only_flags: list[bool] = []
        self.mode = ""
        self.hide_legal_after_physical = False
        self.mode_candidates: dict[str, list[dict[str, object]]] = {
            SEARCH_MODE_LABELS[0]: [self._candidate(SEARCH_MODE_LABELS[0], 1)],
            SEARCH_MODE_LABELS[1]: [self._candidate(SEARCH_MODE_LABELS[1], 2)],
        }
        self.snapshots = {
            "PERSONNES PHYSIQUES": {
                "scope_found": True,
                "scope_info": {"tag_name": "div", "id": "physical-portlet", "class_name": "RechercheDetailleePortlet"},
                "forms": [{"form_id": "physical", "form_name": "physical_form", "form_title": "Recherche physique", "method": "POST"}],
                "fields": [{"label": "Numéro d'inscription", "name": "registration_number", "id": "physical-id",
                            "html_type": "text", "required": True, "form_id": "physical",
                            "form_title": "Recherche physique"}],
                "buttons": [{"text": "Rechercher", "tag_name": "button", "html_type": "submit"}],
                "clickables": [],
            },
            "PERSONNES MORALES": {
                "scope_found": True,
                "scope_info": {"tag_name": "div", "id": "legal-portlet", "class_name": "RechercheDetailleePortlet"},
                "forms": [{"form_id": "legal", "form_name": "legal_form", "form_title": "Recherche morale", "method": "GET"}],
                "fields": [{"label": "Raison sociale", "name": "company_name", "id": "legal-id",
                            "html_type": "text", "required": True, "form_id": "legal",
                            "form_title": "Recherche morale"}],
                "buttons": [{"text": "Rechercher", "tag_name": "button", "html_type": "submit"}],
                "clickables": [],
            },
        }

    @property
    def available_modes(self) -> tuple[str, ...]:
        available = []
        for label, candidates in self.mode_candidates.items():
            if label == SEARCH_MODE_LABELS[1] and self.hide_legal_after_physical and self.mode == SEARCH_MODE_LABELS[0]:
                continue
            if candidates:
                available.append(label)
        return tuple(available)

    def locator(self, selector: str) -> _ModeLocator:
        return _ModeLocator(self, selector)

    def evaluate(self, script: str, *args: object) -> object:
        if script == SEARCH_MODE_CANDIDATES_SCRIPT:
            return [
                dict(candidate)
                for label in self.available_modes
                for candidate in self.mode_candidates[label]
            ]
        if "MutationObserver" in script:
            self.stability_waits += 1
            return True
        if args and isinstance(args[0], dict):
            self.portlet_only_flags.append(args[0].get("portletOnly") is True)
        return self.snapshots[self.mode]


class SearchModeTextNormalizationTests(unittest.TestCase):
    def test_exact_text_multiple_whitespace_case_and_nbsp_normalize_identically(self) -> None:
        for text in (
            "PERSONNES PHYSIQUES",
            "PERSONNES   PHYSIQUES",
            " personnes\n physiques ",
            "Personnes" + chr(160) + "Physiques",
        ):
            with self.subTest(text=text):
                self.assertEqual(normalize_search_mode_label(text), "personnes physiques")
        self.assertNotEqual(normalize_search_mode_label("PERSONNES physiques complémentaires"), "personnes physiques")
        self.assertNotEqual(normalize_search_mode_label("PERSONNES"), "personnes physiques")
        self.assertNotEqual(normalize_search_mode_label("PHYSIQUES"), "personnes physiques")
        self.assertNotEqual(normalize_search_mode_label("MORALES"), "personnes morales")

    def test_text_spread_across_fixture_html_children_is_normalized(self) -> None:
        class VisibleText(HTMLParser):
            def __init__(self) -> None:
                super().__init__(convert_charrefs=True)
                self.links: list[str] = []
                self.current: list[str] | None = None

            def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                if tag == "a" and self.current is None:
                    self.current = []

            def handle_endtag(self, tag: str) -> None:
                if tag == "a" and self.current is not None:
                    self.links.append("".join(self.current))
                    self.current = None

            def handle_data(self, data: str) -> None:
                if self.current is not None:
                    self.current.append(data)

        fixture = Path(__file__).parent / "fixtures" / "sidjilcom_mode_choices.html"
        parser = VisibleText()
        parser.feed(fixture.read_text(encoding="utf-8"))
        self.assertIn("personnes physiques", [normalize_search_mode_label(text) for text in parser.links])


class SearchModeAnalysisTests(unittest.TestCase):
    def _browser(self, frame: _ModeFrame) -> PlaywrightBrowser:
        page = Mock()
        page.url = frame.url
        page.title.return_value = "Trouver une entreprise"
        page.frames = [frame]
        page.main_frame = frame
        page.get_by_role.return_value = _EmptyLinks()
        browser = PlaywrightBrowser()
        browser._page = page
        return browser

    @staticmethod
    def _candidate(label: str, frame: object, selector: str, **overrides: object) -> _SearchModeCandidate:
        values: dict[str, object] = {
            "label": label,
            "frame": frame,
            "selector": selector,
            "text": label,
            "text_source": "texte visible",
            "tag_name": "a",
            "href": f"https://sidjilcom.cnrc.dz{DEFAULT_ENTERPRISE_SEARCH_ROUTE}",
            "href_allowed": True,
            "has_href_parameters": False,
            "visible": True,
            "enabled": True,
            "clickable": True,
            "parent": "nav.mode-choice",
            "context": "conteneur partagé nav.mode-choice",
            "paired_context": True,
            "in_portlet": False,
            "frame_name": "Document principal",
        }
        values.update(overrides)
        return _SearchModeCandidate(**values)

    def test_simultaneous_modes_are_clicked_once_and_diagnostics_are_reported(self) -> None:
        frame = _ModeFrame()
        browser = self._browser(frame)

        result = browser.diagnose_search_modes(SessionConfig(url=DEFAULT_SIDJILCOM_URL))

        self.assertEqual(frame.clicks, ["PERSONNES PHYSIQUES", "PERSONNES MORALES"])
        self.assertEqual(frame.stability_waits, 2)
        self.assertEqual(frame.portlet_only_flags, [True, True])
        self.assertIn("===== PERSONNES PHYSIQUES =====", result.report)
        self.assertIn("===== PERSONNES MORALES =====", result.report)
        self.assertIn("PORTLET PARENT", result.report)
        self.assertIn("physical-portlet", result.report)
        self.assertIn("Name : physical_form", result.report)
        self.assertIn("Spécifiques physiques : registration_number", result.report)
        self.assertIn("Spécifiques morales : company_name", result.report)
        self.assertIn("aucun bouton de recherche activé", result.report)
        self.assertIn("CANDIDATS PERSONNES PHYSIQUES : 1", result.report)
        self.assertIn("CANDIDATS PERSONNES MORALES : 1", result.report)
        self.assertIn("raison du choix", result.report)
        self.assertNotIn(".click(", inspect.getsource(PlaywrightBrowser.diagnostics))

    def test_candidates_are_not_restricted_to_the_original_portlet_marker(self) -> None:
        frame = _ModeFrame()
        browser = self._browser(frame)
        result = browser.diagnose_search_modes(SessionConfig(url=DEFAULT_SIDJILCOM_URL))
        self.assertIn("CANDIDATS PERSONNES PHYSIQUES : 1", result.report)
        self.assertTrue(all(not item["in_portlet"] for records in frame.mode_candidates.values() for item in records))

    def test_multiple_candidates_prefer_visible_paired_and_internal_link(self) -> None:
        frame = object()
        label = SEARCH_MODE_LABELS[0]
        candidates = [
            self._candidate(label, frame, "a:nth-of-type(1)", visible=False, paired_context=True),
            self._candidate(label, frame, "a:nth-of-type(2)", paired_context=False,
                            parent="nav.utility", context="parent proche nav.utility"),
            self._candidate(label, frame, "a:nth-of-type(3)", paired_context=True,
                            parent="nav.mode-choice", context="conteneur partagé nav.mode-choice"),
        ]
        chosen, reason = PlaywrightBrowser._choose_search_mode_candidate(candidates)
        self.assertEqual(chosen.selector, "a:nth-of-type(3)")
        self.assertIn("préféré parmi 3 candidats", reason)
        report = PlaywrightBrowser._format_mode_candidate_report(
            {SEARCH_MODE_LABELS[0]: candidates, SEARCH_MODE_LABELS[1]: []},
            {SEARCH_MODE_LABELS[0]: (chosen, reason), SEARCH_MODE_LABELS[1]: (None, "absent")},
        )
        self.assertIn("CANDIDATS PERSONNES PHYSIQUES : 3", report)
        self.assertIn("visible=non", report)

    def test_different_internal_hrefs_without_distinguishing_context_are_reported_ambiguous(self) -> None:
        frame = object()
        label = SEARCH_MODE_LABELS[1]
        candidates = [
            self._candidate(label, frame, "a:nth-of-type(1)", href="https://sidjilcom.cnrc.dz/mode/one",
                            paired_context=False, parent="div.choices", context="parent proche div.choices"),
            self._candidate(label, frame, "a:nth-of-type(2)", href="https://sidjilcom.cnrc.dz/mode/two",
                            paired_context=False, parent="div.choices", context="parent proche div.choices"),
        ]
        chosen, reason = PlaywrightBrowser._choose_search_mode_candidate(candidates)
        self.assertIsNone(chosen)
        self.assertIn("href ou contexte DOM distinct", reason)
        report = PlaywrightBrowser._format_mode_candidate_report(
            {SEARCH_MODE_LABELS[0]: [], SEARCH_MODE_LABELS[1]: candidates},
            {SEARCH_MODE_LABELS[0]: (None, "absent"), SEARCH_MODE_LABELS[1]: (chosen, reason)},
        )
        self.assertIn("CANDIDATS PERSONNES MORALES : 2", report)
        self.assertIn("mode/one", report)
        self.assertIn("mode/two", report)

    def test_absent_both_modes_reports_zero_candidates_and_clicks_nothing(self) -> None:
        frame = _ModeFrame()
        frame.mode_candidates = {label: [] for label in SEARCH_MODE_LABELS}
        browser = self._browser(frame)

        with self.assertRaises(SearchModeAnalysisError) as caught:
            browser.diagnose_search_modes(SessionConfig(url=DEFAULT_SIDJILCOM_URL))

        self.assertEqual(frame.clicks, [])
        self.assertIn("MODE PERSONNES PHYSIQUES", str(caught.exception))
        self.assertIn("MODE PERSONNES MORALES", str(caught.exception))
        self.assertIn("CANDIDATS PERSONNES PHYSIQUES : 0", str(caught.exception))
        self.assertIn("CANDIDATS PERSONNES MORALES : 0", str(caught.exception))

    def test_second_mode_reloads_initial_route_if_first_selection_hides_its_link(self) -> None:
        frame = _ModeFrame()
        frame.hide_legal_after_physical = True
        browser = self._browser(frame)
        browser._page.goto.side_effect = lambda *_args, **_kwargs: setattr(frame, "mode", "")

        result = browser.diagnose_search_modes(SessionConfig(url=DEFAULT_SIDJILCOM_URL))

        self.assertEqual(frame.clicks, ["PERSONNES PHYSIQUES", "PERSONNES MORALES"])
        self.assertEqual(browser._page.goto.call_count, 1)
        self.assertIn("===== PERSONNES MORALES =====", result.report)

    def test_submit_and_reset_buttons_are_not_clickable_mode_candidates(self) -> None:
        label = SEARCH_MODE_LABELS[0]
        candidate = self._candidate(label, object(), "button:nth-of-type(1)",
                                    tag_name="button", clickable=False)
        chosen, reason = PlaywrightBrowser._choose_search_mode_candidate([candidate])
        self.assertIsNone(chosen)
        self.assertIn("visible, activé, cliquable", reason)

    def test_external_href_candidates_are_not_clicked(self) -> None:
        frame = _ModeFrame()
        for records in frame.mode_candidates.values():
            for record in records:
                record["href"] = "https://example.org/[chemin masqué]"
                record["href_allowed"] = False
        browser = self._browser(frame)
        with self.assertRaises(SearchModeAnalysisError) as caught:
            browser.diagnose_search_modes(SessionConfig(url=DEFAULT_SIDJILCOM_URL))
        self.assertEqual(frame.clicks, [])
        self.assertNotIn("token", str(caught.exception).casefold())

    def test_href_parameters_are_never_included_in_candidate_report(self) -> None:
        frame = _ModeFrame()
        frame.mode_candidates[SEARCH_MODE_LABELS[0]][0]["has_href_parameters"] = True
        browser = self._browser(frame)
        result = browser.diagnose_search_modes(SessionConfig(url=DEFAULT_SIDJILCOM_URL))
        self.assertIn("paramètres/fragment omis", result.report)
        self.assertNotIn("?token", result.report.casefold())

    def test_accessible_child_text_and_candidate_counts_appear_in_report(self) -> None:
        frame = _ModeFrame()
        frame.mode_candidates[SEARCH_MODE_LABELS[0]][0]["text_source"] = "texte des éléments enfants"
        frame.mode_candidates[SEARCH_MODE_LABELS[0]][0]["text"] = "Personnes   Physiques"
        browser = self._browser(frame)
        result = browser.diagnose_search_modes(SessionConfig(url=DEFAULT_SIDJILCOM_URL))
        self.assertIn("Personnes Physiques", result.report)
        self.assertIn("source du texte : texte des éléments enfants", result.report)
        self.assertIn("CANDIDATS PERSONNES PHYSIQUES : 1", result.report)


class _MockFrame:
    def __init__(self, name: str, url: str, snapshot: dict[str, object]):
        self.name = name
        self.url = url
        self.snapshot = snapshot
        self.script = ""

    def evaluate(self, script: str) -> dict[str, object]:
        self.script = script
        return self.snapshot


class _FailingFrame:
    name = "Frame inaccessible"
    url = "https://sidjilcom.cnrc.dz/fr/embed?token=FRAME-TOKEN"

    def evaluate(self, _script: str) -> object:
        raise RuntimeError("sensitive browser exception FRAME-TOKEN")


class InaccessibleFrameTests(unittest.TestCase):
    def test_frame_errors_are_summarized_without_exception_details(self) -> None:
        page = Mock()
        page.url = DEFAULT_SIDJILCOM_URL
        page.title.return_value = "Sidjilcom"
        page.get_by_role.return_value = _EmptyLinks()
        page.frames = [
            _MockFrame("", DEFAULT_SIDJILCOM_URL, {"forms": [], "fields": [], "buttons": [], "clickables": []}),
            _FailingFrame(),
        ]
        browser = PlaywrightBrowser()
        browser._page = page

        result = browser.diagnostics(SessionConfig(url=DEFAULT_SIDJILCOM_URL))
        inaccessible = result.form_diagnostics.frames[1]
        self.assertFalse(inaccessible.accessible)
        self.assertIn("inaccessible", inaccessible.status)
        self.assertNotIn("FRAME-TOKEN", result.report)
        self.assertIn("inaccessible/non disponible", result.report)


class DiagnosticDOMFixtureTests(unittest.TestCase):
    def test_fixture_detects_form_outside_controls_aria_frames_and_disabled_state(self) -> None:
        fixture_path = Path(__file__).parent / "fixtures" / "sidjilcom_page_controls.html"
        html = fixture_path.read_text(encoding="utf-8")
        bundle = diagnostics_from_html_fixture(html)
        controls = {field.element_id: field for field in bundle.all_controls}

        self.assertIn("company-name", controls)
        self.assertEqual(controls["company-name"].associated_text, "Raison sociale")
        self.assertIn("outside-search", controls)
        self.assertEqual(controls["outside-search"].aria_label, "Recherche hors formulaire")
        self.assertEqual(
            controls["outside-search"].data_attributes,
            (("data-automation-id", "outside-search-control"),),
        )
        self.assertIn("labelled-control", controls)
        self.assertEqual(controls["labelled-control"].aria_labelledby, "outside-label")
        self.assertEqual(controls["custom-combo"].role, "combobox")
        self.assertEqual(controls["custom-textbox"].role, "textbox")
        self.assertTrue(controls["labelled-control"].disabled)
        self.assertFalse(controls["hidden-helper"].visible)
        self.assertTrue(controls["wilaya"].disabled)
        self.assertIn("comments", controls)
        self.assertTrue(controls["comments"].disabled)
        self.assertTrue(any(frame.name == "Portail intégré" for frame in bundle.frames))
        self.assertTrue(any(not frame.accessible for frame in bundle.frames if frame.name == "Portail intégré"))
        self.assertEqual(len(bundle.outside_controls), 6)
        links = {element.element_id: element for element in bundle.clickables}
        self.assertEqual(links["mode-physical"].text, "Personne physique")
        self.assertEqual(links["mode-physical"].href, "https://sidjilcom.cnrc.dz/fr/search/physical")
        self.assertEqual(links["mode-physical"].class_name, "mode-choice physical-choice")
        self.assertEqual(links["mode-physical"].role, "link")
        self.assertEqual(links["mode-physical"].data_attributes, (("data-qa", "mode-physical"),))
        interactive_buttons = {button.element_id: button for button in bundle.buttons}
        self.assertTrue(interactive_buttons["custom-button"].disabled)
        self.assertTrue(interactive_buttons["custom-button"].visible)
        self.assertEqual(interactive_buttons["custom-button"].class_name, "search-mode")
        self.assertEqual(links["mode-legal"].text, "Personne morale")
        self.assertEqual(links["mode-legal"].href, "https://sidjilcom.cnrc.dz/fr/search/legal")

        report = format_diagnostic(
            SimpleNamespace(title="Fixture", section="Test", url=DEFAULT_SIDJILCOM_URL), bundle
        )
        self.assertIn("aria-label", report)
        self.assertIn("aria-labelledby", report)
        self.assertIn("Hiérarchie DOM", report)
        for secret in (
            "VALEUR PRIVEE", "918273", "DONNEE EXTERNE PRIVEE", "SECRET INPUT VALUE",
            "NE PAS LIRE", "MOT-DE-PASSE-SECRET", "JETON-CSRF-SECRET", "FRAME-SECRET",
            "NE-PAS-COPIER", "HIDDEN FIELD VALUE MUST NOT APPEAR", "PHYSICAL-TOKEN", "LEGAL-SESSION",
        ):
            self.assertNotIn(secret, report)
        self.assertNotIn("access_token=", report)

    def test_persons_modes_compare_form_metadata_without_values_or_secrets(self) -> None:
        fixture_dir = Path(__file__).parent / "fixtures"
        physical = diagnostics_from_html_fixture(
            (fixture_dir / "sidjilcom_personnes_physiques.html").read_text(encoding="utf-8")
        )
        legal = diagnostics_from_html_fixture(
            (fixture_dir / "sidjilcom_personnes_morales.html").read_text(encoding="utf-8")
        )
        physical_fields = {field.element_id: field for field in physical.all_controls}
        legal_fields = {field.element_id: field for field in legal.all_controls}

        self.assertTrue(physical_fields["physical-registration"].required)
        self.assertEqual(physical_fields["physical-registration"].name, "registration_number")
        self.assertEqual(physical_fields["physical-wilaya"].component_type, "Select2")
        self.assertFalse(physical_fields["physical-wilaya"].visible)
        self.assertTrue(
            any(field.role == "combobox" and field.label == "Wilaya" and field.visible for field in physical.all_controls)
        )
        self.assertEqual(
            physical_fields["physical-wilaya"].ajax_endpoint,
            "https://sidjilcom.cnrc.dz/fr/api/wilayas",
        )
        self.assertEqual(physical_fields["physical-wilaya"].ajax_method, "GET")
        self.assertIn("Commerce de détail", physical_fields["physical-activity"].options)
        self.assertEqual(physical_fields["physical-commune"].component_type, "autocomplete / liste dynamique")
        self.assertTrue(physical_fields["physical-commune"].required)
        self.assertIn(
            ("data-dependent-on", "physical-wilaya"),
            physical_fields["physical-commune"].data_attributes,
        )
        self.assertNotIn(("data-value", "PRIVATE QUERY"), physical_fields["physical-commune"].data_attributes)
        self.assertEqual(physical.forms[0].method, "POST")
        self.assertEqual(physical.forms[0].action, "https://sidjilcom.cnrc.dz/fr/search/physical")
        self.assertEqual(legal.forms[0].method, "GET")
        self.assertEqual(legal.forms[0].action, "https://sidjilcom.cnrc.dz/fr/search/legal")
        self.assertEqual(legal_fields["legal-form"].options, ("SPA", "SARL"))
        self.assertEqual(legal_fields["legal-status"].options, ("Actif", "Radié"))
        self.assertEqual(legal_fields["legal-date-to"].component_type, "composant JavaScript date / période")

        physical_buttons = {button.element_id: button for button in physical.buttons}
        legal_buttons = {button.element_id: button for button in legal.buttons}
        self.assertEqual(physical_buttons["physical-search-button"].form_id, "physical-search")
        self.assertEqual(
            physical_buttons["physical-search-button"].form_action,
            "https://sidjilcom.cnrc.dz/fr/search/physical",
        )
        self.assertEqual(physical_buttons["physical-search-button"].form_method, "POST")
        self.assertTrue(physical_buttons["physical-search-button"].onclick_present)
        self.assertEqual(physical_buttons["physical-search-button"].onclick_handler, "code masqué")
        self.assertEqual(physical_buttons["physical-reset-button"].onclick_handler, "clearSearch()")
        self.assertEqual(legal_buttons["legal-search-button"].form_method, "GET")

        report = format_search_mode_comparison(physical, legal)
        self.assertIn("COMPARAISON DES MODES", report)
        self.assertIn("Spécifiques physiques", report)
        self.assertIn("Spécifiques morales", report)
        self.assertIn("id=physical-search, name=— [POST · https://sidjilcom.cnrc.dz/fr/search/physical]", report)
        self.assertIn("Bouton(s) Rechercher physiques : Rechercher", report)
        self.assertIn("Bouton(s) Réinitialiser physiques : Réinitialiser", report)
        complete_report = "\n".join(
            (format_diagnostic(SimpleNamespace(title="Fixture", section="Test", url=DEFAULT_SIDJILCOM_URL), physical),
             format_diagnostic(SimpleNamespace(title="Fixture", section="Test", url=DEFAULT_SIDJILCOM_URL), legal),
             report)
        )
        for private in (
            "PERSONNELLE FIXTURE", "ENTREPRISE PRIVEE", "DIRIGEANT PRIVE", "99887766",
            "FIXTURE-TOKEN", "WILAYA-TOKEN", "COMMUNE-TOKEN", "FIXTURE-SESSION", "PRIVATE QUERY",
            "PASSWORD-FIXTURE-SECRET", "CSRF-FIXTURE-SECRET", "submitSearch('PRIVATE')",
            "clearSearch(this.form)",
        ):
            self.assertNotIn(private, complete_report)
        self.assertNotIn("?token=", complete_report)
        self.assertNotIn("?session=", complete_report)

    def test_accessible_iframe_html_fixture_can_be_inspected_as_its_own_document(self) -> None:
        fixture_path = Path(__file__).parent / "fixtures" / "sidjilcom_iframe_content.html"
        bundle = diagnostics_from_html_fixture(
            fixture_path.read_text(encoding="utf-8"),
            frame_name="Frame recherche",
            frame_url="https://sidjilcom.cnrc.dz/fr/embedded?session=FRAME-SECRET",
        )
        self.assertEqual(bundle.forms[0].frame_name, "Frame recherche")
        self.assertIn("frame-query", {field.element_id for field in bundle.all_controls})
        self.assertNotIn("FRAME-SECRET", str(bundle))


if __name__ == "__main__":
    unittest.main()
