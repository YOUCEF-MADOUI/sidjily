"""Modèle typé des critères Sidjilcom et correspondance structurelle vers le DOM.

Les identifiants complets des contrôles du rapport réel comportent un préfixe généré
qui n'a pas été transmis. Le registre centralise donc uniquement les suffixes confirmés;
aucune saisie automatique n'est activée par ce module.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date
from enum import Enum
import re
from typing import Mapping, TypeVar


class SearchMode(str, Enum):
    PERSONNE_PHYSIQUE = "PERSONNE_PHYSIQUE"
    PERSONNE_MORALE = "PERSONNE_MORALE"


class ComponentType(str, Enum):
    TEXT = "text"
    AUTOCOMPLETE = "autocomplete"
    SELECT = "select"
    DATE = "date"
    UNCONFIRMED = "unconfirmed"


NUMERO_INSCRIPTION_SEMANTICS_NOTICE = (
    "signification exacte à confirmer avant implémentation de la saisie automatique"
)


class CriteriaValidationError(ValueError):
    """Erreur de validation locale; les valeurs fournies ne figurent jamais dans le message."""


class ControlMappingError(ValueError):
    """Un suffixe DOM est absent ou ambigu dans un instantané de contrôles."""


@dataclass(frozen=True, slots=True)
class NumeroInscriptionCriteria:
    """Composants nrc1…nrc5; aucune interprétation métier n'est attribuée aux composants."""

    nrc1: str | None = None
    nrc2: str | None = None
    nrc3: str | None = None
    nrc4: str | None = None
    nrc5: str | None = None


@dataclass(frozen=True, slots=True)
class CommonSearchCriteria:
    numero_inscription: NumeroInscriptionCriteria = NumeroInscriptionCriteria()
    commune_wilaya: str | None = None
    secteur_activite: str | None = None
    activite: str | None = None
    date_inscription_du: str | None = None
    date_inscription_au: str | None = None
    conformite_rc: str | None = None
    etat_commercant: str | None = None


@dataclass(frozen=True, slots=True)
class PersonnePhysiqueCriteria:
    nom: str | None = None
    prenom: str | None = None
    nom_commercial: str | None = None
    date_naissance: str | None = None
    presume: str | None = None
    nationalite: str | None = None


@dataclass(frozen=True, slots=True)
class PersonneMoraleCriteria:
    raison_sociale: str | None = None
    forme_juridique: str | None = None
    nom_prenom_dirigeant: str | None = None
    date_naissance_dirigeant: str | None = None
    presume: str | None = None
    nationalite: str | None = None
    qualite: str | None = None


@dataclass(frozen=True, slots=True)
class SearchCriteria:
    mode: SearchMode
    common: CommonSearchCriteria = CommonSearchCriteria()
    physique: PersonnePhysiqueCriteria | None = None
    morale: PersonneMoraleCriteria | None = None


@dataclass(frozen=True, slots=True)
class ControlMapping:
    criterion: str
    label: str
    modes: tuple[SearchMode, ...]
    name_suffix: str | None
    element_id: str | None
    html_type: str | None
    component_type: ComponentType
    option_count: int | None = None
    options: tuple[str, ...] = ()
    css_class: str | None = None
    aria_autocomplete: str | None = None
    input_semantics_confirmed: bool = True


@dataclass(frozen=True, slots=True)
class MappedCriterion:
    criterion: str
    label: str
    name_suffix: str | None
    component_type: ComponentType
    value: str
    input_semantics_confirmed: bool
    automation_allowed: bool = False


_BOTH_MODES = (SearchMode.PERSONNE_PHYSIQUE, SearchMode.PERSONNE_MORALE)


def _control(
    criterion: str,
    label: str,
    suffix: str | None,
    modes: tuple[SearchMode, ...] = _BOTH_MODES,
    *,
    html_type: str | None = None,
    component_type: ComponentType = ComponentType.UNCONFIRMED,
    options: tuple[str, ...] = (),
    option_count: int | None = None,
    css_class: str | None = None,
    aria_autocomplete: str | None = None,
    input_semantics_confirmed: bool = True,
) -> ControlMapping:
    return ControlMapping(
        criterion=criterion,
        label=label,
        modes=modes,
        name_suffix=suffix,
        element_id=None,
        html_type=html_type,
        component_type=component_type,
        option_count=option_count,
        options=options,
        css_class=css_class,
        aria_autocomplete=aria_autocomplete,
        input_semantics_confirmed=input_semantics_confirmed,
    )


