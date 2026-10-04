"""Extraction et rendu de métadonnées DOM sans lire les valeurs des champs."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

from sidjily.sidjilcom.config import DEFAULT_SIDJILCOM_URL
from sidjily.sidjilcom.selectors import sanitize_current_url

_ENUMERATION_LABELS = (
    "type de personne", "personne physique", "personne morale", "wilaya", "commune",
    "secteur", "activite", "forme juridique", "conformite", "etat commercant",
    "nationalite", "qualite",
)
_SENSITIVE_LABELS = (
    "nom", "prenom", "email", "e-mail", "telephone", "date", "numero", "inscription",
    "raison sociale", "commercial", "dirigeant", "adresse", "nif", "nis",
)
_SECRET_WORDS = re.compile(
    r"password|passwd|token|secret|csrf|cookie|session|auth|credential|bearer|api.?key|access.?key",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_LONG_NUMBER = re.compile(r"(?<!\d)\d{6,}(?!\d)")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]+)?\b")
_ACTION_TEXT = re.compile(
    r"rechercher|recherche|chercher|search|soumettre|submit|valider|appliquer|filtrer|effacer|"
    r"réinitialiser|reinitialiser|annuler|ajouter|confirmer|ouvrir|connexion|"
    r"suivant|précédent|precedent|enregistrer|continuer|\bok\b",
    re.IGNORECASE,
)
_SEARCH_BUTTON_TEXT = re.compile(r"rechercher|chercher|search|soumettre|submit", re.IGNORECASE)
_RESET_BUTTON_TEXT = re.compile(r"réinitialiser|reinitialiser|effacer|reset", re.IGNORECASE)
_SAFE_DATA_ATTRIBUTE = re.compile(
    r"data-(?:test(?:id)?|qa|cy|automation-id|field(?:-name)?|role|select2-id|"
    r"ajax(?:--?(?:url|type|method))?|api(?:-(?:url|method))?|"
    r"endpoint(?:-url)?|url|href|method|remote|controller|component|widget|target|"
    r"parent(?:-id)?|depends-on|dependent-on|dependency|cascade)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class FormFieldDiagnostic:
    label: str
    associated_text: str
    html_type: str
    role: str
    name: str
    element_id: str
    placeholder: str
    data_attributes: tuple[tuple[str, str], ...]
    disabled: bool
    option_count: int
    options: tuple[str, ...]
    options_redacted: bool
    hierarchy: tuple[str, ...]
    tag_name: str = ""
    aria_label: str = ""
    aria_labelledby: str = ""
    visible: bool = True
    frame_name: str = "Document principal"
    frame_url: str = ""
    class_name: str = ""
    required: bool = False
    readonly: bool = False
    list_id: str = ""
    css_selector: str = ""
    aria_autocomplete: str = ""
    component_type: str = ""
    ajax_endpoint: str = ""
    ajax_method: str = ""


@dataclass(frozen=True, slots=True)
class FormDiagnostic:
    title: str
    fields: tuple[FormFieldDiagnostic, ...]
    frame_name: str = "Document principal"
    frame_url: str = ""
    element_id: str = ""
    name: str = ""
    action: str = ""
    method: str = "GET"


@dataclass(frozen=True, slots=True)
class ButtonDiagnostic:
    text: str
    html_type: str
    role: str
    name: str
    element_id: str
    data_attributes: tuple[tuple[str, str], ...]
    disabled: bool
    hierarchy: tuple[str, ...]
    tag_name: str = "button"
    aria_label: str = ""
    aria_labelledby: str = ""
    placeholder: str = ""
    visible: bool = True
    frame_name: str = "Document principal"
    frame_url: str = ""
    nature: str = "bouton"
    class_name: str = ""
    href: str = ""
    form_id: str = ""
    form_action: str = ""
    form_method: str = ""
    onclick_present: bool = False
    onclick_handler: str = ""


@dataclass(frozen=True, slots=True)
class FrameDiagnostic:
    name: str
    url: str
    accessible: bool
    form_count: int
    control_count: int
    status: str = "inspectée"


@dataclass(frozen=True, slots=True)
class FormDiagnosticBundle:
    forms: tuple[FormDiagnostic, ...]
    buttons: tuple[ButtonDiagnostic, ...]
    outside_controls: tuple[FormFieldDiagnostic, ...] = ()
    frames: tuple[FrameDiagnostic, ...] = ()
    clickables: tuple[ButtonDiagnostic, ...] = ()

    @property
    def all_controls(self) -> tuple[FormFieldDiagnostic, ...]:
        return tuple(field for form in self.forms for field in form.fields) + self.outside_controls


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _clean_text(value: object, limit: int = 160) -> str:
    """Normalise du texte structurel et masque les motifs ressemblant à des secrets."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = _EMAIL.sub("[masqué]", text)
    text = _JWT.sub("[masqué]", text)
    text = _LONG_NUMBER.sub("[masqué]", text)
    return text[:limit]


def _safe_attribute(key: str, value: object, limit: int = 120) -> str:
    if _SECRET_WORDS.search(key) or key.casefold() == "value":
        return ""
    raw_text = str(value or "")
    if any(pattern.search(raw_text) for pattern in (_SECRET_WORDS, _EMAIL, _JWT, _LONG_NUMBER)):
        return ""
    cleaned = _clean_text(raw_text, limit)
    if _SECRET_WORDS.search(cleaned) or "[masqué]" in cleaned:
        return ""
    return cleaned


def sanitize_metadata_text(value: object, limit: int = 160) -> str:
    """Nettoie un texte structurel sans émettre d'indice ressemblant à un secret."""
    return _safe_attribute("text", value, limit)


