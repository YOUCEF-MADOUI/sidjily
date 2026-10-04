from __future__ import annotations

import unittest
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


class _MockLocator:
    def __init__(self, payload: dict[str, object]):
        self.payload = payload
        self.script = ""

    def evaluate_all(self, script: str) -> dict[str, object]:
        self.script = script
        return self.payload


class BrowserDiagnosticMockTests(unittest.TestCase):
    def test_browser_diagnostic_scrubs_url_and_uses_non_value_dom_snapshot(self) -> None:
        locator = _MockLocator(
            {
                "fields": [
                    {
                        "label": "Type de personne",
                        "associated_text": "Type de personne",
                        "html_type": "select",
                        "name": "person_type",
                        "id": "type-personne",
                        "option_count": 2,
                        "options": ["Personne morale", "Personne physique"],
                        "form_id": "lookup",
                        "form_title": "Recherche",
                    }
                ],
                "buttons": [{"text": "Rechercher dossier privé", "html_type": "submit"}],
            }
        )
        page = Mock()
        page.url = "https://sidjilcom.cnrc.dz/fr/group/sidjilcom/repertoire-des-commercants?token=TOKEN-SECRET#private"
        page.title.return_value = "Trouver une entreprise"
        page.locator.return_value = locator
        page.get_by_role.return_value = _EmptyLinks()

        browser = PlaywrightBrowser()
        browser._page = page
        diagnostics = browser.diagnostics(SessionConfig(url=DEFAULT_SIDJILCOM_URL))

        self.assertNotIn("token=", diagnostics.url)
        self.assertNotIn("TOKEN-SECRET", diagnostics.report)
        self.assertNotIn("#private", diagnostics.report)
        self.assertIn("Type de personne", diagnostics.report)
        self.assertIn("Personne morale", diagnostics.report)
        self.assertIn("Rechercher", diagnostics.report)
        self.assertNotIn("dossier privé", diagnostics.report)
        self.assertNotIn(".value", locator.script)
        self.assertNotIn("getAttribute('value')", locator.script)
        self.assertIn("getAttribute('type')", locator.script)
        self.assertIn("mayReadOptionText(label)", locator.script)
        self.assertNotIn("document.cookie", locator.script)
        self.assertNotIn("localStorage", locator.script)


if __name__ == "__main__":
    unittest.main()