# Source unique des suffixes DOM; les préfixes générés complets ne sont pas disponibles.
SIDJILCOM_CONTROL_MAP: dict[str, ControlMapping] = {
    **{
        f"numero_inscription.nrc{index}": _control(
            f"numero_inscription.nrc{index}",
            f"Composant nrc{index}",
            f"_nrc{index}",
            html_type="select" if index in (2, 5) else None,
            component_type=ComponentType.SELECT if index in (2, 5) else ComponentType.UNCONFIRMED,
            input_semantics_confirmed=False,
        )
        for index in range(1, 6)
    },
    "commune_wilaya": _control(
        "commune_wilaya", "Commune/Wilaya d'inscription", "_wilcom",
        html_type="text", component_type=ComponentType.AUTOCOMPLETE,
        css_class="yui3-aclist-input", aria_autocomplete="list",
    ),
    "secteur_activite": _control(
        "secteur_activite", "Secteur d'activité", "_secteu",
        html_type="select", component_type=ComponentType.SELECT,
    ),
    "activite": _control(
        "activite", "Activité", "_activi", html_type="text",
        component_type=ComponentType.AUTOCOMPLETE,
        css_class="yui3-aclist-input", aria_autocomplete="list",
    ),
    "date_inscription_du": _control(
        "date_inscription_du", "Date d'inscription — Du", "_deb_im",
        component_type=ComponentType.DATE,
    ),
    "date_inscription_au": _control(
        "date_inscription_au", "Date d'inscription — Au", "_fin_im",
        component_type=ComponentType.DATE,
    ),
    "conformite_rc": _control(
        "conformite_rc", "Conformité RC", "_CRCE", html_type="select",
        component_type=ComponentType.SELECT,
    ),
    "etat_commercant": _control(
        "etat_commercant", "État commerçant", "_etat_c", html_type="select",
        component_type=ComponentType.SELECT,
    ),
    "physique.nom": _control(
        "physique.nom", "Nom", "_nom", (SearchMode.PERSONNE_PHYSIQUE,),
        html_type="text", component_type=ComponentType.TEXT,
    ),
    "physique.prenom": _control(
        "physique.prenom", "Prénom", "_prenom", (SearchMode.PERSONNE_PHYSIQUE,),
        html_type="text", component_type=ComponentType.TEXT,
    ),
    "physique.nom_commercial": _control(
        "physique.nom_commercial", "Nom commercial", "_nom_co", (SearchMode.PERSONNE_PHYSIQUE,),
        html_type="text", component_type=ComponentType.TEXT,
    ),
    "physique.date_naissance": _control(
        "physique.date_naissance", "Date de naissance", "_d_nais", (SearchMode.PERSONNE_PHYSIQUE,),
        component_type=ComponentType.DATE,
    ),
    "physique.presume": _control(
        "physique.presume", "Présumé", "_presum", (SearchMode.PERSONNE_PHYSIQUE,),
        component_type=ComponentType.UNCONFIRMED, input_semantics_confirmed=False,
    ),
    "physique.nationalite": _control(
        "physique.nationalite", "Nationalité", "_nation", (SearchMode.PERSONNE_PHYSIQUE,),
        html_type="text", component_type=ComponentType.AUTOCOMPLETE,
        css_class="yui3-aclist-input", aria_autocomplete="list",
    ),
    "morale.raison_sociale": _control(
        "morale.raison_sociale", "Raison Sociale / Nom commercial", "_raison",
        (SearchMode.PERSONNE_MORALE,), html_type="text", component_type=ComponentType.TEXT,
    ),
    "morale.forme_juridique": _control(
        "morale.forme_juridique", "Forme Juridique", "_forme_",
        (SearchMode.PERSONNE_MORALE,), html_type="select", component_type=ComponentType.SELECT,
    ),
    "morale.nom_prenom_dirigeant": _control(
        "morale.nom_prenom_dirigeant", "Nom / Prénom du dirigeant", "_nom_pr",
        (SearchMode.PERSONNE_MORALE,), html_type="text", component_type=ComponentType.TEXT,
    ),
    "morale.date_naissance_dirigeant": _control(
        "morale.date_naissance_dirigeant", "Date de naissance du dirigeant", "_d_nais",
        (SearchMode.PERSONNE_MORALE,), component_type=ComponentType.DATE,
    ),
    "morale.presume": _control(
        "morale.presume", "Présumé", "_presum", (SearchMode.PERSONNE_MORALE,),
        component_type=ComponentType.UNCONFIRMED, input_semantics_confirmed=False,
    ),
    "morale.nationalite": _control(
        "morale.nationalite", "Nationalité", "_nation", (SearchMode.PERSONNE_MORALE,),
        html_type="text", component_type=ComponentType.AUTOCOMPLETE,
        css_class="yui3-aclist-input", aria_autocomplete="list",
    ),
    "morale.qualite": _control(
        "morale.qualite", "Qualité", "_qualit", (SearchMode.PERSONNE_MORALE,),
        html_type="select", component_type=ComponentType.SELECT,
    ),
}