def _is_endpoint_attribute(key: str, value: object) -> bool:
    normalized = key.casefold()
    if any(marker in normalized for marker in ("url", "href", "endpoint")):
        return True
    return normalized in {"data-api", "data-ajax", "data-remote"} and str(value or "").strip().startswith(("/", "https://"))


def _safe_data_attributes(raw: object, frame_url: str = "") -> tuple[tuple[str, str], ...]:
    if not isinstance(raw, dict):
        return ()
    safe: list[tuple[str, str]] = []
    for key, value in raw.items():
        normalized_key = str(key).casefold()
        if not _SAFE_DATA_ATTRIBUTE.fullmatch(normalized_key):
            continue
        if _SECRET_WORDS.search(normalized_key) or re.search(r"api.?key|access.?key|credential|bearer", normalized_key):
            continue
        if _is_endpoint_attribute(normalized_key, value):
            cleaned = _safe_href(value, frame_url or DEFAULT_SIDJILCOM_URL)
            if cleaned == "URL indisponible":
                cleaned = ""
        else:
            cleaned = _safe_attribute(normalized_key, value)
        if cleaned:
            safe.append((normalized_key, cleaned))
    return tuple(safe)


def _options_allowed(label: str) -> bool:
    normalized = _fold(label)
    sensitive_matches = [word for word in _SENSITIVE_LABELS if word in normalized]
    location = any(word in normalized for word in ("wilaya", "commune"))
    if sensitive_matches and (
        not location or any(word != "inscription" for word in sensitive_matches)
    ):
        return False
    return any(word in normalized for word in _ENUMERATION_LABELS)


def _safe_options(label: str, raw_options: object, raw_count: object) -> tuple[int, tuple[str, ...], bool]:
    try:
        count = max(0, int(raw_count or 0))
    except (TypeError, ValueError):
        count = 0
    if not isinstance(raw_options, (list, tuple)) or not _options_allowed(label):
        return count, (), count > 0
    options: list[str] = []
    for raw in raw_options:
        text = _clean_text(raw, 80)
        if not text or "[masqué]" in text or _EMAIL.search(text) or _LONG_NUMBER.search(text):
            continue
        if text not in options:
            options.append(text)
    return count, tuple(options), False


