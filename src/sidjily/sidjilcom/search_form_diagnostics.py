"""Rapport en lecture seule pour diagnostiquer le précontrôle Tâche 12.

Les types de ce module ne contiennent jamais de valeurs de champs. Le lecteur Playwright
ne peut transmettre que des métadonnées structurelles et un booléen ``has_value``.
"""

from __future__ import annotations

from dataclasses import dataclass
import unicodedata
from types import SimpleNamespace
from typing import Any

from sidjily.sidjilcom.criteria import SIDJILCOM_CONTROL_MAP
from sidjily.sidjilcom.diagnostics import FormDiagnosticBundle, format_diagnostic, sanitize_metadata_text


DIAGNOSTIC_STAGES: tuple[tuple[str, str], ...] = (
    ("after_navigation", "Après navigation"),
    ("after_wilaya_selection", "Après sélection de la Wilaya"),
    ("before_submission", "Juste avant soumission (sans soumettre)"),
)
DIAGNOSTIC_STAGE_KEYS = frozenset(key for key, _label in DIAGNOSTIC_STAGES)

# 7 critères communs + 7 critères de personne morale. Les composants nrc sont listés
# séparément et aucune signification ne leur est attribuée.
MORALE_FIELD_PATHS: tuple[tuple[str, str], ...] = (
    ("commune_wilaya", "Commune / Wilaya"),
    ("secteur_activite", "Secteur d'activité"),
    ("activite", "Activité"),
    ("date_inscription_du", "Date d'inscription — Du"),
    ("date_inscription_au", "Date d'inscription — Au"),
    ("conformite_rc", "Conformité RC"),
    ("etat_commercant", "État commerçant"),
    ("morale.raison_sociale", "Raison sociale / nom commercial"),
    ("morale.forme_juridique", "Forme juridique"),
    ("morale.nom_prenom_dirigeant", "Nom / prénom de l'associé (mappage central combiné)"),
    ("morale.date_naissance_dirigeant", "Date de naissance associée au mappage personne morale"),
    ("morale.presume", "Présumé"),
    ("morale.nationalite", "Nationalité"),
    ("morale.qualite", "Qualité"),
)
_FIELD_LABEL_ALIASES: dict[str, tuple[str, ...]] = {
    "morale.raison_sociale": ("raison sociale", "nom commercial"),
    "morale.forme_juridique": ("forme juridique",),
    "morale.nom_prenom_dirigeant": (
        "nom de l'associe", "prenom de l'associe", "nom associe", "prenom associe",
        "nom de l'associé", "prénom de l'associé",
    ),
    "morale.date_naissance_dirigeant": ("date de naissance", "date naissance"),
    "morale.nationalite": ("nationalite",),
    "morale.qualite": ("qualite",),
    "commune_wilaya": ("wilaya", "commune"),
    "secteur_activite": ("secteur",),
    "activite": ("activite",),
    "date_inscription_du": ("date d'inscription", "date inscription"),
    "date_inscription_au": ("date d'inscription", "date inscription"),
    "conformite_rc": ("conformite",),
    "etat_commercant": ("etat commercant",),
    "morale.presume": ("presume",),
}


@dataclass(frozen=True, slots=True)
class PreflightButton:
    text: str
    visible: bool
    enabled: bool
    associated: bool
    selector: str = ""


@dataclass(frozen=True, slots=True)
class PreflightForm:
    frame_name: str
    form_id: str
    form_name: str
    title: str
    action: str
    method: str
    class_name: str
    role: str
    visible: bool
    css_selector: str
    expected_control_count: int
    expected_controls: tuple[str, ...]
    visible_control_count: int
    nonempty_control_count: int
    nonempty_controls: tuple[str, ...]
    search_buttons: tuple[PreflightButton, ...]
    reset_buttons: tuple[PreflightButton, ...]
    action_is_portal: bool


@dataclass(frozen=True, slots=True)
class SearchPreflightSnapshot:
    route_matches: bool
    page_section: str
    expected_suffix: str
    visible_form_count: int
    global_expected_control_count: int
    global_expected_controls: tuple[str, ...]
    forms: tuple[PreflightForm, ...]
    inspection_incomplete: bool = False


