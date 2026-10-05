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
from sidjily.sidjilcom.diagnostics import sanitize_metadata_text
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

    def select_mode(self, mode: SearchMode) -> None:
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
                    if not all(any(name.casefold().endswith(suffix.casefold()) for name in names) for suffix in expected_suffixes):
                        continue
                    method = (form.get_attribute("method") or "get").strip().casefold()
                    action = (form.get_attribute("action") or self._page.url).strip()
                    target = urljoin(self._page.url, action)
                    if method != REPORTED_FORM_METHOD.casefold() or not is_portal_host(target, self._portal_url):
                        raise SearchFormUnsafe()
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
                    buttons = self._find_search_buttons(form)
                    if len(buttons) != 1:
                        matching_forms.append((form, None, tuple(selected_controls), all_empty))
                    else:
                        matching_forms.append((form, buttons[0], tuple(selected_controls), all_empty))
            if len(matching_forms) != 1:
                raise SearchFormUnsafe()
            form, button, controls, all_empty = matching_forms[0]
            if button is None:
                raise SearchFormUnsafe()
            self._active_form, self._active_button = form, button
            return SearchFormSnapshot(
                mode=mode,
                page_marker=self._active_marker,
                controls=controls,
                all_visible_controls_empty=all_empty,
                search_button_count=1,
                search_button_enabled=button.is_enabled() and button.is_visible(),
                handle=form,
            )
        except SearchExecutionError:
            raise
        except Exception:
            raise SearchFormUnsafe() from None

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
        matches: list[Any] = []
        buttons = form.locator("button, input[type='submit'], input[type='button']")
        for index in range(min(buttons.count(), 50)):
            button = buttons.nth(index)
            try:
                if not button.is_visible() or not button.is_enabled():
                    continue
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

    def submit_search(self, form: SearchFormSnapshot) -> None:
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
            current_buttons = self._find_search_buttons(self._active_form)
            if len(current_buttons) != 1:
                raise SearchFormUnsafe()
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
