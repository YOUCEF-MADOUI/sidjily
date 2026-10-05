from __future__ import annotations

import unittest

from sidjily.sidjilcom.criteria import SearchMode, criteria_from_mapping, criteria_to_mapping
from sidjily.sidjilcom.preparation import (
    ADVANCED_FIELDS,
    CONFIRMED_AUTOCOMPLETE_FIELDS,
    PREPARATION_NOTICE,
    PRIMARY_FIELDS,
    count_filled_criteria,
    draft_summary_record,
    format_preparation_summary,
)


class SearchPreparationTests(unittest.TestCase):
    def test_primary_criteria_are_ordered_by_person_type(self) -> None:
        self.assertEqual(
            PRIMARY_FIELDS[SearchMode.PERSONNE_PHYSIQUE],
            ("nom", "prenom", "activite", "commune_wilaya", "nationalite"),
        )
        self.assertEqual(
            PRIMARY_FIELDS[SearchMode.PERSONNE_MORALE],
            ("raison_sociale", "forme_juridique", "activite", "commune_wilaya"),
        )
        self.assertIn("secteur_activite", ADVANCED_FIELDS[SearchMode.PERSONNE_MORALE])
        self.assertIn("nom_prenom_dirigeant", ADVANCED_FIELDS[SearchMode.PERSONNE_MORALE])

    def test_switching_mode_uses_only_compatible_mode_criteria(self) -> None:
        physical = criteria_to_mapping(criteria_from_mapping({
            "mode": SearchMode.PERSONNE_PHYSIQUE.value,
            "nom": "Benali",
        }))
        moral = criteria_to_mapping(criteria_from_mapping({
            "mode": SearchMode.PERSONNE_MORALE.value,
            "raison_sociale": "Atlas Conseil",
        }))
        self.assertIn("nom", physical)
        self.assertNotIn("raison_sociale", physical)
        self.assertIn("raison_sociale", moral)
        self.assertNotIn("nom", moral)

    def test_wilaya_and_commune_remain_one_confirmed_criterion(self) -> None:
        criteria = criteria_from_mapping({
            "mode": SearchMode.PERSONNE_PHYSIQUE.value,
            "commune_wilaya": "Sétif",
        })
        mapping = criteria_to_mapping(criteria)
        self.assertEqual(mapping["commune_wilaya"], "Sétif")
        self.assertNotIn("wilaya", mapping)
        self.assertNotIn("commune", mapping)

    def test_confirmed_autocomplete_scope_matches_existing_mappings(self) -> None:
        self.assertEqual(
            CONFIRMED_AUTOCOMPLETE_FIELDS,
            {
                "activite",
                "commune_wilaya",
                "physique.nationalite",
                "morale.nationalite",
            },
        )

    def test_summary_counts_multiple_filled_criteria_and_stays_local(self) -> None:
        criteria = criteria_from_mapping({
            "mode": SearchMode.PERSONNE_PHYSIQUE.value,
            "nom": "Benali",
            "prenom": "Nadia",
            "activite": "Commerce",
            "commune_wilaya": "Sétif",
            "nationalite": "Algérienne",
        })
        self.assertEqual(count_filled_criteria(criteria), 5)
        summary = format_preparation_summary(criteria)
        self.assertIn("Mode : Personne physique", summary)
        self.assertIn("Nombre de critères renseignés : 5", summary)
        self.assertIn(PREPARATION_NOTICE, summary)
        self.assertEqual(draft_summary_record(criteria)["submitted"], False)

    def test_empty_valid_modes_can_be_prepared_without_submission(self) -> None:
        for mode in SearchMode:
            with self.subTest(mode=mode):
                criteria = criteria_from_mapping({"mode": mode.value})
                self.assertEqual(count_filled_criteria(criteria), 0)
                self.assertIn(PREPARATION_NOTICE, format_preparation_summary(criteria))


if __name__ == "__main__":
    unittest.main()