def _field_matches(field: object, suffix: str) -> bool:
    name = getattr(field, "name", "")
    element_id = getattr(field, "element_id", "")
    return bool(
        (name and name.casefold().endswith(suffix.casefold()))
        or (element_id and element_id.casefold().endswith(suffix.casefold()))
    )


def _form_key(form: PreflightForm) -> str:
    identity = form.form_id or form.form_name or form.css_selector or "formulaire sans identifiant"
    return f"{form.frame_name} · {identity}"


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def analyze_preflight(snapshot: SearchPreflightSnapshot) -> tuple[str, ...]:
    """Retourne des constats sûrs et classés selon les conditions actuelles de Tâche 12."""
    findings: list[str] = []
    if snapshot.inspection_incomplete:
        findings.append("INSPECTION PARTIELLE : au moins une frame ou une lecture de contrôle n'a pas pu être inspectée.")
    if not snapshot.route_matches:
        findings.append("ROUTE : la page courante ne correspond pas à la route de recherche reconnue.")
    if snapshot.visible_form_count == 0:
        findings.append("ABSENT : aucun élément <form> visible n'est recensé dans les frames accessibles.")
        return tuple(findings)

    visible_forms = tuple(form for form in snapshot.forms if form.visible)
    candidates = tuple(form for form in visible_forms if form.expected_control_count > 0)
    if snapshot.global_expected_control_count == 0:
        findings.append(
            f"CHAMP ABSENT : aucun contrôle visible ne correspond au suffixe attendu {snapshot.expected_suffix}."
        )
    elif not candidates:
        findings.append(
            f"ASSOCIATION : le contrôle {snapshot.expected_suffix} apparaît dans le DOM inspecté, "
            "mais pas comme descendant du <form> utilisé par le précontrôle Tâche 12."
        )
    elif len(candidates) > 1:
        findings.append(
            f"AMBIGUÏTÉ : {len(candidates)} formulaires visibles contiennent le contrôle attendu."
        )

    for form in candidates:
        key = _form_key(form)
        if form.expected_control_count != 1:
            findings.append(
                f"AMBIGUÏTÉ DU CHAMP : {key} contient {form.expected_control_count} correspondance(s) au suffixe attendu."
            )
        if form.method.casefold() != "post":
            findings.append(f"MÉTHODE : {key} déclare {form.method or 'non définie'} au lieu de POST.")
        if not form.action_is_portal:
            findings.append(f"ACTION : l'action de {key} n'est pas reconnue comme une destination Sidjilcom autorisée.")
        if form.nonempty_control_count:
            findings.append(
                f"PRÉREMPLI : {key} contient {form.nonempty_control_count} contrôle(s) visible(s) non vide(s); "
                "aucune valeur n'est affichée."
            )
        matching_search = tuple(
            button for button in form.search_buttons
            if button.visible and button.enabled and button.associated
        )
        if not matching_search:
            findings.append(f"BOUTON : aucun bouton Rechercher visible et activé n'est associé à {key}.")
        elif len(matching_search) > 1:
            findings.append(f"AMBIGUÏTÉ DU BOUTON : {len(matching_search)} boutons Rechercher activés sont associés à {key}.")

    if not findings:
        findings.append(
            "AUCUN ÉCHEC REPRODUIT : le précontrôle structurel observé satisfait les critères rapportés "
            "(route, formulaire, suffixe, méthode/action, contrôles vides, bouton associé). "
            "Un état antérieur transitoire ou un changement depuis l'échec reste possible."
        )
    return tuple(findings)


