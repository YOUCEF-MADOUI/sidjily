"""Helpers purs pour préparer et résumer une recherche sans toucher au portail."""

from __future__ import annotations

from typing import Any

from sidjily.sidjilcom.criteria import (
    ComponentType,
    SearchCriteria,
    SearchMode,
    SIDJILCOM_CONTROL_MAP,
    criteria_to_mapping,
    format_criteria_preview,
)


# Ordre visible retenu pour les critères principaux du parcours de préparation.
PRIMARY_FIELDS: dict[SearchMode, tuple[str, ...]] = {
    SearchMode.PERSONNE_PHYSIQUE: (
        "nom", "prenom", "activite", "commune_wilaya", "nationalite",
    ),
    SearchMode.PERSONNE_MORALE: (
        "raison_sociale", "forme_juridique", "activite", "commune_wilaya",
    ),
}

ADVANCED_FIELDS: dict[SearchMode, tuple[str, ...]] = {
    SearchMode.PERSONNE_PHYSIQUE: (
        "nom_commercial", "date_naissance", "presume", "secteur_activite",
        "date_inscription_du", "date_inscription_au", "conformite_rc",
        "etat_commercant", "numero_inscription",
    ),
    SearchMode.PERSONNE_MORALE: (
        "secteur_activite", "date_inscription_du", "date_inscription_au",
        "conformite_rc", "etat_commercant", "nom_prenom_dirigeant",
        "date_naissance_dirigeant", "nationalite", "presume", "qualite",
        "numero_inscription",
    ),
}

# Une seule entrée Sidjilcom confirmée couvre la Wilaya et la Commune.
# On ne crée donc pas deux sélecteurs déconnectés ni une dépendance réseau supposée.
WILAYA_COMMUNE_CRITERION = "commune_wilaya"
CONFIRMED_AUTOCOMPLETE_FIELDS = frozenset(
    key
    for key, mapping in SIDJILCOM_CONTROL_MAP.items()
    if mapping.component_type is ComponentType.AUTOCOMPLETE and mapping.name_suffix in {
        "_activi", "_wilcom", "_nation",
    }
)


FIELD_LABELS: dict[str, str] = {
    "nom": "Nom",
    "prenom": "Prénom",
    "activite": "Activité",
    "commune_wilaya": "Wilaya / Commune",
    "nationalite": "Nationalité",
    "raison_sociale": "Raison sociale",
    "forme_juridique": "Forme juridique",
    "secteur_activite": "Secteur d'activité",
    "date_inscription_du": "Date d'inscription — du",
    "date_inscription_au": "Date d'inscription — au",
    "conformite_rc": "Conformité RC",
    "etat_commercant": "État commerçant",
    "nom_prenom_dirigeant": "Nom / prénom du dirigeant",
    "date_naissance_dirigeant": "Date de naissance du dirigeant",
    "nom_commercial": "Nom commercial",
    "date_naissance": "Date de naissance",
    "presume": "Présumé",
    "qualite": "Qualité",
    "numero_inscription": "Composants du numéro d'inscription (sens non interprété)",
}


PREPARATION_NOTICE = "Aucune recherche n'a encore été envoyée à Sidjilcom."


def count_filled_criteria(criteria: SearchCriteria) -> int:
    """Compte les valeurs renseignées, sans compter le mode comme un critère."""
    values = criteria_to_mapping(criteria)
    total = 0
    for key, value in values.items():
        if key == "mode":
            continue
        if key == "numero_inscription" and isinstance(value, dict):
            total += sum(bool(component) for component in value.values())
        elif value not in (None, ""):
            total += 1
    return total


def format_preparation_summary(criteria: SearchCriteria) -> str:
    """Construit un récapitulatif local; cette fonction n'accepte aucun client réseau."""
    preview_lines = format_criteria_preview(criteria).splitlines()
    if preview_lines and preview_lines[-1] == "Aucune requête Sidjilcom n'a été envoyée.":
        preview_lines.pop()
    return "\n".join((
        "\n".join(preview_lines),
        f"Nombre de critères renseignés : {count_filled_criteria(criteria)}",
        PREPARATION_NOTICE,
    ))


def draft_summary_record(criteria: SearchCriteria) -> dict[str, Any]:
    """Valeurs à persister dans la colonne de résumé SQLite déjà existante."""
    return {
        "kind": "sidjily_preparation_draft",
        "mode": criteria.mode.value,
        "filled_criteria": count_filled_criteria(criteria),
        "summary": format_preparation_summary(criteria),
        "submitted": False,
    }
