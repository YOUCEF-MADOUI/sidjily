"""Pilote Playwright du formulaire normal de recherche Sidjilcom.

Les noms de champs viennent exclusivement de ``SIDJILCOM_CONTROL_MAP``. Les rapports
ne lisent jamais les lignes d'entreprise : seuls les en-têtes et les nombres de lignes
sont consultés après soumission.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

from sidjily.sidjilcom.autocomplete import (
    AutocompleteObservation,
    AutocompleteSelectionResult,
    AutocompleteTestError,
    AutocompleteTester,
)
from sidjily.sidjilcom.autocomplete_playwright import PlaywrightAutocompleteDriver
from sidjily.sidjilcom.criteria import (
    MappedCriterion,
    SearchMode,
    REPORTED_FORM_METHOD,
    SIDJILCOM_CONTROL_MAP,
    resolve_control_name,
)
from sidjily.sidjilcom.diagnostics import _css_selector, sanitize_diagnostic_url, sanitize_metadata_text
from sidjily.sidjilcom.search_form_diagnostics import (
    PreflightButton,
    PreflightForm,
    SearchPreflightSnapshot,
)
from sidjily.sidjilcom.search import (
    SearchControl,
    SearchExecutionError,
    SearchFormSnapshot,
    SearchFormUnsafe,
    SearchNavigationUnexpected,
    SearchObservation,
    SearchResultsObservationError,
    SearchSessionExpired,
)
from sidjily.sidjilcom.selectors import (
    DEFAULT_ENTERPRISE_SEARCH_ROUTE,
    identify_section,
    is_portal_host,
    sanitize_current_url,
    SEARCH_MODE_PORTLET_MARKER,
)


_RESULTS_DOM_SCRIPT = r"""() => {
    const visible = element => {
        if (!element || !element.isConnected) return false;
        let current = element;
        while (current && current.nodeType === Node.ELEMENT_NODE) {
            const style = window.getComputedStyle(current);
            if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0' ||
                current.hasAttribute('hidden') || current.getAttribute('aria-hidden') === 'true') return false;
            current = current.parentElement;
        }
        return !!element.getClientRects().length;
    };
    const outsideTable = element => !element.closest('table');
    const tables = Array.from(document.querySelectorAll('table')).filter(visible);
    const firstTable = tables[0] || null;
    const semanticZone = element => {
        let current = element ? element.parentElement : null;
        while (current) {
            const tag = String(current.tagName || '').toLowerCase();
            const role = String(current.getAttribute('role') || '').toLowerCase();
            if (tag === 'main' || tag === 'section' || role === 'main' || role === 'region') return current;
            current = current.parentElement;
        }
        return null;
    };
    const resultZone = semanticZone(firstTable);
    const headers = firstTable ? Array.from(firstTable.querySelectorAll('thead th, tr th'))
        .filter(visible).map(cell => (cell.innerText || cell.textContent || '').replace(/\s+/gu, ' ').trim()).filter(Boolean) : [];
    const rows = firstTable ? Array.from(firstTable.querySelectorAll('tbody tr')).filter(visible).length : 0;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node;
    const noResultsPattern = /(?:aucun(?:e)?\s+(?:résultat|resultat|entreprise|enregistrement)|pas\s+de\s+résultat|no\s+results?)/i;
    const expiredPattern = /(?:session.{0,40}(?:expir|ended)|(?:veuillez|please).{0,30}(?:reconnect|connecter)|انتهت الجلسة)/i;
    const errorPattern = /(?:erreur|error|failed|échec|echec|impossible|délai dépassé|timeout)/i;
    const countPattern = /\b\d[\d\s,.]*\s*(?:résultats?|resultats?|results?)\b/i;
    let noResults = false;
    let sessionExpired = false;
    let resultCount = null;
    const detectedErrors = new Set();
    while ((node = walker.nextNode())) {
        const parent = node.parentElement;
        if (!parent || !outsideTable(parent) || !visible(parent)) continue;
        const text = String(node.nodeValue || '').replace(/\s+/gu, ' ').trim();
        if (!text || text.length > 180) continue;
        if (noResultsPattern.test(text)) noResults = true;
        if (expiredPattern.test(text)) sessionExpired = true;
        if (errorPattern.test(text)) detectedErrors.add(text);
        if (resultCount === null && countPattern.test(text)) {
            const match = text.match(/\b([\d][\d\s,.]*)\s*(?:résultats?|resultats?|results?)\b/i);
            if (match) {
                const digits = match[1].replace(/[^\d]/g, '');
                if (digits.length <= 9) resultCount = Number(digits);
            }
        }
    }
    const alerts = Array.from(document.querySelectorAll('[role="alert"], [aria-live="assertive"]'))
        .filter(visible).filter(outsideTable).map(element => (element.innerText || element.textContent || '').replace(/\s+/gu, ' ').trim())
        .filter(Boolean).slice(0, 8);
    if (alerts.some(text => expiredPattern.test(text))) sessionExpired = true;
    const pagination = Array.from(document.querySelectorAll(
        '[aria-label*="page" i], [aria-label*="pagination" i], a[rel="next"], a[rel="prev"]'
    )).some(visible) || Array.from(document.querySelectorAll('[role="navigation"], nav')).some(element => {
        if (!visible(element)) return false;
        const label = String(element.getAttribute('aria-label') || '').toLowerCase();
        const text = String(element.innerText || '').replace(/\s+/gu, ' ').trim().toLowerCase();
        return /pagination|page|suivant|précédent|precedent|next|previous/.test(label) ||
            /(?:page\s+)?(?:suivante|précédente|precedente|next|previous)|\b\d+\s*(?:sur|of)\s*\d+\b/.test(text);
    });
    const passwordVisible = Array.from(document.querySelectorAll('input[type="password"]')).some(visible);
    const loginRoute = /\/(?:login|signin|sign-in|connexion|auth)(?:\/|$)/i.test(location.pathname);
    return {
        title: document.title || '',
        tableCount: tables.length,
        rowCount: rows,
        resultZoneFound: !!resultZone && visible(resultZone),
        resultZoneTag: resultZone && visible(resultZone) ? String(resultZone.tagName || '').toLowerCase() : '',
        headers: headers.slice(0, 40),
        paginationVisible: pagination,
        noResults,
        resultCount,
        errors: Array.from(new Set([...alerts, ...detectedErrors])).slice(0, 8),
        sessionExpired: sessionExpired || passwordVisible || loginRoute
    };
}"""


_SESSION_EXPIRY_CHECK_SCRIPT = r"""() => {
    if (/\/(?:login|signin|sign-in|connexion|auth)(?:\/|$)/i.test(location.pathname)) return true;
    if (Array.from(document.querySelectorAll('input[type="password"]')).some(element => {
        const style = window.getComputedStyle(element);
        return style.display !== 'none' && style.visibility !== 'hidden' && !element.disabled;
    })) return true;
    const pattern = /(?:session.{0,40}(?:expir|ended)|(?:veuillez|please).{0,30}(?:reconnect|connecter)|انتهت الجلسة)/i;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
        const parent = node.parentElement;
        if (!parent || parent.closest('table')) continue;
        const text = String(node.nodeValue || '').replace(/\s+/gu, ' ').trim();
        if (text.length <= 180 && pattern.test(text)) return true;
    }
    return false;
}"""


def session_expired_visible(page: Any) -> bool:
    """Retourne uniquement un booléen depuis le DOM; aucun texte de résultat n'est exposé."""
    return bool(page.evaluate(_SESSION_EXPIRY_CHECK_SCRIPT))