def expected_field_rows(bundle: FormDiagnosticBundle) -> tuple[str, ...]:
    """Associe les 14 critères personne morale aux contrôles réellement recensés."""
    lines = ["14 CHAMPS DE RÉFÉRENCE — constats DOM, sans lecture des valeurs"]
    fields = bundle.all_controls
    for path, display in MORALE_FIELD_PATHS:
        mapping = SIDJILCOM_CONTROL_MAP[path]
        suffix = mapping.name_suffix or ""
        matches = tuple(field for field in fields if _field_matches(field, suffix))
        aliases = tuple(_fold(alias) for alias in _FIELD_LABEL_ALIASES.get(path, ()))
        label_matches = tuple(
            field for field in fields
            if _fold(display) in _fold(f"{field.label} {field.associated_text}")
            or any(alias in _fold(f"{field.label} {field.associated_text}") for alias in aliases)
        )
        combined = list(dict.fromkeys((*matches, *label_matches)))
        lines.append(f"- {display} · mapping {path} / suffixe {suffix or 'non défini'} : {len(combined)} correspondance(s)")
        for field in combined[:6]:
            location = field.frame_name
            if field.element_id:
                location += f" · id={field.element_id}"
            if field.name:
                location += f" · name={field.name}"
            lines.append(
                f"  {field.tag_name}/{field.html_type} · label={field.label} · rôle={field.role} · "
                f"class={field.class_name or '—'} · aria-autocomplete={field.aria_autocomplete or '—'} · "
                f"composant={field.component_type} · visible={'oui' if field.visible else 'non'} · {location}"
            )
        if not combined:
            lines.append("  Aucun contrôle correspondant dans les frames recensées.")
    lines.extend(
        (
            "Nom et prénom de l'associé : le mapping central existant est combiné (_nom_pr); "
            "les libellés et contrôles distincts réellement présents restent listés dans l'inventaire DOM ci-dessus.",
            "nrc1 à nrc5 — structure uniquement, sans interprétation :",
        )
    )
    for index in range(1, 6):
        path = f"numero_inscription.nrc{index}"
        mapping = SIDJILCOM_CONTROL_MAP[path]
        matches = tuple(field for field in fields if _field_matches(field, mapping.name_suffix or ""))
        if not matches:
            lines.append(f"- nrc{index} ({mapping.name_suffix}) : absent de l'instantané accessible.")
            continue
        for field in matches[:6]:
            lines.append(
                f"- nrc{index} : {field.tag_name}/{field.html_type} · label={field.label} · "
                f"id={field.element_id or '—'} · name={field.name or '—'} · class={field.class_name or '—'} · "
                f"options={field.option_count} · liste={'textes affichés' if field.options else ('textes masqués' if field.options_redacted else 'aucune')}."
            )
    return tuple(lines)


def _safe_snapshot_button_label(text: str) -> str:
    normalized = _fold(text)
    if normalized in {"rechercher", "reinitialiser", "effacer", "annuler", "valider"}:
        return normalized
    return "libellé de bouton masqué"


def snapshot_signature(bundle: FormDiagnosticBundle, preflight: SearchPreflightSnapshot) -> tuple[tuple[str, ...], ...]:
    """Empreinte de métadonnées uniquement; aucun contenu de champ ou d'option n'est inclus."""
    forms = tuple(sorted(
        "|".join((form.frame_name, form.element_id, form.name, form.action, form.method,
                  form.class_name, form.role, str(form.visible), form.css_selector))
        for form in bundle.forms
    ))
    controls = tuple(sorted(
        "|".join((form.frame_name, form.element_id, field.name, field.element_id,
                  field.tag_name, field.html_type, field.role, field.class_name,
                  field.aria_autocomplete, field.placeholder, field.container_tag,
                  field.container_id, field.container_class, field.container_role,
                  field.container_label, field.section_tag, field.section_id, field.section_class,
                  field.section_role, field.section_label, ">".join(field.hierarchy), str(field.visible),
                  str(field.disabled), str(field.required), str(field.readonly),
                  str(field.option_count), str(field.options_redacted), field.css_selector))
        for form in bundle.forms for field in form.fields
    )) + tuple(sorted(
        "|".join((field.frame_name, "hors-formulaire", field.name, field.element_id,
                  field.tag_name, field.html_type, field.role, field.class_name,
                  field.aria_autocomplete, field.placeholder, field.container_tag,
                  field.container_id, field.container_class, field.container_role,
                  field.container_label, field.section_tag, field.section_id, field.section_class,
                  field.section_role, field.section_label, ">".join(field.hierarchy), str(field.visible),
                  str(field.disabled), str(field.required), str(field.readonly),
                  str(field.option_count), str(field.options_redacted), field.css_selector))
        for field in bundle.outside_controls
    ))
    buttons = tuple(sorted(
        "|".join((button.frame_name, _safe_snapshot_button_label(button.text), button.html_type, button.role, button.form_id,
                  button.element_id, button.class_name, str(button.visible), str(button.disabled)))
        for button in bundle.buttons
    ))
    states = tuple(sorted(
        "|".join((_form_key(form), str(form.expected_control_count), str(form.visible_control_count),
                  str(form.nonempty_control_count), str(len(form.search_buttons)), str(len(form.reset_buttons))))
        for form in preflight.forms
    ))
    return forms, controls, buttons, states


