"""Extraction et rendu de métadonnées DOM sans lire les valeurs des champs."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

from sidjily.sidjilcom.config import DEFAULT_SIDJILCOM_URL
from sidjily.sidjilcom.selectors import sanitize_current_url

SAFE_DATA_ATTRIBUTES = frozenset(
    {"data-testid", "data-test", "data-qa", "data-cy", "data-field", "data-field-name", "data-role"}
)
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


@dataclass(frozen=True, slots=True)
class FormDiagnostic:
    title: str
    fields: tuple[FormFieldDiagnostic, ...]


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


@dataclass(frozen=True, slots=True)
class FormDiagnosticBundle:
    forms: tuple[FormDiagnostic, ...]
    buttons: tuple[ButtonDiagnostic, ...]


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
        if normalized_key not in SAFE_DATA_ATTRIBUTES:
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
    for raw in raw_options[:40]:
        text = _clean_text(raw, 80)
        if not text or "[masqué]" in text or _EMAIL.search(text) or _LONG_NUMBER.search(text):
            continue
        if text not in options:
            options.append(text)
    return count, tuple(options), False


def _safe_hierarchy(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(text for item in raw[:8] if (text := _clean_text(item, 100)) and "[masqué]" not in text)


def build_form_diagnostics(raw: object) -> FormDiagnosticBundle:
    """Valide et assainit un instantané DOM ne contenant que des métadonnées autorisées."""
    if not isinstance(raw, dict):
        return FormDiagnosticBundle((), ())
    grouped: dict[str, tuple[str, list[FormFieldDiagnostic]]] = {}
    raw_fields = raw.get("fields", [])
    if isinstance(raw_fields, list):
        for item in raw_fields[:300]:
            if not isinstance(item, dict):
                continue
            html_type = _safe_attribute("type", item.get("html_type"), 40).lower()
            if html_type in {"password", "hidden"} or _SECRET_WORDS.search(str(item.get("name", ""))):
                continue
            label = _safe_attribute("label", item.get("label"), 120) or "Champ sans libellé"
            associated = _safe_attribute("associated_text", item.get("associated_text"), 160)
            name = _safe_attribute("name", item.get("name"), 100)
            element_id = _safe_attribute("id", item.get("id"), 100)
            native_tag = html_type if html_type in {"select", "textarea"} else "input"
            role = _safe_attribute("role", item.get("role"), 40) or _native_role(native_tag, html_type)
            placeholder = _safe_attribute("placeholder", item.get("placeholder"), 120)
            option_count, options, options_redacted = _safe_options(
                label, item.get("options", []), item.get("option_count", 0)
            )
            field = FormFieldDiagnostic(
                label=label,
                associated_text=associated or label,
                html_type=html_type or "inconnu",
                role=role or "non défini",
                name=name,
                element_id=element_id,
                placeholder=placeholder,
                data_attributes=_safe_data_attributes(item.get("data_attributes")),
                disabled=bool(item.get("disabled", False)),
                option_count=option_count,
                options=options,
                options_redacted=options_redacted,
                hierarchy=_safe_hierarchy(item.get("hierarchy")),
            )
            form_id = _safe_attribute("form_id", item.get("form_id"), 100) or "formulaire-non-identifié"
            form_title = _safe_attribute("form_title", item.get("form_title"), 120) or "Formulaire"
            if form_id not in grouped:
                grouped[form_id] = (form_title, [])
            grouped[form_id][1].append(field)
    forms = tuple(FormDiagnostic(title=title, fields=tuple(fields)) for title, fields in grouped.values())

    buttons: list[ButtonDiagnostic] = []
    raw_buttons = raw.get("buttons", [])
    if isinstance(raw_buttons, list):
        for item in raw_buttons[:100]:
            if not isinstance(item, dict):
                continue
            html_type = _safe_attribute("type", item.get("html_type"), 40).lower() or "button"
            text = _safe_attribute("text", item.get("text"), 80)
            action_match = _ACTION_TEXT.search(text) if text else None
            if action_match:
                # N'émettre que le libellé d'action reconnu, pas un éventuel suffixe personnalisé.
                text = _clean_text(action_match.group(0), 40)
            else:
                text = "Texte masqué (action non classifiée)"
            hierarchy = _safe_hierarchy(item.get("hierarchy"))
            form_title = _safe_attribute("form_title", item.get("form_title"), 120)
            if form_title:
                hierarchy = (form_title, *hierarchy)
            buttons.append(
                ButtonDiagnostic(
                    text=text,
                    html_type=html_type,
                    role=_safe_attribute("role", item.get("role"), 40) or "button",
                    name=_safe_attribute("name", item.get("name"), 100),
                    element_id=_safe_attribute("id", item.get("id"), 100),
                    data_attributes=_safe_data_attributes(item.get("data_attributes")),
                    disabled=bool(item.get("disabled", False)),
                    hierarchy=hierarchy,
                )
            )
    return FormDiagnosticBundle(forms=forms, buttons=tuple(buttons))


def format_diagnostic(page: Any, bundle: FormDiagnosticBundle | None = None) -> str:
    """Rend un rapport partageable à partir des métadonnées déjà filtrées."""
    bundle = bundle or FormDiagnosticBundle((), ())
    lines = [
        "PAGE",
        f"Titre : {sanitize_metadata_text(getattr(page, 'title', ''), 160) or '—'}",
        f"Section : {_clean_text(getattr(page, 'section', ''), 100) or '—'}",
        f"URL : {sanitize_current_url(str(getattr(page, 'url', '')), DEFAULT_SIDJILCOM_URL)}",
        "Confidentialité : aucune valeur de champ, cookie ou jeton collecté.",
        "",
        "FORMULAIRES",
    ]
    if not bundle.forms:
        lines.append("Aucun champ de formulaire visible.")
    for form_index, form in enumerate(bundle.forms, start=1):
        lines.extend(("", f"Formulaire {form_index} : {form.title}"))
        for field_index, field in enumerate(form.fields, start=1):
            lines.extend(
                (
                    "",
                    f"CHAMP {field_index}",
                    f"Label : {field.label}",
                    f"Texte associé : {field.associated_text}",
                    f"Type : {field.html_type}",
                    f"Name : {field.name or '—'}",
                    f"ID : {field.element_id or '—'}",
                    f"Role : {field.role}",
                    f"Placeholder : {field.placeholder or '—'}",
                    f"Disabled : {'oui' if field.disabled else 'non'}",
                    f"Hiérarchie : {' > '.join((form.title, *field.hierarchy))}",
                    f"Attributs data-* : {_format_attributes(field.data_attributes)}",
                    f"Nombre d'options : {field.option_count}",
                    "Options : " + (
                        ", ".join(field.options) if field.options else
                        ("textes masqués (potentiellement sensibles)" if field.options_redacted else "—")
                    ),
                )
            )
    lines.extend(("", "BOUTONS"))
    if not bundle.buttons:
        lines.append("Aucun bouton visible identifié.")
    for index, button in enumerate(bundle.buttons, start=1):
        lines.extend(
            (
                "",
                f"Bouton {index}",
                f"Texte : {button.text}",
                f"Type : {button.html_type}",
                f"Role : {button.role}",
                f"Name : {button.name or '—'}",
                f"ID : {button.element_id or '—'}",
                f"Disabled : {'oui' if button.disabled else 'non'}",
                f"Hiérarchie : {' > '.join(button.hierarchy) or 'Page'}",
                f"Attributs data-* : {_format_attributes(button.data_attributes)}",
            )
        )
    return "\n".join(lines)


def _format_attributes(attributes: tuple[tuple[str, str], ...]) -> str:
    return ", ".join(f"{key}={value}" for key, value in attributes) or "—"


class _FixtureHTMLParser(HTMLParser):
    """Parseur de fixtures de test qui ignore les attributs `value` dès la lecture."""

    _ALLOWED_ATTRIBUTES = frozenset(
        {
            "id", "name", "type", "role", "placeholder", "aria-label", "aria-labelledby", "for",
            "title", "disabled", "checked", "selected", "multiple", "required", "data-testid",
            "data-test", "data-qa", "data-cy", "data-field", "data-field-name", "data-role",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.raw: dict[str, list[dict[str, Any]]] = {"fields": [], "buttons": []}
        self.labels: dict[str, str] = {}
        self.form_title = "Formulaire"
        self.form_id = "hors-formulaire"
        self.form_index = 0
        self.fieldsets: list[dict[str, str]] = []
        self._active_label: dict[str, Any] | None = None
        self._active_legend: dict[str, Any] | None = None
        self._active_field: dict[str, Any] | None = None
        self._active_option: dict[str, Any] | None = None
        self._active_button: dict[str, Any] | None = None
        self._active_button_depth = 0

    @classmethod
    def _attributes(cls, attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        # Deliberately never copy/read the HTML `value` attribute.
        return {key: value or "" for key, value in attrs if key in cls._ALLOWED_ATTRIBUTES}

    def _button_record(self, safe: dict[str, str], default_type: str) -> dict[str, Any]:
        return {
            "text": safe.get("aria-label", "") or safe.get("title", ""),
            "html_type": safe.get("type", default_type),
            "role": safe.get("role", "button"),
            "name": safe.get("name", ""),
            "id": safe.get("id", ""),
            "data_attributes": {key: value for key, value in safe.items() if key.startswith("data-")},
            "disabled": "disabled" in safe,
            "form_title": self.form_title,
            "hierarchy": [item["legend"] for item in self.fieldsets if item["legend"]],
        }

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        safe = self._attributes(attrs)
        if tag == "form":
            self.form_index += 1
            self.form_id = safe.get("id") or f"formulaire-{self.form_index}"
            self.form_title = safe.get("aria-label") or safe.get("title") or self.form_id or "Formulaire"
        elif tag == "fieldset":
            self.fieldsets.append({"legend": ""})
        elif tag == "legend" and self.fieldsets:
            self._active_legend = self.fieldsets[-1]
        elif tag == "label":
            self._active_label = {"for": safe.get("for", ""), "parts": []}
        elif tag in {"input", "select", "textarea"}:
            html_type = safe.get("type", "text").lower() if tag == "input" else tag
            if tag == "input" and html_type in {"submit", "button", "reset"}:
                self.raw["buttons"].append(self._button_record(safe, html_type))
            elif html_type not in {"hidden", "password"} and not _SECRET_WORDS.search(safe.get("name", "")):
                element_id = safe.get("id", "")
                label = self.labels.get(element_id, "") or safe.get("aria-label", "") or safe.get("placeholder", "")
                role = safe.get("role") or _native_role(tag, html_type)
                field = {
                    "label": label,
                    "associated_text": label,
                    "html_type": html_type,
                    "role": role,
                    "name": safe.get("name", ""),
                    "id": element_id,
                    "placeholder": safe.get("placeholder", ""),
                    "data_attributes": {key: value for key, value in safe.items() if key.startswith("data-")},
                    "disabled": "disabled" in safe,
                    "option_count": 0,
                    "options": [],
                    "form_id": self.form_id,
                    "form_title": self.form_title,
                    "hierarchy": [item["legend"] for item in self.fieldsets if item["legend"]],
                }
                self.raw["fields"].append(field)
                if tag == "select":
                    self._active_field = field
        elif tag == "option" and self._active_field is not None:
            self._active_option = {"parts": []}
        elif tag == "button":
            self._active_button_depth += 1
            if self._active_button_depth == 1:
                self._active_button = self._button_record(safe, "submit")
                self._active_button["text"] = safe.get("aria-label", "") or safe.get("title", "")
                self._active_button["parts"] = []

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


def diagnostics_from_html_fixture(html: str) -> FormDiagnosticBundle:
    """Fabrique un instantané de métadonnées depuis une fixture; jamais d'attribut value."""
    parser = _FixtureHTMLParser()
    parser.feed(html)
    for button in parser.raw["buttons"]:
        parts = button.pop("parts", [])
        if parts and not button.get("text"):
            button["text"] = " ".join(parts)
    return build_form_diagnostics(parser.raw)