REPORTED_FORM_METHOD = "POST"
REPORTED_CONTROL_COUNTS: dict[SearchMode, int] = {
    SearchMode.PERSONNE_PHYSIQUE: 20,
    SearchMode.PERSONNE_MORALE: 21,
}
REPORTED_UNCLASSIFIED_CONTROL_COUNTS: dict[SearchMode, int] = {
    mode: REPORTED_CONTROL_COUNTS[mode]
    - sum(mode in mapping.modes for mapping in SIDJILCOM_CONTROL_MAP.values())
    for mode in SearchMode
}

# Les options de ces listes n'étaient pas exposées dans le rapport transmis.
SELECT_OPTION_CATALOGS: dict[str, tuple[str, ...]] = {
    key: mapping.options
    for key, mapping in SIDJILCOM_CONTROL_MAP.items()
    if mapping.component_type is ComponentType.SELECT
}

_E = TypeVar("_E", bound=Enum)


def _enum_value(value: object, enum_type: type[_E], field_name: str) -> _E | None:
    if value is None or value == "":
        return None
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        try:
            return enum_type(value)
        except ValueError:
            pass
    raise CriteriaValidationError(f"Valeur enum invalide pour {field_name}.")


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise CriteriaValidationError(f"Le critère {field_name} doit être un texte.")
    cleaned = value.strip()
    return cleaned or None


