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
_SECRET_WORDS = re.compile(r"password|passwd|token|secret|csrf|cookie|session|auth", re.IGNORECASE)
_EMAIL = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_LONG_NUMBER = re.compile(r"(?<!\d)\d{6,}(?!\d)")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]+)?\b")
_ACTION_TEXT = re.compile(
    r"rechercher|recherche|chercher|search|soumettre|submit|valider|appliquer|filtrer|effacer|"
    r"réinitialiser|reinitialiser|annuler|ajouter|confirmer|ouvrir|connexion|"
    r"suivant|précédent|precedent|enregistrer|continuer|\bok\b",
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


@dataclass(frozen=True, slots=True)
class FormDiagnostic:
    title: str
    fields: tuple[FormFieldDiagnostic, ...]
    frame_name: str = "Document principal"
    frame_url: str = ""


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
    cleaned = _clean_text(value, limit)
    if _SECRET_WORDS.search(cleaned) or "[masqué]" in cleaned:
        return ""
    return cleaned


def sanitize_metadata_text(value: object, limit: int = 160) -> str:
    """Nettoie un texte structurel sans émettre d'indice ressemblant à un secret."""
    return _safe_attribute("text", value, limit)


def _safe_data_attributes(raw: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(raw, dict):
        return ()
    safe: list[tuple[str, str]] = []
    for key, value in raw.items():
        normalized_key = str(key).casefold()
        if not re.fullmatch(r"data-[a-z0-9][a-z0-9_.:-]{0,59}", normalized_key):
            continue
        if _SECRET_WORDS.search(normalized_key):
            continue
        cleaned = _safe_attribute(normalized_key, value)
        if cleaned:
            safe.append((normalized_key, cleaned))
    return tuple(safe)


def _options_allowed(label: str) -> bool:
    normalized = _fold(label)
    if any(word in normalized for word in _SENSITIVE_LABELS):
        # Wilaya/commune restent des vocabulaires contrôlés, même si le libellé mentionne « inscription ».
        if not any(word in normalized for word in ("wilaya", "commune")):
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

    grouped: dict[tuple[str, str, str], tuple[str, list[FormFieldDiagnostic]]] = {}
    raw_forms = raw.get("form_entries", [])
    if isinstance(raw_forms, list):
        for item in raw_forms:
            if not isinstance(item, dict):
                continue
            frame_name, frame_url = _frame_context(item)
            form_id = _safe_attribute("form_id", item.get("form_id"), 100) or "formulaire-non-identifié"
            form_title = _safe_attribute("form_title", item.get("form_title"), 120) or "Formulaire"
            grouped.setdefault((frame_name, frame_url, form_id), (form_title, []))

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
            grouped.setdefault(key, (form_title, []))[1].append(field)
    forms = tuple(
        FormDiagnostic(title=title, fields=tuple(fields), frame_name=frame_name, frame_url=frame_url)
        for (frame_name, frame_url, _form_id), (title, fields) in grouped.items()
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
    return FormFieldDiagnostic(
        label=label,
        associated_text=associated,
        html_type=html_type or "inconnu",
        role=role or "non défini",
        name=_safe_attribute("name", name_raw, 100),
        element_id=_safe_attribute("id", item.get("id"), 100),
        placeholder=_safe_attribute("placeholder", item.get("placeholder"), 120),
        data_attributes=_safe_data_attributes(item.get("data_attributes")),
        disabled=bool(item.get("disabled", False)),
        option_count=option_count,
        options=options,
        options_redacted=options_redacted,
        hierarchy=_safe_hierarchy(item.get("hierarchy")),
        tag_name=_safe_attribute("tag", item.get("tag_name"), 40) or native_tag,
        aria_label=aria_label,
        aria_labelledby=aria_labelledby,
        visible=bool(item.get("visible", True)),
        frame_name=frame_name,
        frame_url=frame_url,
        class_name=_safe_attribute("class", item.get("class_name"), 160),
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
                data_attributes=_safe_data_attributes(item.get("data_attributes")),
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
        f"Class : {field.class_name or '—'}",
        f"Placeholder : {field.placeholder or '—'}",
        f"aria-label : {field.aria_label or '—'}",
        f"aria-labelledby : {field.aria_labelledby or '—'}",
        f"Disabled : {'oui' if field.disabled else 'non'}",
        f"Visible : {'oui' if field.visible else 'non'}",
        f"Hiérarchie DOM : {' > '.join(field.hierarchy) or '—'}",
        f"Attributs data-* : {_format_attributes(field.data_attributes)}",
        f"Nombre d'options : {field.option_count}",
        f"Options : {options}",
    ]


def _format_button(button: ButtonDiagnostic) -> list[str]:
    return [
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

    lines.extend(("", "FORMULAIRES"))
    if not bundle.forms:
        lines.append("Aucun formulaire repéré.")
    for form_index, form in enumerate(bundle.forms, start=1):
        lines.extend(("", f"Formulaire {form_index} : {form.title} · Frame : {form.frame_name}"))
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


def _format_attributes(attributes: tuple[tuple[str, str], ...]) -> str:
    return ", ".join(f"{key}={value}" for key, value in attributes) or "—"


class _FixtureHTMLParser(HTMLParser):
    """Parseur de fixtures de test qui ignore les attributs `value` dès la lecture."""

    _ALLOWED_ATTRIBUTES = frozenset(
        {
            "id", "name", "type", "role", "placeholder", "aria-label", "aria-labelledby", "for", "href",
            "title", "disabled", "aria-disabled", "hidden", "checked", "selected", "multiple", "required", "tabindex",
            "contenteditable", "class", "src", "data-testid",
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
        self.form_index = 0
        self.form_stack: list[tuple[str, str]] = []
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
            "visible": "hidden" not in safe,
            "form_title": self.form_title,
            "nature": nature,
            "hierarchy": [item["legend"] for item in self.fieldsets if item["legend"]],
        }

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        safe = self._attributes(attrs)
        attr_names = {key for key, _value in attrs}
        role = safe.get("role", "").casefold()
        if tag == "form":
            self.form_stack.append((self.form_id, self.form_title))
            self.form_index += 1
            self.form_id = safe.get("id") or f"formulaire-{self.form_index}"
            self.form_title = safe.get("aria-label") or safe.get("title") or self.form_id or "Formulaire"
            self.raw["form_entries"].append(
                {"form_id": self.form_id, "form_title": self.form_title}
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
                    "placeholder": safe.get("placeholder", ""),
                    "data_attributes": {key: value for key, value in safe.items() if key.startswith("data-")},
                    "disabled": "disabled" in safe or safe.get("aria-disabled") == "true",
                    "visible": "hidden" not in safe,
                    "option_count": 0,
                    "options": [],
                    "form_id": self.form_id,
                    "form_title": self.form_title,
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
            self.form_id, self.form_title = self.form_stack.pop()

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
