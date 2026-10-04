from __future__ import annotations

import inspect
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from sidjily.sidjilcom.browser import PlaywrightBrowser
from sidjily.sidjilcom.config import DEFAULT_SIDJILCOM_URL, SessionConfig
from sidjily.sidjilcom.diagnostics import diagnostics_from_html_fixture, format_diagnostic


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
        self.assertEqual(bundle.buttons[1].text, "Texte masqué (action non classifiée)")

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


class _EmptyLinks:
    def count(self) -> int:
        return 0


class BrowserDiagnosticMockTests(unittest.TestCase):
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
            "clickables": [{"text": "Voir details confidentiels", "tag_name": "a", "role": "link", "nature": "élément cliquable"}],
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
        self.assertIn("SELECTS", diagnostics.report)
        self.assertIn("INPUTS", diagnostics.report)
        self.assertIn("TEXTAREAS", diagnostics.report)
        self.assertNotIn("dossier privé", diagnostics.report)
        script = main_frame.script
        self.assertNotIn("element.value", script)
        self.assertNotIn("getAttribute('value')", script)
        self.assertIn("getAttribute('type')", script)
        self.assertIn("mayReadOptionText(label)", script)
        self.assertIn("[role=", script)
        self.assertNotIn("document.cookie", script)
        self.assertNotIn("localStorage", script)
        diagnostic_source = inspect.getsource(PlaywrightBrowser.diagnostics)
        self.assertNotIn(".click(", diagnostic_source)
        self.assertNotIn(".fill(", diagnostic_source)
        self.assertNotIn(".press(", diagnostic_source)
        self.assertNotIn("submit()", diagnostic_source)


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

        report = format_diagnostic(
            SimpleNamespace(title="Fixture", section="Test", url=DEFAULT_SIDJILCOM_URL), bundle
        )
        self.assertIn("aria-label", report)
        self.assertIn("aria-labelledby", report)
        self.assertIn("Hiérarchie DOM", report)
        for secret in (
            "VALEUR PRIVEE", "918273", "DONNEE EXTERNE PRIVEE", "SECRET INPUT VALUE",
            "NE PAS LIRE", "MOT-DE-PASSE-SECRET", "JETON-CSRF-SECRET", "FRAME-SECRET",
            "NE-PAS-COPIER", "HIDDEN FIELD VALUE MUST NOT APPEAR",
        ):
            self.assertNotIn(secret, report)
        self.assertNotIn("access_token=", report)

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