def _parse_date(value: str | None, field_name: str) -> date | None:
    if value is None:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise CriteriaValidationError(f"Date invalide pour {field_name}; format attendu : AAAA-MM-JJ.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise CriteriaValidationError(f"Date invalide pour {field_name}.") from None


def _check_select_value(criterion: str, value: str | None) -> None:
    if value is None:
        return
    options = SELECT_OPTION_CATALOGS.get(criterion, ())
    if not options:
        raise CriteriaValidationError(
            f"Options Sidjilcom non fournies pour {criterion}; aucune valeur enum n'est autorisée."
        )
    if value not in options:
        raise CriteriaValidationError(f"Valeur enum invalide pour {criterion}.")


def validate_search_criteria(criteria: SearchCriteria) -> SearchCriteria:
    """Valide localement un modèle; les critères vides restent autorisés."""
    if not isinstance(criteria, SearchCriteria) or not isinstance(criteria.mode, SearchMode):
        raise CriteriaValidationError("Le mode de recherche est obligatoire.")
    if criteria.mode is SearchMode.PERSONNE_PHYSIQUE:
        if criteria.morale is not None or (
            criteria.physique is not None and not isinstance(criteria.physique, PersonnePhysiqueCriteria)
        ):
            raise CriteriaValidationError("Les critères doivent correspondre au mode personne physique.")
        specific: PersonnePhysiqueCriteria | PersonneMoraleCriteria = (
            criteria.physique if criteria.physique is not None else PersonnePhysiqueCriteria()
        )
        specific_prefix = "physique"
    else:
        if criteria.physique is not None or (
            criteria.morale is not None and not isinstance(criteria.morale, PersonneMoraleCriteria)
        ):
            raise CriteriaValidationError("Les critères doivent correspondre au mode personne morale.")
        specific = criteria.morale if criteria.morale is not None else PersonneMoraleCriteria()
        specific_prefix = "morale"

    if not isinstance(criteria.common, CommonSearchCriteria):
        raise CriteriaValidationError("Les critères communs sont invalides.")
    number = criteria.common.numero_inscription
    if not isinstance(number, NumeroInscriptionCriteria):
        raise CriteriaValidationError("Le numéro d'inscription doit contenir les composants nrc1…nrc5.")
    for component in fields(number):
        value = _optional_text(getattr(number, component.name), f"numero_inscription.{component.name}")
        if component.name in ("nrc2", "nrc5"):
            _check_select_value(f"numero_inscription.{component.name}", value)

    for name in ("commune_wilaya", "secteur_activite", "activite"):
        value = _optional_text(getattr(criteria.common, name), name)
        if name == "secteur_activite":
            _check_select_value(name, value)
    start = _parse_date(_optional_text(criteria.common.date_inscription_du, "date_inscription_du"), "date_inscription_du")
    end = _parse_date(_optional_text(criteria.common.date_inscription_au, "date_inscription_au"), "date_inscription_au")
    if start is not None and end is not None and start > end:
        raise CriteriaValidationError("La date d'inscription de début doit être antérieure ou égale à la date de fin.")
    for name in ("conformite_rc", "etat_commercant"):
        value = _optional_text(getattr(criteria.common, name), name)
        _check_select_value(name, value)

    for field in fields(specific):
        path = f"{specific_prefix}.{field.name}"
        value = _optional_text(getattr(specific, field.name), path)
        if field.name.startswith("date_"):
            _parse_date(value, path)
        mapping = SIDJILCOM_CONTROL_MAP[path]
        if mapping.component_type is ComponentType.SELECT or mapping.component_type is ComponentType.UNCONFIRMED:
            _check_select_value(path, value)
    return criteria


def criteria_from_mapping(payload: Mapping[str, object]) -> SearchCriteria:
    """Construit un modèle depuis un mapping strict; aucun critère inconnu n'est accepté."""
    if not isinstance(payload, Mapping):
        raise CriteriaValidationError("Les critères doivent être fournis sous forme de mapping.")
    raw_mode = payload.get("mode")
    mode = _enum_value(raw_mode, SearchMode, "mode")
    if mode is None:
        raise CriteriaValidationError("Le mode de recherche est obligatoire.")

    common_keys = {
        "mode", "numero_inscription", "commune_wilaya", "secteur_activite", "activite",
        "date_inscription_du", "date_inscription_au", "conformite_rc", "etat_commercant",
    }
    specific_keys = (
        {"nom", "prenom", "nom_commercial", "date_naissance", "presume", "nationalite"}
        if mode is SearchMode.PERSONNE_PHYSIQUE else
        {"raison_sociale", "forme_juridique", "nom_prenom_dirigeant", "date_naissance_dirigeant", "presume", "nationalite", "qualite"}
    )
    unknown = set(payload) - common_keys - specific_keys
    if unknown:
        raise CriteriaValidationError("Un ou plusieurs critères sont inconnus ou incompatibles avec le mode.")

    raw_number = payload.get("numero_inscription", {})
    if raw_number is None:
        raw_number = {}
    if not isinstance(raw_number, Mapping):
        raise CriteriaValidationError("numero_inscription doit contenir les composants nrc1…nrc5.")
    valid_number_keys = {f"nrc{index}" for index in range(1, 6)}
    if set(raw_number) - valid_number_keys:
        raise CriteriaValidationError("Un composant du numéro d'inscription est inconnu.")
    number = NumeroInscriptionCriteria(**{
        key: _optional_text(raw_number.get(key), f"numero_inscription.{key}")
        for key in valid_number_keys
    })
    common = CommonSearchCriteria(
        numero_inscription=number,
        commune_wilaya=_optional_text(payload.get("commune_wilaya"), "commune_wilaya"),
        secteur_activite=_optional_text(payload.get("secteur_activite"), "secteur_activite"),
        activite=_optional_text(payload.get("activite"), "activite"),
        date_inscription_du=_optional_text(payload.get("date_inscription_du"), "date_inscription_du"),
        date_inscription_au=_optional_text(payload.get("date_inscription_au"), "date_inscription_au"),
        conformite_rc=_optional_text(payload.get("conformite_rc"), "conformite_rc"),
        etat_commercant=_optional_text(payload.get("etat_commercant"), "etat_commercant"),
    )
    if mode is SearchMode.PERSONNE_PHYSIQUE:
        criteria = SearchCriteria(
            mode=mode,
            common=common,
            physique=PersonnePhysiqueCriteria(**{
                key: _optional_text(payload.get(key), key)
                for key in specific_keys
            }),
        )
    else:
        criteria = SearchCriteria(
            mode=mode,
            common=common,
            morale=PersonneMoraleCriteria(**{
                key: _optional_text(payload.get(key), key)
                for key in specific_keys
            }),
        )
    return validate_search_criteria(criteria)


def criteria_to_mapping(criteria: SearchCriteria) -> dict[str, object]:
    """Sérialise uniquement le mode et les critères renseignés, sans produire de log."""
    validate_search_criteria(criteria)
    result: dict[str, object] = {"mode": criteria.mode.value}
    common = criteria.common
    number = {
        field.name: value
        for field in fields(common.numero_inscription)
        if (value := _optional_text(getattr(common.numero_inscription, field.name), field.name)) is not None
    }
    if number:
        result["numero_inscription"] = number
    for field in fields(common):
        if field.name == "numero_inscription":
            continue
        value = _optional_text(getattr(common, field.name), field.name)
        if value is not None:
            result[field.name] = value
    if criteria.mode is SearchMode.PERSONNE_PHYSIQUE:
        specific = criteria.physique or PersonnePhysiqueCriteria()
    else:
        specific = criteria.morale or PersonneMoraleCriteria()
    for field in fields(specific):
        value = _optional_text(getattr(specific, field.name), field.name)
        if value is not None:
            result[field.name] = value
    return result


def numero_inscription_is_complete(criteria: NumeroInscriptionCriteria) -> bool:
    """Indique uniquement si les cinq slots sont renseignés; ne leur attribue pas de sens métier."""
    return all(_optional_text(getattr(criteria, f"nrc{index}"), f"nrc{index}") for index in range(1, 6))


def _criterion_values(criteria: SearchCriteria) -> dict[str, str]:
    values: dict[str, str] = {}
    for field in fields(criteria.common):
        if field.name == "numero_inscription":
            for component in fields(criteria.common.numero_inscription):
                value = _optional_text(
                    getattr(criteria.common.numero_inscription, component.name),
                    f"numero_inscription.{component.name}",
                )
                if value:
                    values[f"numero_inscription.{component.name}"] = value
        else:
            value = _optional_text(getattr(criteria.common, field.name), field.name)
            if value:
                values[field.name] = str(value)
    if criteria.mode is SearchMode.PERSONNE_PHYSIQUE:
        specific = criteria.physique or PersonnePhysiqueCriteria()
        prefix = "physique"
    else:
        specific = criteria.morale or PersonneMoraleCriteria()
        prefix = "morale"
    for field in fields(specific):
        value = _optional_text(getattr(specific, field.name), f"{prefix}.{field.name}")
        if value:
            values[f"{prefix}.{field.name}"] = value
    return values


def map_criteria_to_controls(criteria: SearchCriteria) -> tuple[MappedCriterion, ...]:
    """Projette les critères vers des suffixes confirmés; n'agit jamais sur le navigateur."""
    validate_search_criteria(criteria)
    result = []
    for key, value in _criterion_values(criteria).items():
        mapping = SIDJILCOM_CONTROL_MAP.get(key)
        if mapping is None or criteria.mode not in mapping.modes:
            raise CriteriaValidationError("Un critère n'a pas de mapping compatible avec le mode.")
        result.append(MappedCriterion(
            criterion=key,
            label=mapping.label,
            name_suffix=mapping.name_suffix,
            component_type=mapping.component_type,
            value=value,
            input_semantics_confirmed=mapping.input_semantics_confirmed,
            automation_allowed=False,
        ))
    return tuple(result)


def resolve_control_name(mode: SearchMode, criterion: str, observed_names: tuple[str, ...] | list[str]) -> str:
    """Résout un nom réel par suffixe exact et mode, sans stocker le préfixe généré."""
    mapping = SIDJILCOM_CONTROL_MAP.get(criterion)
    if mapping is None or mode not in mapping.modes or not mapping.name_suffix:
        raise ControlMappingError("Mapping du contrôle absent pour ce mode.")
    matches = [
        name for name in observed_names
        if isinstance(name, str) and name.casefold().endswith(mapping.name_suffix.casefold())
    ]
    if not matches:
        raise ControlMappingError("Aucun nom DOM ne correspond au suffixe confirmé.")
    if len(matches) != 1:
        raise ControlMappingError("Plusieurs noms DOM correspondent au suffixe; résolution ambiguë.")
    return matches[0]


def format_criteria_preview(criteria: SearchCriteria) -> str:
    """Aperçu local des critères sélectionnés; n'exécute aucune action réseau."""
    mapped = map_criteria_to_controls(criteria)
    mode_label = "Personne physique" if criteria.mode is SearchMode.PERSONNE_PHYSIQUE else "Personne morale"
    lines = [f"Mode : {mode_label}"]
    if not mapped:
        lines.append("Aucun critère renseigné; aucune recherche ne sera lancée.")
    else:
        lines.append("Critères sélectionnés :")
        lines.extend(f"- {item.label} : {item.value}" for item in mapped)
    if any(item.criterion.startswith("numero_inscription.") for item in mapped):
        lines.append(NUMERO_INSCRIPTION_SEMANTICS_NOTICE + "; aucun remplissage automatique n'est activé.")
    lines.append("Aucune requête Sidjilcom n'a été envoyée.")
    return "\n".join(lines)