def _safe_hierarchy(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(text for item in raw[:10] if (text := _safe_attribute("hierarchy", item, 100)) and "[masqué]" not in text)


def build_form_diagnostics(raw: object) -> FormDiagnosticBundle:
    """Assainit un instantané multi-frame sans accepter de valeurs de contrôles."""
    if not isinstance(raw, dict):
        return FormDiagnosticBundle((), ())

    frames: list[FrameDiagnostic] = []
    raw_frames = raw.get("frames", [])
    if isinstance(raw_frames, list):
        for item in raw_frames:
            if not isinstance(item, dict):
                continue
            frame_name = _safe_attribute("frame_name", item.get("name"), 100) or "Frame"
            frame_url = sanitize_current_url(str(item.get("url", "")), DEFAULT_SIDJILCOM_URL)
            frames.append(
                FrameDiagnostic(
                    name=frame_name,
                    url=frame_url,
                    accessible=bool(item.get("accessible", False)),
                    form_count=max(0, _as_int(item.get("form_count", 0))),
                    control_count=max(0, _as_int(item.get("control_count", 0))),
                    status=("inspectée" if item.get("accessible", False) else "inaccessible/non disponible"),
                )
            )

    grouped: dict[
        tuple[str, str, str],
        tuple[str, list[FormFieldDiagnostic], str, str, str, str],
    ] = {}
    raw_forms = raw.get("form_entries", [])
    if isinstance(raw_forms, list):
        for item in raw_forms:
            if not isinstance(item, dict):
                continue
            frame_name, frame_url = _frame_context(item)
            form_id = _safe_attribute("form_id", item.get("form_id"), 100) or "formulaire-non-identifié"
            form_title = _safe_attribute("form_title", item.get("form_title"), 120) or "Formulaire"
            grouped.setdefault(
                (frame_name, frame_url, form_id),
                (
                    form_title,
                    [],
                    _safe_attribute("form_id", item.get("form_id"), 100),
                    _safe_attribute("form_name", item.get("form_name"), 100),
                    _safe_href(item.get("action"), frame_url),
                    _safe_method(item.get("method")),
                ),
            )

    outside_controls: list[FormFieldDiagnostic] = []
    raw_fields = raw.get("fields", [])
    if isinstance(raw_fields, list):
        for item in raw_fields:
            if not isinstance(item, dict):
                continue
            field = _build_field(item)
            if field is None:
                continue
            frame_name, frame_url = field.frame_name, field.frame_url
            form_id = _safe_attribute("form_id", item.get("form_id"), 100)
            if not form_id or form_id == "hors-formulaire":
                outside_controls.append(field)
                continue
            form_title = _safe_attribute("form_title", item.get("form_title"), 120) or "Formulaire"
            key = (frame_name, frame_url, form_id)
            grouped.setdefault(
                key,
                (
                    form_title,
                    [],
                    form_id,
                    _safe_attribute("form_name", item.get("form_name"), 100),
                    _safe_href(item.get("form_action"), frame_url),
                    _safe_method(item.get("form_method")),
                ),
            )[1].append(field)
    forms = tuple(
        FormDiagnostic(
            title=title,
            fields=tuple(fields),
            frame_name=frame_name,
            frame_url=frame_url,
            element_id=element_id,
            name=form_name,
            action=action,
            method=method,
        )
        for (frame_name, frame_url, _form_id), (title, fields, element_id, form_name, action, method) in grouped.items()
    )

    buttons = _build_buttons(raw.get("buttons", []), "bouton")
    clickables = _build_buttons(raw.get("clickables", []), "élément cliquable")
    return FormDiagnosticBundle(
        forms=forms,
        buttons=buttons,
        outside_controls=tuple(outside_controls),
        frames=tuple(frames),
        clickables=clickables,
    )


def _as_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _frame_context(item: dict[str, Any]) -> tuple[str, str]:
    name = _safe_attribute("frame_name", item.get("frame_name"), 100) or "Document principal"
    url = sanitize_current_url(str(item.get("frame_url", "")), DEFAULT_SIDJILCOM_URL)
    return name, url


def _safe_method(raw_method: object) -> str:
    method = str(raw_method or "GET").strip().upper()
    return method if method in {"GET", "POST", "DIALOG"} else "GET (défaut HTML)"


def _safe_href(raw_href: object, frame_url: str) -> str:
    """Keep useful destinations while removing query, fragment, credentials and token-like paths."""
    href = str(raw_href or "").strip()
    if not href:
        return ""
    path_only = re.split(r"[?#]", href, maxsplit=1)[0]
    if not path_only:
        return ""
    destination = urljoin(frame_url, path_only)
    safe = sanitize_current_url(destination, DEFAULT_SIDJILCOM_URL)
    parsed = urlsplit(safe)
    if _SECRET_WORDS.search(parsed.path) or _EMAIL.search(parsed.path) or _LONG_NUMBER.search(parsed.path) or _JWT.search(parsed.path):
        return f"{parsed.scheme}://{parsed.netloc}/[chemin masqué]"
    return safe


def _safe_interactive_text(raw_text: object) -> str:
    """Keep non-sensitive visible labels; trim personalized suffixes from action buttons."""
    text = _safe_attribute("text", raw_text, 160)
    if not text:
        return "Texte masqué (potentiellement sensible)"
    action = _ACTION_TEXT.search(text)
    if action and text[action.end():].strip(" .,:;!?-–—»"):
        return _clean_text(action.group(0), 40)
    return text


def _safe_onclick_handler(raw_handler: object) -> str:
    """Expose only a parameter-free function name, never inline JavaScript source."""
    source = str(raw_handler or "").strip()
    if not source:
        return ""
    match = re.fullmatch(
        r"(?:return\s+)?([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\(\s*\)\s*;?",
        source,
    )
    if not match:
        return "code masqué"
    return _safe_attribute("onclick", f"{match.group(1)}()", 100) or "code masqué"


def _endpoint_details(
    attributes: tuple[tuple[str, str], ...], frame_url: str
) -> tuple[str, str]:
    endpoint = ""
    for key, value in attributes:
        normalized = key.casefold()
        if normalized.startswith("data-") and _is_endpoint_attribute(normalized, value):
            endpoint = _safe_href(value, frame_url)
            if endpoint and endpoint != "URL indisponible":
                break
    method = ""
    for key, value in attributes:
        if key.casefold() in {"data-method", "data-ajax-method", "data-ajax--method", "data-api-method"}:
            candidate = value.strip().upper()
            if candidate in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                method = candidate
                break
    return endpoint, method


def _component_type(
    tag_name: str,
    html_type: str,
    role: str,
    class_name: str,
    aria_autocomplete: str,
    list_id: str,
    attributes: tuple[tuple[str, str], ...],
    endpoint: str,
) -> str:
    classes = class_name.casefold()
    data_names = " ".join(key.casefold() for key, _value in attributes)
    if "select2" in classes or "data-select2-id" in data_names:
        return "Select2"
    if "chosen" in classes:
        return "widget JavaScript Chosen"
    if any(marker in classes for marker in ("datepicker", "date-picker", "daterangepicker", "flatpickr", "calendar")):
        return "composant JavaScript date / période"
    if tag_name == "select":
        return "select HTML classique"
    if list_id:
        return "input avec datalist"
    if aria_autocomplete in {"list", "both", "inline"} or any(
        marker in classes for marker in ("autocomplete", "typeahead", "ui-autocomplete")
    ):
        return "autocomplete / liste dynamique"
    if endpoint:
        return "composant JavaScript avec endpoint déclaré"
    if role in {"combobox", "listbox"}:
        return "composant JavaScript / ARIA"
    if tag_name == "input" and html_type in {"text", "search"}:
        return "champ texte HTML"
    if html_type in {"date", "datetime-local", "month", "week"}:
        return "champ date HTML"
    return "contrôle HTML / ARIA"


def _css_selector(tag_name: str, element_id: str, name: str, role: str) -> str:
    def quoted(value: str) -> str:
        escaped = "".join(
            f"\\{ord(char):x} " if ord(char) < 0x20 or ord(char) == 0x7F
            else f"\\{char}" if char in {"\\", '"'}
            else char
            for char in value
        )
        return f'"{escaped}"'

    if element_id:
        return f"[id={quoted(element_id)}]"
    if name:
        return f"{tag_name}[name={quoted(name)}]"
    if role:
        return f"[role={quoted(role)}]"
    return tag_name


def _build_field(item: dict[str, Any]) -> FormFieldDiagnostic | None:
    html_type = _safe_attribute("type", item.get("html_type"), 40).lower()
    name_raw = str(item.get("name", ""))
    if html_type in {"password", "hidden"} or _SECRET_WORDS.search(name_raw):
        return None
    aria_label = _safe_attribute("aria_label", item.get("aria_label"), 120)
    aria_labelledby = _safe_attribute("aria_labelledby", item.get("aria_labelledby"), 120)
    label = _safe_attribute("label", item.get("label"), 120) or aria_label or "Champ sans libellé"
    associated = _safe_attribute("associated_text", item.get("associated_text"), 160) or label
    native_tag = html_type if html_type in {"select", "textarea"} else "input"
    role = _safe_attribute("role", item.get("role"), 40) or _native_role(native_tag, html_type)
    option_count, options, options_redacted = _safe_options(
        label, item.get("options", []), item.get("option_count", 0)
    )
    frame_name, frame_url = _frame_context(item)
    data_attributes = _safe_data_attributes(item.get("data_attributes"), frame_url)
    class_name = _safe_attribute("class", item.get("class_name"), 160)
    aria_autocomplete = _safe_attribute("aria_autocomplete", item.get("aria_autocomplete"), 40).lower()
    endpoint, endpoint_method = _endpoint_details(data_attributes, frame_url)
    tag_name = _safe_attribute("tag", item.get("tag_name"), 40) or native_tag
    safe_name = _safe_attribute("name", name_raw, 100)
    element_id = _safe_attribute("id", item.get("id"), 100)
    list_id = _safe_attribute("list", item.get("list_id"), 100)
    css_selector = _css_selector(tag_name, element_id, safe_name, role)
    return FormFieldDiagnostic(
        label=label,
        associated_text=associated,
        html_type=html_type or "inconnu",
        role=role or "non défini",
        name=safe_name,
        element_id=element_id,
        placeholder=_safe_attribute("placeholder", item.get("placeholder"), 120),
        data_attributes=data_attributes,
        disabled=bool(item.get("disabled", False)),
        option_count=option_count,
        options=options,
        options_redacted=options_redacted,
        hierarchy=_safe_hierarchy(item.get("hierarchy")),
        tag_name=tag_name,
        aria_label=aria_label,
        aria_labelledby=aria_labelledby,
        visible=bool(item.get("visible", True)),
        frame_name=frame_name,
        frame_url=frame_url,
        class_name=class_name,
        required=bool(item.get("required", False)),
        readonly=bool(item.get("readonly", False)),
        list_id=list_id,
        css_selector=css_selector,
        aria_autocomplete=aria_autocomplete,
        component_type=_component_type(
            tag_name, html_type, role or "", class_name, aria_autocomplete,
            list_id, data_attributes, endpoint
        ),
        ajax_endpoint=endpoint,
        ajax_method=endpoint_method,
    )


def _build_buttons(raw_items: object, default_nature: str) -> tuple[ButtonDiagnostic, ...]:
    if not isinstance(raw_items, list):
        return ()
    buttons: list[ButtonDiagnostic] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        text = _safe_interactive_text(item.get("text"))
        hierarchy = _safe_hierarchy(item.get("hierarchy"))
        form_title = _safe_attribute("form_title", item.get("form_title"), 120)
        if form_title:
            hierarchy = (form_title, *hierarchy)
        frame_name, frame_url = _frame_context(item)
        tag_name = _safe_attribute("tag", item.get("tag_name"), 40) or "button"
        role = _safe_attribute("role", item.get("role"), 40)
        default_type = "link" if tag_name == "a" else "button"
        default_role = "link" if tag_name == "a" else "button"
        buttons.append(
            ButtonDiagnostic(
                text=text,
                html_type=_safe_attribute("type", item.get("html_type"), 40) or default_type,
                role=role or default_role,
                name=_safe_attribute("name", item.get("name"), 100),
                element_id=_safe_attribute("id", item.get("id"), 100),
                data_attributes=_safe_data_attributes(item.get("data_attributes"), frame_url),
                disabled=bool(item.get("disabled", False)),
                hierarchy=hierarchy,
                tag_name=tag_name,
                aria_label=_safe_attribute("aria_label", item.get("aria_label"), 120),
                aria_labelledby=_safe_attribute("aria_labelledby", item.get("aria_labelledby"), 120),
                placeholder=_safe_attribute("placeholder", item.get("placeholder"), 120),
                visible=bool(item.get("visible", True)),
                frame_name=frame_name,
                frame_url=frame_url,
                nature=_safe_attribute("nature", item.get("nature"), 60) or default_nature,
                class_name=_safe_attribute("class", item.get("class_name"), 160),
                href=_safe_href(item.get("href"), frame_url),
                form_id=_safe_attribute("form_id", item.get("form_id"), 100),
                form_action=_safe_href(item.get("form_action"), frame_url),
                form_method=_safe_method(item.get("form_method")) if item.get("form_method") else "",
                onclick_present=bool(item.get("onclick_present", False)),
                onclick_handler=_safe_onclick_handler(item.get("onclick_handler")),
            )
        )
    return tuple(buttons)


def _format_field(field: FormFieldDiagnostic) -> list[str]:
    options = ", ".join(field.options) if field.options else (
        "textes masqués (potentiellement sensibles)" if field.options_redacted else "—"
    )
    return [
        f"Label : {field.label}",
        f"Texte associé : {field.associated_text}",
        f"Tag / type : {field.tag_name} / {field.html_type}",
        f"Role : {field.role}",
        f"Name : {field.name or '—'}",
        f"ID : {field.element_id or '—'}",
        f"Sélecteur CSS : {field.css_selector or '—'}",
        f"Class : {field.class_name or '—'}",
        f"Liste associée : {field.list_id or '—'}",
        f"Placeholder : {field.placeholder or '—'}",
        f"aria-label : {field.aria_label or '—'}",
        f"aria-labelledby : {field.aria_labelledby or '—'}",
        f"aria-autocomplete : {field.aria_autocomplete or '—'}",
        f"Required : {'oui' if field.required else 'non'}",
        f"Readonly : {'oui' if field.readonly else 'non'}",
        f"Disabled : {'oui' if field.disabled else 'non'}",
        f"Visible : {'oui' if field.visible else 'non'}",
        f"Hiérarchie DOM : {' > '.join(field.hierarchy) or '—'}",
        f"Attributs data-* : {_format_attributes(field.data_attributes)}",
        f"Composant : {field.component_type or 'non déterminé'}",
        f"Endpoint déclaré : {field.ajax_endpoint or '—'}",
        f"Méthode endpoint déclarée : {field.ajax_method or '—'}",
        f"Nombre d'options : {field.option_count}",
        f"Options : {options}",
    ]


def _format_button(button: ButtonDiagnostic) -> list[str]:
    lines = [
        f"Nature : {button.nature}",
        f"Tag / type : {button.tag_name} / {button.html_type}",
        f"Texte : {button.text}",
        f"Role : {button.role}",
        f"Name : {button.name or '—'}",
        f"ID : {button.element_id or '—'}",
        f"Class : {button.class_name or '—'}",
        f"href : {button.href or '—'}",
        f"aria-label : {button.aria_label or '—'}",
        f"aria-labelledby : {button.aria_labelledby or '—'}",
        f"Placeholder : {button.placeholder or '—'}",
        f"Disabled : {'oui' if button.disabled else 'non'}",
        f"Visible : {'oui' if button.visible else 'non'}",
        f"Hiérarchie DOM : {' > '.join(button.hierarchy) or '—'}",
        f"Attributs data-* : {_format_attributes(button.data_attributes)}",
        f"Frame : {button.frame_name}",
    ]
    if button.nature == "bouton" and button.tag_name != "a":
        form_id = button.form_id if button.form_id != "hors-formulaire" else ""
        lines.extend(
            (
                f"Formulaire associé : {form_id or '—'}",
                f"Action du formulaire : {button.form_action or 'implicite (page courante)'}",
                f"Méthode formulaire : {button.form_method or '—'}",
                f"onclick HTML inline : {'présent' if button.onclick_present else 'absent'}"
                + (f" · {button.onclick_handler}" if button.onclick_handler else ""),
            )
        )
    return lines


def _format_control_section(title: str, controls: tuple[FormFieldDiagnostic, ...]) -> list[str]:
    lines = ["", title]
    if not controls:
        lines.append("Aucun contrôle correspondant.")
        return lines
    for index, control in enumerate(controls, start=1):
        lines.extend(("", f"Contrôle {index} · {control.frame_name}"))
        lines.extend(_format_field(control))
    return lines


def format_diagnostic(page: Any, bundle: FormDiagnosticBundle | None = None) -> str:
    """Rend un rapport structuré par page, frames et types de contrôles."""
    bundle = bundle or FormDiagnosticBundle((), ())
    lines = [
        "PAGE",
        f"Titre : {sanitize_metadata_text(getattr(page, 'title', ''), 160) or '—'}",
        f"Section : {_clean_text(getattr(page, 'section', ''), 100) or '—'}",
        f"URL : {sanitize_current_url(str(getattr(page, 'url', '')), DEFAULT_SIDJILCOM_URL)}",
        "Confidentialité : aucune valeur de champ, cookie ou jeton collecté.",
        "",
        "FRAMES",
    ]
    if not bundle.frames:
        lines.append("Aucune frame recensée.")
    for index, frame in enumerate(bundle.frames, start=1):
        lines.append(
            f"Frame {index} : {frame.name} · URL : {frame.url} · accès : {frame.status} · "
            f"formulaires : {frame.form_count} · contrôles : {frame.control_count}"
        )

    portlet_scopes = getattr(page, "portlet_scopes", ())
    if portlet_scopes:
        lines.extend(("", "PORTLET PARENT"))
        for frame_name, tag_name, element_id, class_name in portlet_scopes:
            lines.append(
                f"Frame : {frame_name} · Tag : {tag_name or '—'} · "
                f"ID : {element_id or '—'} · Class : {class_name or '—'}"
            )

    lines.extend(("", "FORMULAIRES"))
    if not bundle.forms:
        lines.append("Aucun formulaire repéré.")
    for form_index, form in enumerate(bundle.forms, start=1):
        lines.extend(("", f"Formulaire {form_index} : {form.title} · Frame : {form.frame_name}"))
        lines.append(
            f"ID : {form.element_id or '—'} · Name : {form.name or '—'} · "
            f"Action : {form.action or 'implicite (page courante)'} · Méthode : {form.method}"
        )
        if form.frame_url:
            lines.append(f"URL frame : {form.frame_url}")
        if not form.fields:
            lines.append("Aucun contrôle de formulaire recensé.")
        for field_index, field in enumerate(form.fields, start=1):
            lines.extend(("", f"Contrôle {field_index}"))
            lines.extend(_format_field(field))

    lines.extend(("", "CONTRÔLES HORS FORMULAIRE"))
    if not bundle.outside_controls:
        lines.append("Aucun contrôle hors formulaire repéré.")
    for index, field in enumerate(bundle.outside_controls, start=1):
        lines.extend(("", f"Contrôle {index} · {field.frame_name}"))
        lines.extend(_format_field(field))

    lines.extend(("", "BOUTONS"))
    if not bundle.buttons:
        lines.append("Aucun bouton repéré.")
    for index, button in enumerate(bundle.buttons, start=1):
        lines.extend(("", f"Bouton {index}"))
        lines.extend(_format_button(button))
    lines.append("ÉLÉMENTS POTENTIELLEMENT CLIQUABLES")
    if not bundle.clickables:
        lines.append("Aucun élément cliquable supplémentaire repéré.")
    for index, element in enumerate(bundle.clickables, start=1):
        lines.extend(("", f"Élément cliquable {index}"))
        lines.extend(_format_button(element))

    all_controls = bundle.all_controls
    lines.extend(_format_control_section("SELECTS", tuple(c for c in all_controls if c.tag_name == "select" or c.role == "combobox")))
    lines.extend(_format_control_section("INPUTS", tuple(c for c in all_controls if c.tag_name == "input" or c.role in {"textbox", "searchbox"})))
    lines.extend(_format_control_section("TEXTAREAS", tuple(c for c in all_controls if c.tag_name == "textarea")))
    return "\n".join(lines)


def format_search_mode_comparison(
    physical: FormDiagnosticBundle, legal: FormDiagnosticBundle
) -> str:
    """Compare uniquement les métadonnées assainies des deux instantanés de formulaire."""
    def indexed(bundle: FormDiagnosticBundle) -> dict[str, FormFieldDiagnostic]:
        result: dict[str, FormFieldDiagnostic] = {}
        for field in bundle.all_controls:
            key = field.name or field.element_id or f"{_fold(field.label)}:{field.html_type}"
            if key in result:
                suffix = field.element_id or f"{field.label}:{field.html_type}:{len(result)}"
                key = f"{key} ({suffix})"
            result[key] = field
        return result

    physical_fields = indexed(physical)
    legal_fields = indexed(legal)
    physical_only = sorted(physical_fields.keys() - legal_fields.keys())
    legal_only = sorted(legal_fields.keys() - physical_fields.keys())
    changed: list[str] = []
    for key in sorted(physical_fields.keys() & legal_fields.keys()):
        left, right = physical_fields[key], legal_fields[key]
        left_signature = (
            left.tag_name, left.html_type, left.role, left.element_id, left.class_name,
            left.required, left.readonly, left.list_id, left.options, left.component_type,
            left.ajax_endpoint, left.ajax_method, left.data_attributes,
        )
        right_signature = (
            right.tag_name, right.html_type, right.role, right.element_id, right.class_name,
            right.required, right.readonly, right.list_id, right.options, right.component_type,
            right.ajax_endpoint, right.ajax_method, right.data_attributes,
        )
        if left_signature != right_signature:
            def field_summary(field: FormFieldDiagnostic) -> str:
                return (
                    f"{field.tag_name}/{field.html_type}, name={field.name or '—'}, "
                    f"id={field.element_id or '—'}, selector={field.css_selector or '—'}, "
                    f"class={field.class_name or '—'}, role={field.role}, "
                    f"required={field.required}, readonly={field.readonly}, "
                    f"component={field.component_type or '—'}, endpoint={field.ajax_endpoint or '—'} "
                    f"{field.ajax_method or ''}, data={_format_attributes(field.data_attributes)}, "
                    f"options={', '.join(field.options) or '—'}"
                )
            changed.append(f"{left.label or key} : {field_summary(left)} → {field_summary(right)}")

    def button_signature(button: ButtonDiagnostic) -> tuple[str, ...]:
        return (
            button.text, button.tag_name, button.html_type, button.name, button.element_id,
            button.class_name, button.role, str(button.disabled), button.form_id,
            button.form_action, button.form_method,
        )

    physical_button_set = {button_signature(button) for button in physical.buttons}
    legal_button_set = {button_signature(button) for button in legal.buttons}
    def render_button_signature(signature: tuple[str, ...]) -> str:
        text, tag, html_type, name, element_id, class_name, role, disabled, form_id, action, method = signature
        return (
            f"{text or '—'} [{tag}/{html_type}; name={name or '—'}; id={element_id or '—'}; "
            f"class={class_name or '—'}; role={role or '—'}; disabled={disabled}; "
            f"form={form_id or '—'}; {method or '—'} {action or '—'}]"
        )

    lines = [
        "COMPARAISON DES MODES (métadonnées seulement)",
        f"Personnes physiques : {len(physical_fields)} contrôle(s) recensé(s), {len(physical.buttons)} bouton(s), "
        f"{len(physical.forms)} formulaire(s).",
        f"Personnes morales : {len(legal_fields)} contrôle(s) recensé(s), {len(legal.buttons)} bouton(s), "
        f"{len(legal.forms)} formulaire(s).",
        "Structure physique : " + ("; ".join(
            f"id={form.element_id or '—'}, name={form.name or '—'} "
            f"[{form.method} · {form.action or 'action implicite'}]" for form in physical.forms
        ) or "aucun formulaire identifié"),
        "Structure morale : " + ("; ".join(
            f"id={form.element_id or '—'}, name={form.name or '—'} "
            f"[{form.method} · {form.action or 'action implicite'}]" for form in legal.forms
        ) or "aucun formulaire identifié"),
        "Spécifiques physiques : " + (", ".join(physical_only) or "aucun"),
        "Spécifiques morales : " + (", ".join(legal_only) or "aucun"),
        "Métadonnées différentes : " + ("; ".join(changed) or "aucune différence détectée"),
        "Boutons spécifiques physiques : " + (
            "; ".join(render_button_signature(item) for item in sorted(physical_button_set - legal_button_set)) or "aucun"
        ),
        "Boutons spécifiques morales : " + (
            "; ".join(render_button_signature(item) for item in sorted(legal_button_set - physical_button_set)) or "aucun"
        ),
    ]
    def matching_buttons(bundle: FormDiagnosticBundle, pattern: re.Pattern[str]) -> str:
        matches = [button for button in bundle.buttons if pattern.search(button.text)]
        return ", ".join(
            f"{button.text} [{button.tag_name}/{button.html_type}; id={button.element_id or '—'}]"
            for button in matches
        ) or "aucun identifié"

    lines.extend(
        (
            "Bouton(s) Rechercher physiques : " + matching_buttons(physical, _SEARCH_BUTTON_TEXT),
            "Bouton(s) Réinitialiser physiques : " + matching_buttons(physical, _RESET_BUTTON_TEXT),
            "Bouton(s) Rechercher morales : " + matching_buttons(legal, _SEARCH_BUTTON_TEXT),
            "Bouton(s) Réinitialiser morales : " + matching_buttons(legal, _RESET_BUTTON_TEXT),
        )
    )
    return "\n".join(lines)


def _format_attributes(attributes: tuple[tuple[str, str], ...]) -> str:
    return ", ".join(f"{key}={value}" for key, value in attributes) or "—"


class _FixtureHTMLParser(HTMLParser):
    """Parseur de fixtures de test qui ignore les attributs `value` dès la lecture."""

    _ALLOWED_ATTRIBUTES = frozenset(
        {
            "id", "name", "type", "role", "placeholder", "aria-label", "aria-labelledby", "aria-readonly", "for", "href",
            "title", "disabled", "aria-disabled", "aria-hidden", "aria-autocomplete", "aria-required", "hidden", "checked", "selected", "multiple", "required", "readonly", "list", "tabindex",
            "contenteditable", "class", "src", "action", "method", "formaction", "formmethod", "onclick", "data-testid",
            "data-test", "data-qa", "data-cy", "data-field", "data-field-name", "data-role",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.raw: dict[str, list[dict[str, Any]]] = {
            "fields": [], "buttons": [], "clickables": [], "form_entries": [], "frames": []
        }
        self.labels: dict[str, str] = {}
        self.form_title = "Formulaire"
        self.form_id = "hors-formulaire"
        self.form_action = ""
        self.form_method = "GET"
        self.form_name = ""
        self.form_index = 0
        self.form_stack: list[tuple[str, str, str, str, str]] = []
        self.fieldsets: list[dict[str, str]] = []
        self._active_label: dict[str, Any] | None = None
        self._active_legend: dict[str, Any] | None = None
        self._active_field: dict[str, Any] | None = None
        self._active_option: dict[str, Any] | None = None
        self._active_button: dict[str, Any] | None = None
        self._active_button_depth = 0

    @classmethod
    def _attributes(cls, attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        # Deliberately never copy/read the HTML `value` attribute or secret-named data attrs.
        return {
            key: value or ""
            for key, value in attrs
            if key in cls._ALLOWED_ATTRIBUTES
            or (key.startswith("data-") and not _SECRET_WORDS.search(key))
        }

    def _button_record(self, tag: str, safe: dict[str, str], default_type: str, nature: str = "bouton") -> dict[str, Any]:
        return {
            "text": safe.get("aria-label", "") or safe.get("title", ""),
            "html_type": safe.get("type", default_type),
            "role": safe.get("role", "link" if tag == "a" else "button"),
            "name": safe.get("name", ""),
            "id": safe.get("id", ""),
            "class_name": safe.get("class", ""),
            "href": safe.get("href", ""),
            "tag_name": tag,
            "aria_label": safe.get("aria-label", ""),
            "aria_labelledby": safe.get("aria-labelledby", ""),
            "placeholder": safe.get("placeholder", ""),
            "data_attributes": {key: value for key, value in safe.items() if key.startswith("data-")},
            "disabled": "disabled" in safe or safe.get("aria-disabled") == "true",
            "visible": "hidden" not in safe and safe.get("aria-hidden") != "true",
            "form_title": self.form_title,
            "form_id": self.form_id,
            "form_action": safe.get("formaction", self.form_action) or self.form_action,
            "form_method": safe.get("formmethod", self.form_method) or self.form_method,
            "onclick_present": "onclick" in safe,
            "onclick_handler": safe.get("onclick", ""),
            "nature": nature,
            "hierarchy": [item["legend"] for item in self.fieldsets if item["legend"]],
        }

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        safe = self._attributes(attrs)
        attr_names = {key for key, _value in attrs}
        role = safe.get("role", "").casefold()
        if tag == "form":
            self.form_stack.append((self.form_id, self.form_title, self.form_action, self.form_method, self.form_name))
            self.form_index += 1
            self.form_id = safe.get("id") or f"formulaire-{self.form_index}"
            self.form_title = safe.get("aria-label") or safe.get("title") or self.form_id or "Formulaire"
            self.form_action = safe.get("action", "")
            self.form_method = safe.get("method", "GET")
            self.form_name = safe.get("name", "")
            self.raw["form_entries"].append(
                {
                    "form_id": self.form_id,
                    "form_title": self.form_title,
                    "form_name": self.form_name,
                    "action": self.form_action,
                    "method": self.form_method,
                }
            )
        elif tag == "iframe":
            self.raw["frames"].append(
                {
                    "name": safe.get("title") or safe.get("name") or safe.get("id") or "Frame intégrée",
                    "url": safe.get("src", ""),
                    "accessible": False,
                    "form_count": 0,
                    "control_count": 0,
                }
            )
        elif tag == "fieldset":
            self.fieldsets.append({"legend": ""})
        elif tag == "legend" and self.fieldsets:
            self._active_legend = self.fieldsets[-1]
        elif tag == "label":
            self._active_label = {"for": safe.get("for", ""), "parts": []}

        is_native_button = tag == "button" or (
            tag == "input" and safe.get("type", "text").lower() in {"submit", "button", "reset", "image"}
        )
        is_role_button = role == "button"
        if is_native_button or is_role_button:
            record = self._button_record(
                tag, safe, safe.get("type", "submit" if tag == "button" else "button") or "button"
            )
            if tag == "button":
                self._active_button_depth += 1
                if self._active_button_depth == 1:
                    self._active_button = record
                    self._active_button["parts"] = []
            else:
                self.raw["buttons"].append(record)
            self.raw["clickables"].append({**record, "nature": "cliquable"})
            return

        if tag == "input" or tag in {"select", "textarea"} or role in {"textbox", "combobox", "searchbox"} or safe.get("contenteditable") == "true":
            html_type = safe.get("type", "text").lower() if tag == "input" else tag
            name = safe.get("name", "")
            if html_type not in {"hidden", "password"} and not _SECRET_WORDS.search(name):
                element_id = safe.get("id", "")
                label = self.labels.get(element_id, "") or safe.get("aria-label", "") or safe.get("placeholder", "")
                field = {
                    "label": label,
                    "associated_text": label,
                    "html_type": html_type,
                    "role": role or _native_role(tag, html_type),
                    "name": name,
                    "id": element_id,
                    "class_name": safe.get("class", ""),
                    "tag_name": tag,
                    "aria_label": safe.get("aria-label", ""),
                    "aria_labelledby": safe.get("aria-labelledby", ""),
                    "aria_autocomplete": safe.get("aria-autocomplete", ""),
                    "list_id": safe.get("list", ""),
                    "placeholder": safe.get("placeholder", ""),
                    "data_attributes": {key: value for key, value in safe.items() if key.startswith("data-")},
                    "required": "required" in safe or safe.get("aria-required") == "true",
                    "readonly": "readonly" in safe or safe.get("aria-readonly") == "true",
                    "disabled": "disabled" in safe or safe.get("aria-disabled") == "true",
                    "visible": "hidden" not in safe and safe.get("aria-hidden") != "true",
                    "option_count": 0,
                    "options": [],
                    "form_id": self.form_id,
                    "form_title": self.form_title,
                    "form_name": self.form_name,
                    "form_action": self.form_action,
                    "form_method": self.form_method,
                    "hierarchy": [item["legend"] for item in self.fieldsets if item["legend"]],
                }
                self.raw["fields"].append(field)
                if tag == "select":
                    self._active_field = field

        is_clickable = (
            tag == "a" or role in {"link", "menuitem"} or "onclick" in attr_names
            or ("tabindex" in safe and safe.get("tabindex", "-1").lstrip("-").isdigit()
                and int(safe.get("tabindex", "-1")) >= 0)
        )
        if is_clickable:
            self.raw["clickables"].append(
                self._button_record(tag, safe, safe.get("type", ""), "élément cliquable")
            )
        elif tag == "option" and self._active_field is not None:
            self._active_option = {"parts": []}

    def handle_endtag(self, tag: str) -> None:
        if tag == "label" and self._active_label is not None:
            text = " ".join(self._active_label["parts"])
            target_id = self._active_label["for"]
            if target_id:
                self.labels[target_id] = text
                for field in self.raw["fields"]:
                    if field["id"] == target_id and not field["label"]:
                        field["label"] = text
                        field["associated_text"] = text
            self._active_label = None
        elif tag == "legend" and self._active_legend is not None:
            self._active_legend["legend"] = " ".join(self._active_legend.get("parts", []))
            self._active_legend.pop("parts", None)
            self._active_legend = None
        elif tag == "option" and self._active_option is not None:
            if self._active_field is not None:
                self._active_field["option_count"] += 1
                if _options_allowed(self._active_field.get("label", "")):
                    self._active_field["options"].append(" ".join(self._active_option["parts"]))
            self._active_option = None
        elif tag == "select":
            self._active_field = None
        elif tag == "fieldset" and self.fieldsets:
            self.fieldsets.pop()
        elif tag == "button" and self._active_button is not None:
            self.raw["buttons"].append(self._active_button)
            self._active_button = None
            self._active_button_depth = max(0, self._active_button_depth - 1)
        elif tag == "form" and self.form_stack:
            self.form_id, self.form_title, self.form_action, self.form_method, self.form_name = self.form_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._active_label is not None:
            self._active_label["parts"].append(data)
        if self._active_legend is not None:
            self._active_legend.setdefault("parts", []).append(data)
        if self._active_option is not None:
            self._active_option["parts"].append(data)
        if self._active_button is not None:
            self._active_button.setdefault("parts", []).append(data)


def _native_role(tag: str, html_type: str) -> str:
    if tag == "select":
        return "combobox"
    if tag == "textarea" or html_type in {"text", "search", "email", "number", "date", "tel"}:
        return "textbox"
    if html_type == "checkbox":
        return "checkbox"
    if html_type == "radio":
        return "radio"
    return "control"


def diagnostics_from_html_fixture(
    html: str,
    *,
    frame_name: str = "Document principal",
    frame_url: str = DEFAULT_SIDJILCOM_URL,
) -> FormDiagnosticBundle:
    """Fabrique un snapshot de fixture sans jamais copier l'attribut HTML `value`."""
    parser = _FixtureHTMLParser()
    parser.feed(html)
    for button in parser.raw["buttons"]:
        parts = button.pop("parts", [])
        if parts and not button.get("text"):
            button["text"] = " ".join(parts)
    for collection in ("fields", "buttons", "clickables", "form_entries"):
        for item in parser.raw[collection]:
            item["frame_name"] = frame_name
            item["frame_url"] = frame_url
    iframe_frames = parser.raw["frames"]
    parser.raw["frames"] = [
        {
            "name": frame_name,
            "url": frame_url,
            "accessible": True,
            "form_count": len(parser.raw["form_entries"]),
            "control_count": len(parser.raw["fields"]) + len(parser.raw["buttons"]),
        },
        *iframe_frames,
    ]
    return build_form_diagnostics(parser.raw)
