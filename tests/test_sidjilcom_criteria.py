from __future__ import annotations

import unittest

from sidjily.sidjilcom.criteria import (
    ComponentType,
    CommonSearchCriteria,
    ControlMappingError,
    CriteriaValidationError,
    NUMERO_INSCRIPTION_SEMANTICS_NOTICE,
    NumeroInscriptionCriteria,
    PersonneMoraleCriteria,
    PersonnePhysiqueCriteria,
    REPORTED_CONTROL_COUNTS,
    REPORTED_FORM_METHOD,
    REPORTED_UNCLASSIFIED_CONTROL_COUNTS,
    SIDJILCOM_CONTROL_MAP,
    SearchCriteria,
    SearchMode,
    criteria_from_mapping,
    criteria_to_mapping,
    format_criteria_preview,
    map_criteria_to_controls,
    numero_inscription_is_complete,
    resolve_control_name,
    validate_search_criteria,
)


class SearchCriteriaModelTests(unittest.TestCase):
    def test_physical_mode_keeps_physical_fields_separate(self) -> None:
        criteria = SearchCriteria(
            mode=SearchMode.PERSONNE_PHYSIQUE,
            common=CommonSearchCriteria(commune_wilaya="Sétif"),
            physique=PersonnePhysiqueCriteria(nom="A", prenom="B", nationalite="DZ"),
        )
        validate_search_criteria(criteria)
        payload = criteria_to_mapping(criteria)
        self.assertEqual(payload["mode"], "PERSONNE_PHYSIQUE")
        self.assertEqual(payload["nom"], "A")
        self.assertNotIn("raison_sociale", payload)
        mapped = map_criteria_to_controls(criteria)
        self.assertTrue(all(not item.automation_allowed for item in mapped))
        self.assertEqual({item.criterion for item in mapped}, {
            "commune_wilaya", "physique.nom", "physique.prenom", "physique.nationalite",
        })

    def test_moral_mode_keeps_moral_fields_separate(self) -> None:
        criteria = SearchCriteria(
            mode=SearchMode.PERSONNE_MORALE,
            morale=PersonneMoraleCriteria(
                raison_sociale="Société exemple",
                nom_prenom_dirigeant="Exemple A",
                date_naissance_dirigeant="1980-02-29",
                nationalite="DZ",
            ),
        )
        validate_search_criteria(criteria)
        payload = criteria_to_mapping(criteria)
        self.assertEqual(payload["mode"], "PERSONNE_MORALE")
        self.assertEqual(payload["raison_sociale"], "Société exemple")
        self.assertNotIn("nom", payload)
        self.assertFalse(any(item.criterion.startswith("physique.") for item in map_criteria_to_controls(criteria)))

    def test_preview_is_local_and_round_trip_preserves_typed_criteria(self) -> None:
        criteria = criteria_from_mapping({
            "mode": SearchMode.PERSONNE_PHYSIQUE.value,
            "nom": "  Exemple  ",
        })
        serialized = criteria_to_mapping(criteria)
        self.assertEqual(criteria_from_mapping(serialized), criteria)
        preview = format_criteria_preview(criteria)
        self.assertIn("Mode : Personne physique", preview)
        self.assertIn("Nom : Exemple", preview)
        self.assertIn("Aucune requête Sidjilcom n'a été envoyée.", preview)

    def test_empty_criteria_are_valid_for_each_required_mode(self) -> None:
        for mode, specific in (
            (SearchMode.PERSONNE_PHYSIQUE, {"physique": PersonnePhysiqueCriteria()}),
            (SearchMode.PERSONNE_MORALE, {"morale": PersonneMoraleCriteria()}),
        ):
            with self.subTest(mode=mode):
                criteria = SearchCriteria(mode=mode, **specific)
                self.assertEqual(validate_search_criteria(criteria), criteria)
                self.assertEqual(criteria_to_mapping(criteria), {"mode": mode.value})
                self.assertEqual(map_criteria_to_controls(criteria), ())
                implicit_empty = SearchCriteria(mode=mode)
                self.assertEqual(validate_search_criteria(implicit_empty), implicit_empty)
                self.assertEqual(criteria_to_mapping(implicit_empty), {"mode": mode.value})

    def test_mode_is_mandatory_and_incompatible_mode_fields_are_rejected(self) -> None:
        with self.assertRaisesRegex(CriteriaValidationError, "mode de recherche est obligatoire"):
            criteria_from_mapping({"nom": "A"})
        with self.assertRaisesRegex(CriteriaValidationError, "Valeur enum invalide pour mode"):
            criteria_from_mapping({"mode": "MODE_INCONNU"})
        with self.assertRaisesRegex(CriteriaValidationError, "inconnus ou incompatibles"):
            criteria_from_mapping({"mode": SearchMode.PERSONNE_PHYSIQUE.value, "raison_sociale": "X"})
        with self.assertRaisesRegex(CriteriaValidationError, "correspondre au mode"):
            validate_search_criteria(SearchCriteria(
                mode=SearchMode.PERSONNE_PHYSIQUE,
                physique=PersonnePhysiqueCriteria(),
                morale=PersonneMoraleCriteria(),
            ))

    def test_unknown_criteria_are_not_accepted(self) -> None:
        with self.assertRaisesRegex(CriteriaValidationError, "inconnus ou incompatibles"):
            criteria_from_mapping({"mode": SearchMode.PERSONNE_MORALE.value, "wilaya_typo": "Sétif"})
        with self.assertRaisesRegex(CriteriaValidationError, "composant du numéro"):
            criteria_from_mapping({
                "mode": SearchMode.PERSONNE_PHYSIQUE.value,
                "numero_inscription": {"nrc6": "x"},
            })

    def test_date_format_and_order_are_validated_locally(self) -> None:
        with self.assertRaisesRegex(CriteriaValidationError, "Date invalide"):
            criteria_from_mapping({
                "mode": SearchMode.PERSONNE_PHYSIQUE.value,
                "date_inscription_du": "31/12/2024",
            })
        with self.assertRaisesRegex(CriteriaValidationError, "antérieure ou égale"):
            criteria_from_mapping({
                "mode": SearchMode.PERSONNE_MORALE.value,
                "date_inscription_du": "2025-01-02",
                "date_inscription_au": "2025-01-01",
            })
        with self.assertRaisesRegex(CriteriaValidationError, "Date invalide"):
            criteria_from_mapping({
                "mode": SearchMode.PERSONNE_MORALE.value,
                "date_naissance_dirigeant": "2023-02-29",
            })
        valid = criteria_from_mapping({
            "mode": SearchMode.PERSONNE_PHYSIQUE.value,
            "date_inscription_du": "2024-02-29",
            "date_inscription_au": "2024-02-29",
        })
        self.assertEqual(valid.common.date_inscription_du, "2024-02-29")

    def test_mode_enum_is_closed_and_invalid_values_are_not_echoed(self) -> None:
        invalid = "MODE_INCONNU"
        with self.assertRaises(CriteriaValidationError) as caught:
            criteria_from_mapping({"mode": invalid})
        self.assertNotIn(invalid, str(caught.exception))

    def test_unknown_select_options_are_not_invented_or_accepted(self) -> None:
        for payload in (
            {"mode": SearchMode.PERSONNE_PHYSIQUE.value, "secteur_activite": "Commerce"},
            {"mode": SearchMode.PERSONNE_MORALE.value, "forme_juridique": "SARL"},
            {"mode": SearchMode.PERSONNE_MORALE.value, "qualite": "Gérant"},
            {"mode": SearchMode.PERSONNE_PHYSIQUE.value, "numero_inscription": {"nrc2": "x"}},
            {"mode": SearchMode.PERSONNE_MORALE.value, "presume": "Oui"},
            {"mode": SearchMode.PERSONNE_PHYSIQUE.value, "conformite_rc": "Oui"},
            {"mode": SearchMode.PERSONNE_MORALE.value, "etat_commercant": "Actif"},
        ):
            with self.subTest(payload=payload), self.assertRaisesRegex(CriteriaValidationError, "Options Sidjilcom non fournies"):
                criteria_from_mapping(payload)
        for key in ("secteur_activite", "morale.forme_juridique", "morale.qualite",
                    "numero_inscription.nrc2", "numero_inscription.nrc5", "conformite_rc", "etat_commercant"):
            self.assertEqual(SIDJILCOM_CONTROL_MAP[key].options, ())
            self.assertIsNone(SIDJILCOM_CONTROL_MAP[key].option_count)

    def test_incomplete_registration_number_is_allowed_but_semantics_block_automation(self) -> None:
        criteria = SearchCriteria(
            mode=SearchMode.PERSONNE_PHYSIQUE,
            physique=PersonnePhysiqueCriteria(),
            common=CommonSearchCriteria(numero_inscription=NumeroInscriptionCriteria(nrc1="01")),
        )
        validate_search_criteria(criteria)
        self.assertFalse(numero_inscription_is_complete(criteria.common.numero_inscription))
        mapping = map_criteria_to_controls(criteria)
        component = next(item for item in mapping if item.criterion == "numero_inscription.nrc1")
        self.assertEqual(component.name_suffix, "_nrc1")
        self.assertFalse(component.input_semantics_confirmed)
        self.assertFalse(component.automation_allowed)
        self.assertIn("signification exacte à confirmer", NUMERO_INSCRIPTION_SEMANTICS_NOTICE)

    def test_canonical_report_mapping_and_autocomplete_metadata(self) -> None:
        self.assertEqual(REPORTED_FORM_METHOD, "POST")
        self.assertEqual(REPORTED_CONTROL_COUNTS[SearchMode.PERSONNE_PHYSIQUE], 20)
        self.assertEqual(REPORTED_CONTROL_COUNTS[SearchMode.PERSONNE_MORALE], 21)
        self.assertEqual(REPORTED_UNCLASSIFIED_CONTROL_COUNTS[SearchMode.PERSONNE_PHYSIQUE], 2)
        self.assertEqual(REPORTED_UNCLASSIFIED_CONTROL_COUNTS[SearchMode.PERSONNE_MORALE], 2)
        for key, suffix in (
            ("commune_wilaya", "_wilcom"), ("activite", "_activi"),
            ("secteur_activite", "_secteu"), ("date_inscription_du", "_deb_im"),
            ("date_inscription_au", "_fin_im"), ("conformite_rc", "_CRCE"),
            ("etat_commercant", "_etat_c"), ("physique.nom", "_nom"),
            ("physique.prenom", "_prenom"), ("physique.nom_commercial", "_nom_co"),
            ("physique.date_naissance", "_d_nais"), ("physique.presume", "_presum"),
            ("physique.nationalite", "_nation"), ("morale.raison_sociale", "_raison"),
            ("morale.forme_juridique", "_forme_"), ("morale.nom_prenom_dirigeant", "_nom_pr"),
            ("morale.date_naissance_dirigeant", "_d_nais"), ("morale.presume", "_presum"),
            ("morale.nationalite", "_nation"), ("morale.qualite", "_qualit"),
        ):
            with self.subTest(key=key):
                self.assertEqual(SIDJILCOM_CONTROL_MAP[key].name_suffix, suffix)
        for key in ("commune_wilaya", "activite", "physique.nationalite", "morale.nationalite"):
            mapping = SIDJILCOM_CONTROL_MAP[key]
            self.assertEqual(mapping.component_type, ComponentType.AUTOCOMPLETE)
            self.assertEqual(mapping.css_class, "yui3-aclist-input")
            self.assertEqual(mapping.aria_autocomplete, "list")

    def test_mode_specific_mappings_and_select_metadata_are_explicit(self) -> None:
        physical = SIDJILCOM_CONTROL_MAP["physique.nom"]
        moral = SIDJILCOM_CONTROL_MAP["morale.raison_sociale"]
        self.assertEqual(physical.modes, (SearchMode.PERSONNE_PHYSIQUE,))
        self.assertEqual(moral.modes, (SearchMode.PERSONNE_MORALE,))
        for key in ("secteur_activite", "morale.forme_juridique", "morale.qualite",
                    "numero_inscription.nrc2", "numero_inscription.nrc5", "conformite_rc", "etat_commercant"):
            control = SIDJILCOM_CONTROL_MAP[key]
            self.assertEqual(control.component_type, ComponentType.SELECT)
            self.assertIsNone(control.element_id)
            self.assertIsNone(control.option_count)
            self.assertEqual(control.options, ())

    def test_suffix_resolution_requires_exactly_one_control_and_respects_mode(self) -> None:
        self.assertEqual(
            resolve_control_name(SearchMode.PERSONNE_PHYSIQUE, "commune_wilaya", ["_ns_wilcom"]),
            "_ns_wilcom",
        )
        with self.assertRaisesRegex(ControlMappingError, "ne correspond"):
            resolve_control_name(SearchMode.PERSONNE_PHYSIQUE, "commune_wilaya", ["_ns_wilaya"])
        with self.assertRaisesRegex(ControlMappingError, "ambiguë"):
            resolve_control_name(SearchMode.PERSONNE_MORALE, "morale.nationalite", ["_a_nation", "_b_nation"])
        with self.assertRaisesRegex(ControlMappingError, "absent"):
            resolve_control_name(SearchMode.PERSONNE_MORALE, "physique.nom", ["_ns_nom"])


if __name__ == "__main__":
    unittest.main()