def compare_signatures(previous: tuple[tuple[str, ...], ...], current: tuple[tuple[str, ...], ...]) -> tuple[str, ...]:
    labels = ("formulaires", "contrôles", "boutons", "états de précontrôle (booléens/counts)")
    lines: list[str] = []
    for index, label in enumerate(labels):
        before, after = set(previous[index]), set(current[index])
        added, removed = sorted(after - before), sorted(before - after)
        if not added and not removed:
            lines.append(f"- {label} : aucune différence structurelle détectée.")
            continue
        lines.append(f"- {label} : {len(added)} ajout(s), {len(removed)} retrait(s)/changement(s).")
        lines.extend(f"  + {item[:220]}" for item in added[:8])
        lines.extend(f"  - {item[:220]}" for item in removed[:8])
    return tuple(lines)


def format_real_form_report(
    page: Any,
    bundle: FormDiagnosticBundle,
    stage: str,
    preflight: SearchPreflightSnapshot,
    previous: tuple[str, tuple[tuple[str, ...], ...]] | None = None,
) -> str:
    """Assemble le rapport lisible, copiable et sauvegardable de la capture courante."""
    if stage not in DIAGNOSTIC_STAGE_KEYS:
        raise ValueError("Étape de diagnostic inconnue.")
    stage_label = dict(DIAGNOSTIC_STAGES)[stage]
    lines = [
        "DIAGNOSTIC STRUCTUREL RÉEL — PERSONNE MORALE",
        f"Capture explicitement demandée : {stage_label}",
        "Lecture seule : aucune navigation, saisie, sélection, suggestion, recherche ou soumission effectuée.",
        "Confidentialité : aucune valeur de champ/option, donnée personnelle, cookie, session, jeton ou réponse réseau affiché.",
        "Action et URL assainies; seules des métadonnées structurelles et des booléens/comptages de préremplissage sont utilisés.",
        "",
        f"Route de recherche reconnue : {'oui' if preflight.route_matches else 'non'} · section détectée : {page.section or '—'}",
        f"Marqueur/portlet de recherche détaillée recensé : {'oui' if page.portlet_scope_found else 'non'}",
        f"Formulaires visibles (frames accessibles) : {preflight.visible_form_count}",
        f"Contrôles correspondant au suffixe attendu {preflight.expected_suffix} dans le DOM : {preflight.global_expected_control_count}",
        "",
        "RÈGLES DE VALIDATION EXISTANTES (observées/documentées, moteur inchangé)",
        "- La page doit être sur la route officielle; le mode demandé doit correspondre au mode sélectionné. Tâche 12 compare le marqueur (URL assainie + titre) capturé après sélection et au précontrôle.",
        "- Un seul formulaire correspondant parmi les formulaires visibles, contenant le contrôle nommé attendu (_wilcom) une seule fois, avec action officielle et méthode POST.",
        "- Le contrôle attendu doit être visible, activé, vide et conforme au composant/type mappé; tous les contrôles utilisateur visibles du formulaire doivent être vides.",
        "- Exactement un bouton visible, activé et libellé exactement « Rechercher » dans le formulaire.",
        "- Le marqueur historique et un éventuel changement passé du DOM ne peuvent pas être reconstruits après coup; la comparaison de captures ci-dessous peut révéler une différence entre états.",
        "- Le précontrôle Tâche 12 ne calcule pas de hash du DOM; les comparaisons structurelles de ce rapport sont diagnostiques et n'ajoutent aucune condition de soumission.",
        "",
        "SECTION « Rechercher par l'information de la société »",
    ]
    wanted_section = _fold("Rechercher par l'information de la société")
    observed_sections = list(dict.fromkeys(
        text for text in (
            *(form.title for form in bundle.forms),
            *(field.container_label for field in bundle.all_controls),
            *(field.section_label for field in bundle.all_controls),
        ) if text
    ))
    matched_sections = tuple(text for text in observed_sections if wanted_section in _fold(text))
    if matched_sections:
        lines.append("En-tête(s) correspondant(s) repéré(s) dans la hiérarchie DOM : " + " · ".join(matched_sections[:8]))
    else:
        lines.append("En-tête exact non détecté dans les titres de formulaire/fieldset/section; consulter conteneurs et hiérarchie dans l'inventaire ci-dessous.")
    associate_sections = tuple(
        text for text in observed_sections
        if "information de l'associe" in _fold(text) or "informations de l'associe" in _fold(text)
    )
    lines.append(
        "Section associée repérée : " + " · ".join(associate_sections[:8])
        if associate_sections else "Section « Informations de l'associé » non détectée comme en-tête structurel."
    )
    lines.extend((
        "",
        "PRÉCONTRÔLE OBSERVATIONNEL — conditions actuelles de Tâche 12 (sans soumettre)",
    ))
    lines.extend(f"- {item}" for item in analyze_preflight(preflight))
    lines.extend(("", "FORMULAIRES ÉVALUÉS PAR LE PRÉCONTRÔLE"))
    if not preflight.forms:
        lines.append("Aucun formulaire accessible à détailler.")
    for form in preflight.forms:
        lines.append(
            f"- {form.frame_name} · id={form.form_id or '—'} · name={form.form_name or '—'} · "
            f"action={form.action or 'implicite (page courante)'} · method={form.method} · "
            f"class={form.class_name or '—'} · role={form.role or '—'} · visible={'oui' if form.visible else 'non'} · "
            f"sélecteur={form.css_selector or '—'}"
        )
        lines.append(
            f"  Contrôle attendu={form.expected_control_count} · contrôles visibles={form.visible_control_count} · "
            f"non vides={form.nonempty_control_count} (valeurs jamais affichées) · "
            f"Rechercher associé(s)={sum(button.visible and button.associated for button in form.search_buttons)} · "
            f"Réinitialiser associé(s)={sum(button.visible and button.associated for button in form.reset_buttons)}"
        )
        if form.expected_controls:
            lines.append("  Contrôle(s) attendu(s) : " + ", ".join(form.expected_controls[:8]))
        if form.nonempty_controls:
            lines.append("  Contrôle(s) non vide(s), valeur omise : " + ", ".join(form.nonempty_controls[:8]))
        for button in (*form.search_buttons, *form.reset_buttons):
            lines.append(
                f"  Bouton {button.text or 'sans libellé'} · visible={'oui' if button.visible else 'non'} · "
                f"activé={'oui' if button.enabled else 'non'} · associé={'oui' if button.associated else 'non'} · "
                f"sélecteur={button.selector or '—'}"
            )

    lines.extend(("", *expected_field_rows(bundle), "", "COMPARAISON DES CAPTURES"))
    if previous is None:
        lines.append("Aucune capture antérieure n'est encore mémorisée dans cette session Chromium.")
    else:
        previous_label, signature = previous
        current_signature = snapshot_signature(bundle, preflight)
        lines.append(f"Comparaison avec : {dict(DIAGNOSTIC_STAGES).get(previous_label, previous_label)}")
        if signature != current_signature:
            lines.append("DYNAMIQUE / DOM MODIFIÉ : une ou plusieurs métadonnées structurelles ou un état booléen/comptage diffère entre les captures.")
        else:
            lines.append("Aucune différence structurelle/booléenne détectée entre ces deux captures.")
        lines.extend(compare_signatures(signature, current_signature))
    safe_page = SimpleNamespace(
        title="Titre de page omis (non nécessaire au diagnostic)",
        section=page.section,
        url=page.url,
        portlet_scopes=getattr(page, "portlet_scopes", ()),
    )
    safe_inventory = format_diagnostic(
        safe_page,
        bundle,
        include_endpoint_metadata=False,
        include_clickables=False,
        mask_nonstandard_button_labels=True,
    )
    lines.extend(("", "INVENTAIRE DOM COMPLET (endpoint/API direct omis)", safe_inventory))
    return "\n".join(lines)