class PlaywrightSearchDriver:
    """Adaptateur utilisable uniquement sur la page Sidjilcom déjà ouverte et visible."""

    def __init__(
        self,
        page: Any,
        portal_url: str,
        select_mode: Callable[[SearchMode], None],
        autocomplete_tester: AutocompleteTester | None = None,
        *,
        timeout_ms: int = 8_000,
    ) -> None:
        self._page = page
        self._portal_url = portal_url
        self._select_mode_callback = select_mode
        self._autocomplete_tester = autocomplete_tester or AutocompleteTester(
            PlaywrightAutocompleteDriver(page, portal_url)
        )
        self._timeout_ms = max(1_000, min(timeout_ms, 15_000))
        self._active_form: Any | None = None
        self._active_button: Any | None = None
        self._active_mode: SearchMode | None = None
        self._active_marker = ""
        self._submission_attempted = False
        self._last_diagnostic: dict[str, object] = {}

    def select_mode(self, mode: SearchMode) -> None:
        if self._page is None or self._page.is_closed():
            raise SearchNavigationUnexpected()
        self._last_diagnostic = {
            "stage": "mode_selection",
            "page": self._page_diagnostic(),
            "forms": [],
            "button": {"label": "Rechercher", "visible": False, "enabled": False, "associated": False},
        }
        try:
            if self._page.evaluate(_SESSION_EXPIRY_CHECK_SCRIPT):
                raise SearchSessionExpired()
        except SearchExecutionError:
            raise
        except Exception:
            raise SearchNavigationUnexpected() from None
        self._require_search_page()
        self._select_mode_callback(mode)
        try:
            if self._page.evaluate(_SESSION_EXPIRY_CHECK_SCRIPT):
                raise SearchSessionExpired()
        except SearchExecutionError:
            raise
        except Exception:
            raise SearchNavigationUnexpected() from None
        self._active_form = None
        self._active_button = None
        self._active_mode = mode
        self._active_marker = self.page_marker()
        self._last_diagnostic["page"] = self._page_diagnostic()
        self._last_diagnostic["stage"] = "form_inspection"
        if not self._active_marker:
            raise SearchNavigationUnexpected()

    def page_marker(self) -> str:
        if self._page is None or self._page.is_closed():
            return ""
        try:
            title = self._page.title()
        except Exception:
            return ""
        source = f"{sanitize_current_url(self._page.url, self._portal_url)}\\0{title}"
        return hashlib.sha256(source.encode("utf-8", errors="replace")).hexdigest()

    def inspect_form(self, mode: SearchMode, criteria: tuple[MappedCriterion, ...]) -> SearchFormSnapshot:
        self._require_search_page()
        if self._active_mode is not mode or self.page_marker() != self._active_marker:
            raise SearchFormUnsafe()
        expected_suffixes = {
            SIDJILCOM_CONTROL_MAP[item.criterion].name_suffix
            for item in criteria
        }
        self._last_diagnostic = {
            "stage": "form_inspection",
            "page": self._page_diagnostic(),
            "forms": [],
            "button": {"label": "Rechercher", "visible": False, "enabled": False, "associated": False},
        }
        if None in expected_suffixes or not expected_suffixes:
            raise SearchFormUnsafe()
        matching_forms: list[tuple[Any, Any, tuple[SearchControl, ...], bool]] = []
        try:
            for frame in list(self._page.frames):
                forms = frame.locator("form")
                for form_index in range(min(forms.count(), 30)):
                    form = forms.nth(form_index)
                    if not form.is_visible():
                        continue
                    locators = form.locator("input[name], select[name], textarea[name]")
                    named: list[tuple[Any, str]] = []
                    for index in range(min(locators.count(), 200)):
                        locator = locators.nth(index)
                        try:
                            name = locator.get_attribute("name") or ""
                            if name and locator.is_visible():
                                named.append((locator, name))
                        except Exception:
                            continue
                    names = tuple(name for _locator, name in named)
                    form_id = sanitize_metadata_text(form.get_attribute("id") or "", 100)
                    form_name = sanitize_metadata_text(form.get_attribute("name") or "", 100)
                    form_class = sanitize_metadata_text(form.get_attribute("class") or "", 180)
                    form_role = sanitize_metadata_text(form.get_attribute("role") or "", 50)
                    method = (form.get_attribute("method") or "get").strip().casefold()
                    action = (form.get_attribute("action") or self._page.url).strip()
                    target = urljoin(self._page.url, action)
                    safe_action = sanitize_diagnostic_url(target, self._portal_url)
                    parent_portlet = self._parent_portlet(form)
                    expected_controls: list[dict[str, object]] = []
                    for item in criteria:
                        suffix = SIDJILCOM_CONTROL_MAP[item.criterion].name_suffix or ""
                        for locator, name in named:
                            if not name.casefold().endswith(suffix.casefold()):
                                continue
                            control_info = self._diagnostic_control(locator, name)
                            control_info["criterion"] = item.criterion
                            control_info["suffix"] = suffix
                            expected_controls.append(control_info)
                    form_diagnostic = {
                        "id": form_id, "name": form_name, "class": form_class, "role": form_role,
                        "method": method.upper(), "action": safe_action,
                        "parent_portlet": parent_portlet.get("id", ""),
                        "parent_portlet_confirmed": bool(parent_portlet.get("confirmed", False)),
                        "visible": bool(form.is_visible()),
                        "fields": expected_controls,
                    }
                    observed_forms = self._last_diagnostic.get("forms")
                    if isinstance(observed_forms, list) and len(observed_forms) < 20:
                        observed_forms.append(form_diagnostic)
                    if not all(any(name.casefold().endswith(suffix.casefold()) for name in names) for suffix in expected_suffixes):
                        continue
                    selected_controls: list[SearchControl] = []
                    for item in criteria:
                        name = resolve_control_name(mode, item.criterion, names)
                        matches = [locator for locator, observed_name in named if observed_name == name]
                        if len(matches) != 1:
                            raise SearchFormUnsafe()
                        locator = matches[0]
                        tag_name = str(locator.evaluate("element => element.tagName.toLowerCase()"))
                        input_type = (locator.get_attribute("type") or ("select-one" if tag_name == "select" else "text")).casefold()
                        classes = tuple((locator.get_attribute("class") or "").split())
                        aria = locator.get_attribute("aria-autocomplete")
                        options: tuple[str, ...] = ()
                        if tag_name == "select":
                            options = tuple(
                                str(value).strip()
                                for value in locator.locator("option").all_text_contents()
                                if str(value).strip()
                            )
                        selected_controls.append(SearchControl(
                            criterion=item.criterion,
                            name=name,
                            tag_name=tag_name,
                            input_type=input_type,
                            enabled=locator.is_enabled() and locator.get_attribute("readonly") is None,
                            visible=locator.is_visible(),
                            empty=self._is_empty(locator, tag_name, input_type),
                            class_names=classes,
                            aria_autocomplete=aria,
                            option_labels=options,
                            element_id=locator.get_attribute("id"),
                            label=self._control_label(locator),
                            role=locator.get_attribute("role"),
                            required=(locator.get_attribute("required") is not None or (locator.get_attribute("aria-required") or "").casefold() == "true"),
                            handle=locator,
                        ))
                    all_empty = True
                    all_controls = form.locator("input, select, textarea")
                    for control_index in range(min(all_controls.count(), 250)):
                        locator = all_controls.nth(control_index)
                        try:
                            tag_name = str(locator.evaluate("element => element.tagName.toLowerCase()"))
                            input_type = (locator.get_attribute("type") or ("select-one" if tag_name == "select" else "text")).casefold()
                            if self._is_visible_user_control(locator, tag_name, input_type) and not self._is_empty(locator, tag_name, input_type):
                                all_empty = False
                                break
                        except Exception:
                            all_empty = False
                            break
                    required_invalid = self._required_invalid_controls(form)
                    buttons = self._find_search_buttons(form)
                    actionable = [button for button in buttons if button.is_visible() and button.is_enabled()]
                    button_diag = self._diagnostic_button(buttons[0]) if len(buttons) == 1 else {
                        "label": "Rechercher", "visible": any(item.is_visible() for item in buttons),
                        "enabled": any(item.is_enabled() for item in buttons), "associated": bool(buttons),
                    }
                    button_diag["candidate_count"] = len(buttons)
                    form_diagnostic["button"] = button_diag
                    form_diagnostic["required_invalid_controls"] = list(required_invalid)
                    self._last_diagnostic["button"] = button_diag
                    if (
                        method != REPORTED_FORM_METHOD.casefold()
                        or not is_portal_host(target, self._portal_url)
                        or urlsplit(target).path.rstrip("/") != DEFAULT_ENTERPRISE_SEARCH_ROUTE.rstrip("/")
                        or not parent_portlet.get("confirmed", False)
                    ):
                        continue
                    if len(buttons) != 1 or len(actionable) != 1:
                        matching_forms.append((form, None, tuple(selected_controls), all_empty))
                    else:
                        matching_forms.append((form, actionable[0], tuple(selected_controls), all_empty))
            if len(matching_forms) != 1:
                raise SearchFormUnsafe()
            form, button, controls, all_empty = matching_forms[0]
            if button is None:
                raise SearchFormUnsafe()
            self._active_form, self._active_button = form, button
            method = (form.get_attribute("method") or "get").strip().upper()
            raw_action = (form.get_attribute("action") or self._page.url).strip()
            safe_action = sanitize_diagnostic_url(urljoin(self._page.url, raw_action), self._portal_url)
            parent_portlet = self._parent_portlet(form)
            button_meta = self._diagnostic_button(button)
            button_meta["candidate_count"] = len(self._find_search_buttons(form))
            required_invalid = self._required_invalid_controls(form)
            self._last_diagnostic.update({
                "stage": "form_inspection",
                "form": {
                    "id": sanitize_metadata_text(form.get_attribute("id") or "", 100),
                    "name": sanitize_metadata_text(form.get_attribute("name") or "", 100),
                    "class": sanitize_metadata_text(form.get_attribute("class") or "", 180),
                    "role": sanitize_metadata_text(form.get_attribute("role") or "", 50),
                    "method": method,
                    "action": safe_action,
                    "parent_portlet": parent_portlet.get("id", ""),
                    "parent_portlet_confirmed": bool(parent_portlet.get("confirmed", False)),
                },
                "fields": [self._diagnostic_control_from_search_control(control) for control in controls],
                "button": button_meta,
                "required_invalid_controls": list(required_invalid),
            })
            return SearchFormSnapshot(
                mode=mode,
                page_marker=self._active_marker,
                controls=controls,
                all_visible_controls_empty=all_empty,
                search_button_count=len([item for item in self._find_search_buttons(form) if item.is_visible() and item.is_enabled()]),
                search_button_enabled=button.is_enabled() and button.is_visible(),
                handle=form,
                form_id=sanitize_metadata_text(form.get_attribute("id") or "", 100),
                form_name=sanitize_metadata_text(form.get_attribute("name") or "", 100),
                form_class=sanitize_metadata_text(form.get_attribute("class") or "", 180),
                form_role=sanitize_metadata_text(form.get_attribute("role") or "", 50),
                form_method=method,
                form_action=safe_action,
                parent_portlet=str(parent_portlet.get("id", "")),
                parent_portlet_confirmed=bool(parent_portlet.get("confirmed", False)),
                button_id=str(button_meta.get("id", "")),
                button_name=str(button_meta.get("name", "")),
                button_role=str(button_meta.get("role", "")),
                button_type=str(button_meta.get("type", "")),
                button_class=str(button_meta.get("class", "")),
                required_invalid_controls=required_invalid,
                blockers=(),
                page_url=str(self._page_diagnostic().get("url", "")),
                page_title=str(self._page_diagnostic().get("title", "")),
                page_section=str(self._page_diagnostic().get("section", "")),
            )
        except SearchExecutionError:
            raise
        except Exception:
            raise SearchFormUnsafe() from None

    def diagnose_preflight(self) -> SearchPreflightSnapshot:
        """Observe les mêmes conditions structurelles que le précontrôle, sans interaction.

        Les champs ne sont interrogés que pour produire un booléen de vacuité; aucun texte
        ni attribut ``value`` n'est renvoyé, conservé ou journalisé.
        """
        expected_suffix = SIDJILCOM_CONTROL_MAP["commune_wilaya"].name_suffix or "_wilcom"
        route_matches = False
        visible_form_count = 0
        global_controls: list[str] = []
        forms_report: list[PreflightForm] = []
        incomplete = False
        if self._page is None or self._page.is_closed():
            return SearchPreflightSnapshot(
                False, "page indisponible", expected_suffix, 0, 0, (), (), True
            )
        try:
            route_matches = (
                is_portal_host(self._page.url, self._portal_url)
                and urlsplit(self._page.url).path.rstrip("/") == DEFAULT_ENTERPRISE_SEARCH_ROUTE.rstrip("/")
            )
        except Exception:
            incomplete = True

        for frame_index, frame in enumerate(list(self._page.frames)):
            frame_name = sanitize_metadata_text(frame.name or ("Document principal" if frame_index == 0 else f"Frame {frame_index}"), 80)
            try:
                all_named = frame.locator("input[name], select[name], textarea[name]")
                for control_index in range(min(all_named.count(), 400)):
                    control = all_named.nth(control_index)
                    try:
                        name = control.get_attribute("name") or ""
                        if not name.casefold().endswith(expected_suffix.casefold()) or not control.is_visible():
                            continue
                        tag_name = str(control.evaluate("element => element.tagName.toLowerCase()"))
                        input_type = (control.get_attribute("type") or tag_name).casefold()
                        control_id = control.get_attribute("id") or ""
                        hint = self._diagnostic_control_hint(name, control_id, tag_name, input_type)
                        global_controls.append(f"{frame_name} · {hint}")
                    except Exception:
                        incomplete = True
            except Exception:
                incomplete = True

            try:
                form_locator = frame.locator("form")
                form_total = min(form_locator.count(), 100)
            except Exception:
                incomplete = True
                continue
            for form_index in range(form_total):
                try:
                    form = form_locator.nth(form_index)
                    visible_form = bool(form.is_visible())
                    visible_form_count += int(visible_form)
                    raw_id = form.get_attribute("id") or ""
                    raw_name = form.get_attribute("name") or ""
                    safe_id = sanitize_metadata_text(raw_id, 90)
                    safe_name = sanitize_metadata_text(raw_name, 90)
                    identity = safe_id or safe_name
                    title = f"Formulaire {form_index + 1}"
                    class_name = sanitize_metadata_text(form.get_attribute("class") or "", 140)
                    role = sanitize_metadata_text(form.get_attribute("role") or "", 40)
                    css_selector = _css_selector("form", safe_id, safe_name, role)
                    method = (form.get_attribute("method") or "GET").strip().upper()
                    raw_action = (form.get_attribute("action") or self._page.url).strip()
                    target = urljoin(self._page.url, raw_action)
                    safe_action = sanitize_diagnostic_url(target, self._portal_url)
                    action_is_portal = is_portal_host(target, self._portal_url)

                    named = form.locator("input[name], select[name], textarea[name]")
                    named_count = min(named.count(), 200)
                    matching_controls: list[str] = []
                    for control_index in range(named_count):
                        control = named.nth(control_index)
                        try:
                            name = control.get_attribute("name") or ""
                            if not name or not control.is_visible():
                                continue
                            if not name.casefold().endswith(expected_suffix.casefold()):
                                continue
                            tag_name = str(control.evaluate("element => element.tagName.toLowerCase()"))
                            input_type = (control.get_attribute("type") or tag_name).casefold()
                            matching_controls.append(self._diagnostic_control_hint(
                                name, control.get_attribute("id") or "", tag_name, input_type
                            ))
                        except Exception:
                            incomplete = True

                    controls = form.locator("input, select, textarea")
                    visible_controls = 0
                    nonempty_hints: list[str] = []
                    for control_index in range(min(controls.count(), 250)):
                        control = controls.nth(control_index)
                        try:
                            tag_name = str(control.evaluate("element => element.tagName.toLowerCase()"))
                            input_type = (control.get_attribute("type") or ("select-one" if tag_name == "select" else "text")).casefold()
                            if not self._is_visible_user_control(control, tag_name, input_type):
                                continue
                            visible_controls += 1
                            if not self._is_empty(control, tag_name, input_type):
                                name = control.get_attribute("name") or ""
                                control_id = control.get_attribute("id") or ""
                                nonempty_hints.append(self._diagnostic_control_hint(name, control_id, tag_name, input_type))
                        except Exception:
                            incomplete = True
                            # En cas de lecture incertaine, signaler seulement un booléen non vide.
                            nonempty_hints.append("état de contrôle non déterminé")

                    search_buttons: list[PreflightButton] = []
                    reset_buttons: list[PreflightButton] = []
                    button_locator = form.locator("button, input[type='submit'], input[type='button'], input[type='reset']")
                    for button_index in range(min(button_locator.count(), 50)):
                        button = button_locator.nth(button_index)
                        try:
                            tag_name = str(button.evaluate("element => element.tagName.toLowerCase()"))
                            label = (
                                button.get_attribute("value") or ""
                                if tag_name == "input" else button.inner_text(timeout=1_000)
                            )
                            normalized = re.sub(r"\s+", " ", str(label)).strip().casefold()
                            visible = bool(button.is_visible())
                            enabled = bool(button.is_enabled())
                            button_id = sanitize_metadata_text(button.get_attribute("id") or "", 90)
                            button_name = sanitize_metadata_text(button.get_attribute("name") or "", 90)
                            button_role = sanitize_metadata_text(button.get_attribute("role") or "", 40)
                            selector = f"{css_selector} {_css_selector(tag_name, button_id, button_name, button_role)}"
                            observed = PreflightButton(
                                text=sanitize_metadata_text(label, 60),
                                visible=visible,
                                enabled=enabled,
                                associated=True,
                                selector=selector,
                            )
                            if normalized == "rechercher":
                                search_buttons.append(observed)
                            if normalized.startswith("réinitialiser") or normalized.startswith("reinitialiser"):
                                reset_buttons.append(observed)
                        except Exception:
                            incomplete = True

                    forms_report.append(PreflightForm(
                        frame_name=frame_name,
                        form_id=safe_id or identity,
                        form_name=safe_name,
                        title=title,
                        action=safe_action,
                        method=sanitize_metadata_text(method, 16) or "inconnue",
                        class_name=class_name,
                        role=role,
                        visible=visible_form,
                        css_selector=css_selector,
                        expected_control_count=len(matching_controls),
                        expected_controls=tuple(matching_controls),
                        visible_control_count=visible_controls,
                        nonempty_control_count=len(nonempty_hints),
                        nonempty_controls=tuple(nonempty_hints),
                        search_buttons=tuple(search_buttons),
                        reset_buttons=tuple(reset_buttons),
                        action_is_portal=action_is_portal,
                    ))
                except Exception:
                    incomplete = True

        return SearchPreflightSnapshot(
            route_matches=route_matches,
            page_section=identify_section(self._page.url),
            expected_suffix=expected_suffix,
            visible_form_count=visible_form_count,
            global_expected_control_count=len(global_controls),
            global_expected_controls=tuple(global_controls[:30]),
            forms=tuple(forms_report),
            inspection_incomplete=incomplete,
        )

    @staticmethod
    def _diagnostic_control_hint(name: str, element_id: str, tag_name: str, input_type: str) -> str:
        """Identifiant structurel autorisé; jamais le contenu d'un contrôle."""
        if input_type == "password" or re.search(r"password|passwd|token|secret|csrf|cookie|session|credential|authorization", name + " " + element_id, re.I):
            return f"{tag_name}/{input_type} · identifiant masqué"
        safe_name = sanitize_metadata_text(name, 90)
        safe_id = sanitize_metadata_text(element_id, 90)
        return " · ".join(part for part in (
            f"{tag_name}/{input_type}",
            f"name={safe_name}" if safe_name else "",
            f"id={safe_id}" if safe_id else "",
        ) if part)

    @staticmethod
    def _is_visible_user_control(locator: Any, tag_name: str, input_type: str) -> bool:
        if not locator.is_visible():
            return False
        if tag_name == "input" and input_type in {"hidden", "submit", "button", "reset", "image"}:
            return False
        return tag_name in {"input", "select", "textarea"}

    @staticmethod
    def _is_empty(locator: Any, tag_name: str, input_type: str) -> bool:
        if tag_name == "select":
            try:
                return locator.evaluate(
                    "element => element.selectedIndex < 0 || element.selectedOptions.length === 0 || " +
                    "Array.from(element.selectedOptions).every(option => !String(option.value || '').trim())"
                )
            except Exception:
                return False
        if tag_name == "input" and input_type in {"checkbox", "radio"}:
            return not bool(locator.is_checked())
        if tag_name == "input" and input_type in {"password", "file"}:
            return False
        try:
            return not bool(locator.input_value(timeout=1_000).strip())
        except Exception:
            return False

    @staticmethod
    def _find_search_buttons(form: Any) -> list[Any]:
        """Retourne uniquement les boutons nommés Rechercher, même désactivés, pour le diagnostic."""
        matches: list[Any] = []
        buttons = form.locator("button, input[type='submit'], input[type='button']")
        for index in range(min(buttons.count(), 50)):
            button = buttons.nth(index)
            try:
                if button.evaluate("element => element.tagName.toLowerCase()") == "input":
                    label = button.get_attribute("value") or ""
                else:
                    label = button.inner_text(timeout=1_000)
                normalized = re.sub(r"\s+", " ", label).strip().casefold()
                if normalized == "rechercher":
                    matches.append(button)
            except Exception:
                continue
        return matches

    def fill_text(self, control: SearchControl, value: str) -> None:
        locator = self._require_control(control, "input", "text")
        try:
            locator.fill(value, timeout=self._timeout_ms)
            if locator.input_value(timeout=1_000) != value:
                raise SearchFormUnsafe()
        except SearchExecutionError:
            raise
        except Exception:
            raise SearchFormUnsafe() from None

    def read_control_value(self, control: SearchControl) -> str:
        locator = self._require_control(control, control.tag_name, control.input_type)
        try:
            return locator.input_value(timeout=1_000)
        except Exception:
            raise SearchFormUnsafe("La valeur du contrôle n'a pas pu être vérifiée dans le DOM.", code="dom_value_unreadable") from None

    def failure_diagnostic(self) -> dict[str, object]:
        return dict(self._last_diagnostic)

    def _page_diagnostic(self) -> dict[str, object]:
        try:
            return {
                "url": sanitize_current_url(self._page.url, self._portal_url),
                "title": sanitize_metadata_text(self._page.title(), 160),
                "section": identify_section(self._page.url),
            }
        except Exception:
            return {"url": "", "title": "", "section": "indisponible"}

    @staticmethod
    def _control_label(locator: Any) -> str:
        try:
            label = locator.evaluate("""element => {
                const labels = Array.from(element.labels || []).map(item => item.innerText || item.textContent || '').join(' ');
                const wrapping = element.closest('label');
                return labels || (wrapping ? (wrapping.innerText || wrapping.textContent || '') : '') ||
                    element.getAttribute('aria-label') || element.getAttribute('title') || '';
            }""")
            return sanitize_metadata_text(label, 120)
        except Exception:
            return ""

    @staticmethod
    def _parent_portlet(form: Any) -> dict[str, object]:
        try:
            raw = form.evaluate("""(element, marker) => {
                let current = element.parentElement;
                while (current) {
                    if (String(current.id || '').includes(marker)) {
                        return {id: current.id || '', className: current.className || '', role: current.getAttribute('role') || '', confirmed: true};
                    }
                    current = current.parentElement;
                }
                return {id: '', className: '', role: '', confirmed: false};
            }""", SEARCH_MODE_PORTLET_MARKER)
            return {
                "id": sanitize_metadata_text(raw.get("id", ""), 140),
                "class": sanitize_metadata_text(raw.get("className", ""), 180),
                "role": sanitize_metadata_text(raw.get("role", ""), 50),
                "confirmed": bool(raw.get("confirmed", False)),
            }
        except Exception:
            return {"id": "", "class": "", "role": "", "confirmed": False}

    @classmethod
    def _diagnostic_control(cls, locator: Any, name: str) -> dict[str, object]:
        try:
            tag = str(locator.evaluate("element => element.tagName.toLowerCase()"))
            input_type = (locator.get_attribute("type") or ("select-one" if tag == "select" else "text")).casefold()
            return {
                "name": cls._safe_dom_name(name),
                "id": cls._safe_dom_name(locator.get_attribute("id") or ""),
                "label": cls._control_label(locator),
                "role": sanitize_metadata_text(locator.get_attribute("role") or "", 50),
                "tag": tag,
                "type": input_type,
                "class": sanitize_metadata_text(locator.get_attribute("class") or "", 180),
                "visible": bool(locator.is_visible()),
                "disabled": not bool(locator.is_enabled()) or locator.get_attribute("readonly") is not None,
                "required": locator.get_attribute("required") is not None or (locator.get_attribute("aria-required") or "").casefold() == "true",
                "has_value": not cls._is_empty(locator, tag, input_type),
            }
        except Exception:
            return {"name": cls._safe_dom_name(name), "found": False}

    @staticmethod
    def _safe_dom_name(value: object) -> str:
        text = sanitize_metadata_text(value, 120)
        if re.search(r"password|passwd|token|secret|csrf|cookie|session|credential|authorization", text, re.I):
            return "[identifiant masqué]"
        return text

    @classmethod
    def _diagnostic_control_from_search_control(cls, control: SearchControl) -> dict[str, object]:
        return {
            "criterion": control.criterion,
            "name": cls._safe_dom_name(control.name),
            "id": cls._safe_dom_name(control.element_id or ""),
            "label": sanitize_metadata_text(control.label, 120),
            "role": sanitize_metadata_text(control.role or "", 50),
            "tag": control.tag_name,
            "type": control.input_type,
            "class": sanitize_metadata_text(" ".join(control.class_names), 180),
            "visible": control.visible,
            "disabled": not control.enabled,
            "required": control.required,
            "has_value": not control.empty,
        }

    @classmethod
    def _diagnostic_button(cls, button: Any) -> dict[str, object]:
        try:
            is_input = button.evaluate("element => element.tagName.toLowerCase() == 'input'")
            label = (button.get_attribute("value") or "") if is_input else (button.inner_text(timeout=1_000) or "")
            return {
                "label": sanitize_metadata_text(label, 80),
                "id": cls._safe_dom_name(button.get_attribute("id") or ""),
                "name": cls._safe_dom_name(button.get_attribute("name") or ""),
                "role": sanitize_metadata_text(button.get_attribute("role") or "", 50),
                "type": sanitize_metadata_text(button.get_attribute("type") or ("button" if not is_input else ""), 40),
                "class": sanitize_metadata_text(button.get_attribute("class") or "", 180),
                "visible": bool(button.is_visible()),
                "enabled": bool(button.is_enabled()),
                "associated": True,
            }
        except Exception:
            return {"label": "Rechercher", "visible": False, "enabled": False, "associated": True}

    @staticmethod
    def _required_invalid_controls(form: Any) -> tuple[str, ...]:
        try:
            values = form.evaluate("""form => Array.from(form.elements || []).filter(element => {
                if (!element || element.disabled) return false;
                const required = element.required || String(element.getAttribute('aria-required') || '').toLowerCase() === 'true';
                if (!required) return false;
                const style = window.getComputedStyle(element);
                const visible = style.display !== 'none' && style.visibility !== 'hidden' && !!element.getClientRects().length;
                return visible && !(element.validity ? element.validity.valid : String(element.value || '').trim().length > 0);
            }).slice(0, 30).map(element => {
                const name = element.getAttribute('name') || '';
                const labels = Array.from(element.labels || []).map(label => label.innerText || label.textContent || '').join(' ');
                return {name, label: labels || element.getAttribute('aria-label') || element.getAttribute('title') || ''};
            })""")
            output = []
            for item in values if isinstance(values, list) else []:
                label = sanitize_metadata_text(item.get("label", ""), 100)
                name = PlaywrightSearchDriver._safe_dom_name(item.get("name", ""))
                output.append(label or name or "champ obligatoire sans libellé")
            return tuple(output)
        except Exception:
            return ("état obligatoire impossible à vérifier",)

    def fill_date(self, control: SearchControl, value: str) -> None:
        locator = self._require_control(control, "input", "date")
        try:
            locator.fill(value, timeout=self._timeout_ms)
            if locator.input_value(timeout=1_000) != value:
                raise SearchFormUnsafe()
        except SearchExecutionError:
            raise
        except Exception:
            raise SearchFormUnsafe() from None

    def fill_select(self, control: SearchControl, value: str) -> None:
        locator = self._require_control(control, "select", "select-one")
        try:
            labels = tuple(
                str(option).strip()
                for option in locator.locator("option").all_text_contents()
                if str(option).strip()
            )
            if labels.count(value) != 1:
                raise SearchFormUnsafe()
            locator.select_option(label=value, timeout=self._timeout_ms)
            selected = locator.locator("option:checked").all_text_contents()
            if len(selected) != 1 or selected[0].strip() != value:
                raise SearchFormUnsafe()
        except SearchExecutionError:
            raise
        except Exception:
            raise SearchFormUnsafe() from None

    def prepare_autocomplete(self, field_id: str, value: str) -> AutocompleteObservation:
        try:
            return self._autocomplete_tester.prepare(field_id, value)
        except AutocompleteTestError:
            raise SearchFormUnsafe() from None

    def select_autocomplete(self, token: str, index: int) -> AutocompleteSelectionResult:
        try:
            return self._autocomplete_tester.select(token, index)
        except AutocompleteTestError:
            raise SearchFormUnsafe() from None

    def reset_autocomplete(self, token: str) -> None:
        try:
            self._autocomplete_tester.reset(token)
        except AutocompleteTestError:
            raise SearchFormUnsafe() from None

    def submit_search(
        self, form: SearchFormSnapshot, *, on_attempt: Callable[[], None] | None = None
    ) -> None:
        if self._submission_attempted:
            raise SearchFormUnsafe("Une soumission a déjà été tentée; aucun second clic n'est autorisé.", code="duplicate_submission")
        if self._page is None or self._page.is_closed():
            raise SearchNavigationUnexpected()
        try:
            if self._page.evaluate(_SESSION_EXPIRY_CHECK_SCRIPT):
                raise SearchSessionExpired()
        except SearchExecutionError:
            raise
        except Exception:
            raise SearchNavigationUnexpected() from None
        self._require_search_page()
        if (
            form.mode is not self._active_mode
            or form.handle is not self._active_form
            or self.page_marker() != form.page_marker
            or self._active_button is None
        ):
            raise SearchFormUnsafe()
        try:
            if self._page.evaluate(_SESSION_EXPIRY_CHECK_SCRIPT):
                raise SearchSessionExpired()
            if not self._active_button.is_visible() or not self._active_button.is_enabled():
                raise SearchFormUnsafe()
            button_candidates = self._find_search_buttons(self._active_form)
            current_buttons = [button for button in button_candidates if button.is_visible() and button.is_enabled()]
            if len(button_candidates) != 1 or len(current_buttons) != 1:
                self._last_diagnostic["button"] = {
                    "label": "Rechercher", "candidate_count": len(button_candidates),
                    "visible": any(button.is_visible() for button in button_candidates),
                    "enabled": any(button.is_enabled() for button in button_candidates),
                    "associated": bool(button_candidates),
                }
                raise SearchFormUnsafe("Le formulaire ne possède plus un bouton Rechercher unique et actif.", code="search_button_changed")
            current_method = (self._active_form.get_attribute("method") or "GET").strip().upper()
            current_action = urljoin(self._page.url, self._active_form.get_attribute("action") or self._page.url)
            current_safe_action = sanitize_diagnostic_url(current_action, self._portal_url)
            current_parent = self._parent_portlet(self._active_form)
            previous_form_diagnostic = self._last_diagnostic.get("form", {})
            form_diagnostic = dict(previous_form_diagnostic) if isinstance(previous_form_diagnostic, dict) else {}
            form_diagnostic.update({
                "method": current_method.casefold(), "action": current_safe_action,
                "parent_portlet": current_parent.get("id", ""),
                "parent_portlet_confirmed": bool(current_parent.get("confirmed", False)),
            })
            self._last_diagnostic["form"] = form_diagnostic
            if (
                current_method != "POST"
                or not is_portal_host(current_action, self._portal_url)
                or current_safe_action != form.form_action
            ):
                raise SearchFormUnsafe("Le formulaire a changé de méthode ou de destination.", code="form_action_changed")
            if not bool(current_parent.get("confirmed", False)):
                raise SearchFormUnsafe("Le formulaire n'est plus dans le portlet Recherche Commerçant.", code="form_parent_changed")
            current_button = self._diagnostic_button(current_buttons[0])
            current_button["candidate_count"] = len(button_candidates)
            self._last_diagnostic["button"] = current_button
            expected_button = {
                "id": form.button_id, "name": form.button_name, "role": form.button_role,
                "type": form.button_type, "class": form.button_class,
            }
            if str(current_button.get("label", "")).strip().casefold() != "rechercher":
                raise SearchFormUnsafe("Le bouton de soumission n'est plus exactement Rechercher.", code="search_button_changed")
            for key in ("id", "name", "role", "type", "class"):
                previous = str(expected_button.get(key, "") or "")
                current = str(current_button.get(key, "") or "")
                if previous and current != previous:
                    raise SearchFormUnsafe("Le bouton Rechercher a changé depuis sa vérification.", code="search_button_changed")
            required_invalid = self._required_invalid_controls(self._active_form)
            self._last_diagnostic["required_invalid_controls"] = list(required_invalid)
            if required_invalid:
                raise SearchFormUnsafe("Un champ obligatoire est invalide avant le clic.", code="required_field_invalid")
            live_fields: list[dict[str, object]] = []
            for control in form.controls:
                name_locator = self._active_form.locator("input[name], select[name], textarea[name]")
                name_matches = [
                    name_locator.nth(index)
                    for index in range(min(name_locator.count(), 250))
                    if name_locator.nth(index).get_attribute("name") == control.name
                ]
                if len(name_matches) == 1:
                    field_info = self._diagnostic_control(name_matches[0], control.name)
                    field_info["criterion"] = control.criterion
                    live_fields.append(field_info)
            if live_fields:
                self._last_diagnostic["fields"] = live_fields
            for name, expected in form.verified_values:
                matching = self._active_form.locator("input[name], select[name], textarea[name]")
                values = []
                for index in range(min(matching.count(), 250)):
                    field = matching.nth(index)
                    if field.get_attribute("name") == name:
                        try:
                            values.append(field.input_value(timeout=1_000))
                        except Exception:
                            values.append("")
                matches = len(values) == 1 and values[0].strip() == expected.strip()
                for field_info in self._last_diagnostic.get("fields", []):
                    if isinstance(field_info, dict) and field_info.get("name") == name:
                        field_info["has_value"] = bool(values and values[0].strip())
                        field_info["value_matches_criterion"] = matches
                if not matches:
                    raise SearchFormUnsafe("Une valeur de critère a changé juste avant le clic.", code="last_moment_value_mismatch")
            if on_attempt is not None:
                on_attempt()
            # Verrou local posé avant l'action : un timeout ambigu ne peut pas déclencher un second clic.
            self._submission_attempted = True
            # Un clic unique dans l'interface du portail; aucune requête HTTP/API n'est reproduite.
            current_buttons[0].click(timeout=self._timeout_ms)
            try:
                self._page.wait_for_load_state("domcontentloaded", timeout=5_000)
            except Exception:
                pass
            self._page.wait_for_timeout(650)
        except SearchExecutionError:
            raise
        except Exception:
            raise SearchFormUnsafe() from None

    def observe_results(self) -> SearchObservation:
        try:
            if self._page is None or self._page.is_closed() or not is_portal_host(self._page.url, self._portal_url):
                raise SearchNavigationUnexpected()
            sanitized_url = sanitize_current_url(self._page.url, self._portal_url)
            raw = self._page.evaluate(_RESULTS_DOM_SCRIPT)
            if not isinstance(raw, dict):
                raise SearchResultsObservationError()
            if not bool(raw.get("sessionExpired", False)) and identify_section(self._page.url) != "Trouver une entreprise":
                raise SearchNavigationUnexpected()
            title = sanitize_metadata_text(raw.get("title", ""), 160)
            columns = tuple(
                label for value in raw.get("headers", [])[:40]
                if (label := sanitize_metadata_text(value, 120))
            )
            errors = tuple(
                label for value in raw.get("errors", [])[:8]
                if (label := sanitize_metadata_text(value, 200))
            )
            result_count = raw.get("resultCount")
            if not isinstance(result_count, int) or result_count < 0:
                result_count = None
            return SearchObservation(
                title=title,
                sanitized_url=sanitized_url,
                result_count=result_count,
                table_count=max(0, int(raw.get("tableCount", 0))),
                row_count=max(0, int(raw.get("rowCount", 0))),
                columns=columns,
                pagination_visible=bool(raw.get("paginationVisible", False)),
                no_results=bool(raw.get("noResults", False)),
                errors=errors,
                session_expired=bool(raw.get("sessionExpired", False)),
                result_zone_found=bool(raw.get("resultZoneFound", False)),
                result_zone_tag=sanitize_metadata_text(raw.get("resultZoneTag", ""), 24),
            )
        except SearchExecutionError:
            raise
        except Exception:
            raise SearchResultsObservationError() from None

    def _require_control(self, control: SearchControl, tag_name: str, input_type: str) -> Any:
        if (
            self.page_marker() != self._active_marker
            or control.handle is None
            or control.tag_name != tag_name
            or control.input_type != input_type
        ):
            raise SearchFormUnsafe()
        try:
            if not control.handle.is_visible() or not control.handle.is_enabled() or not self._is_empty(control.handle, tag_name, input_type):
                raise SearchFormUnsafe()
            return control.handle
        except SearchExecutionError:
            raise
        except Exception:
            raise SearchFormUnsafe() from None

    def _require_search_page(self) -> None:
        if self._page is None or self._page.is_closed():
            raise SearchNavigationUnexpected()
        parsed = urlsplit(self._page.url)
        if (
            not is_portal_host(self._page.url, self._portal_url)
            or parsed.path.rstrip("/") != DEFAULT_ENTERPRISE_SEARCH_ROUTE.rstrip("/")
        ):
            raise SearchNavigationUnexpected()
