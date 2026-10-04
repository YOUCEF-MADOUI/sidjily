"""Pilote Playwright découvrant les suggestions à partir du DOM réellement rendu.

Aucun sélecteur d'option spécifique à YUI n'est supposé. Les relations ARIA, les rôles,
les classes et les éléments visibles sont observés à chaque étape; seuls les contrôles de
liste effectivement reliés au champ sont proposés à la sélection.
"""

from __future__ import annotations

from typing import Any

from sidjily.sidjilcom.autocomplete import (
    AUTOCOMPLETE_FIELD_LABELS,
    AutocompleteContainerInfo,
    AutocompleteControlAmbiguous,
    AutocompleteControlDisabled,
    AutocompleteControlInfo,
    AutocompleteControlNotFound,
    AutocompleteDriver,
    AutocompletePageChanged,
    AutocompleteSnapshot,
    AutocompleteSuggestion,
    ResolvedAutocompleteControl,
)
from sidjily.sidjilcom.criteria import SIDJILCOM_CONTROL_MAP
from sidjily.sidjilcom.selectors import sanitize_current_url


AUTOCOMPLETE_DOM_SNAPSHOT_SCRIPT = r"""input => {
    const normalize = value => String(value || '').replace(/\s+/gu, ' ').trim();
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
    const selectorFor = element => {
        const parts = [];
        let current = element;
        while (current && current.nodeType === Node.ELEMENT_NODE && parts.length < 48) {
            const tag = current.tagName.toLowerCase();
            let position = 1;
            for (let sibling = current.previousElementSibling; sibling; sibling = sibling.previousElementSibling) {
                if (sibling.tagName === current.tagName) position++;
            }
            parts.unshift(`${tag}:nth-of-type(${position})`);
            if (tag === 'html') break;
            current = current.parentElement;
        }
        return parts.join(' > ');
    };
    const classNames = element => Array.from(element.classList || []).slice(0, 12);
    const attrs = element => ({
        tagName: element.tagName.toLowerCase(),
        elementId: element.id || null,
        classNames: classNames(element),
        role: element.getAttribute('role') || null,
        ariaExpanded: element.getAttribute('aria-expanded'),
        visible: visible(element),
        selector: selectorFor(element)
    });
    const inputAttrs = {
        tagName: input.tagName.toLowerCase(),
        elementId: input.id || null,
        classNames: classNames(input),
        role: input.getAttribute('role') || null,
        ariaAutocomplete: input.getAttribute('aria-autocomplete'),
        ariaHaspopup: input.getAttribute('aria-haspopup'),
        ariaControls: input.getAttribute('aria-controls'),
        ariaOwns: input.getAttribute('aria-owns'),
        ariaActiveDescendant: input.getAttribute('aria-activedescendant'),
        ariaExpanded: input.getAttribute('aria-expanded'),
        autocompleteAttribute: input.getAttribute('autocomplete'),
        enabled: !input.disabled && !input.readOnly
    };
    const referencedIds = new Set();
    for (const attribute of ['aria-controls', 'aria-owns', 'aria-activedescendant']) {
        for (const id of String(input.getAttribute(attribute) || '').split(/\s+/).filter(Boolean)) {
            referencedIds.add(id);
        }
    }
    const explicitRoots = Array.from(referencedIds)
        .map(id => document.getElementById(id))
        .filter(Boolean);
    const looksLikeSuggestionRoot = element => {
        if (element === input) return false;
        const marker = `${element.id || ''} ${classNames(element).join(' ')}`;
        const roleListbox = element.getAttribute('role') === 'listbox';
        const optionLikeName = /(aclist|autocomplete|suggest|typeahead).*(input|item|option)|(?:input|item|option).*(aclist|autocomplete|suggest|typeahead)/i.test(marker);
        if (optionLikeName) return roleListbox;
        return /aclist|autocomplete|suggest|typeahead|listbox/i.test(marker) || roleListbox;
    };
    const roots = new Set(explicitRoots);
    for (let current = input.parentElement, depth = 0; current && depth < 7; current = current.parentElement, depth++) {
        if (looksLikeSuggestionRoot(current)) roots.add(current);
    }
    for (const element of document.querySelectorAll(
        '[role="listbox"], [id*="aclist" i], [class*="aclist" i], ' +
        '[id*="autocomplete" i], [class*="autocomplete" i], ' +
        '[id*="suggest" i], [class*="suggest" i], [id*="typeahead" i], [class*="typeahead" i]'
    )) {
        if (!visible(element) || !looksLikeSuggestionRoot(element)) continue;
        const referenced = element.id && referencedIds.has(element.id);
        const inputLabelsRoot = input.id && String(element.getAttribute('aria-labelledby') || '').split(/\s+/).includes(input.id);
        const containsInput = element.contains(input);
        const activeOverlay = document.activeElement === input && element.querySelector(
            '[role="option"], [aria-selected], li, option, [class*="item" i]'
        );
        if (referenced || inputLabelsRoot || containsInput || activeOverlay) roots.add(element);
    }
    const rootArray = Array.from(roots).filter(visible);
    const containers = rootArray.map(root => ({
        ...attrs(root),
        relation: referencedIds.has(root.id) ? 'référencé par aria-controls/owns/activedescendant' :
            (root.contains(input) ? 'ancêtre/conteneur du champ' : 'conteneur visible associé au champ')
    }));
    const optionNodes = new Set();
    for (const root of rootArray) {
        if (root.matches('[role="option"], option, li, [aria-selected]')) optionNodes.add(root);
        for (const option of root.querySelectorAll('[role="option"], option, li, [aria-selected], [class*="aclist-item" i]')) {
            optionNodes.add(option);
        }
    }
    const ordered = Array.from(optionNodes).filter(option => {
        if (!visible(option)) return false;
        const text = normalize(option.innerText || '');
        if (!text) return false;
        const duplicateChild = Array.from(option.querySelectorAll('[role="option"], li, [aria-selected]'))
            .some(child => visible(child) && normalize(child.innerText || '') === text);
        return !duplicateChild;
    });
    const suggestions = ordered.slice(0, 80).map((option, index) => {
        const forbiddenSelector = 'a[href], [role="link"], button, [role="button"], ' +
            'input[type="submit"], input[type="reset"]';
        const forbidden = option.matches(forbiddenSelector) || !!option.closest(forbiddenSelector) ||
            !!option.querySelector(forbiddenSelector);
        return {
            index,
            text: normalize(option.innerText || '').slice(0, 240),
            tagName: option.tagName.toLowerCase(),
            classNames: classNames(option),
            role: option.getAttribute('role') || null,
            ariaSelected: option.getAttribute('aria-selected'),
            dataAttributeNames: Array.from(option.attributes)
                .map(attribute => attribute.name)
                .filter(name => name.toLowerCase().startsWith('data-'))
                .slice(0, 12),
            safeToSelect: !forbidden,
            selector: selectorFor(option)
        };
    });
    let context = input;
    for (let depth = 0; context.parentElement && depth < 5; depth++) {
        context = context.parentElement;
        if (context.querySelectorAll('input, select, textarea, [role="combobox"]').length >= 2) break;
    }
    const nearbyControls = Array.from(context.querySelectorAll('input, select, textarea, [role="combobox"]'))
        .slice(0, 16).map(control => {
            const label = control.labels && control.labels.length
                ? normalize(Array.from(control.labels).map(item => item.innerText || '').join(' ')).slice(0, 100)
                : '';
            return [
                control.tagName.toLowerCase(),
                control.getAttribute('type') || '',
                control.getAttribute('role') || '',
                control.getAttribute('aria-label') || '',
                control.getAttribute('aria-autocomplete') || '',
                control.getAttribute('aria-disabled') || '',
                control.disabled ? 'disabled' : 'enabled',
                control.tagName.toLowerCase() === 'select' ? `options=${control.options.length}` : '',
                label
            ].join('|');
        });
    return {
        input: inputAttrs,
        containers,
        suggestions,
        nearbyControls,
        yuiGlobalAvailable: typeof window.YUI === 'function'
    };
}"""


class PlaywrightAutocompleteDriver(AutocompleteDriver):
    def __init__(self, page: Any, portal_url: str):
        self._page = page
        self._portal_url = portal_url

    def locate_control(self, field_id: str, suffix: str) -> ResolvedAutocompleteControl:
        if self._page is None or self._page.is_closed():
            raise AutocompleteControlNotFound()
        matches: list[tuple[Any, Any]] = []
        found_but_disabled = False
        selector = f'[name$="{suffix}"]'
        for frame in list(self._page.frames):
            try:
                locator = frame.locator(selector)
                for index in range(min(locator.count(), 20)):
                    candidate = locator.nth(index)
                    if not candidate.is_visible():
                        continue
                    try:
                        enabled = candidate.is_enabled()
                        readonly = candidate.get_attribute("readonly") is not None
                    except Exception:
                        continue
                    if not enabled or readonly:
                        found_but_disabled = True
                        continue
                    matches.append((frame, candidate))
            except Exception:
                continue
        if not matches:
            if found_but_disabled:
                raise AutocompleteControlDisabled()
            raise AutocompleteControlNotFound()
        if len(matches) != 1:
            raise AutocompleteControlAmbiguous()
        frame, locator = matches[0]
        try:
            tag_name = locator.evaluate("element => element.tagName.toLowerCase()")
            classes = tuple((locator.get_attribute("class") or "").split())
            role = locator.get_attribute("role")
            aria_autocomplete = locator.get_attribute("aria-autocomplete")
            mapping = next(
                SIDJILCOM_CONTROL_MAP[path]
                for path in ("activite", "commune_wilaya", "physique.nationalite", "morale.nationalite")
                if SIDJILCOM_CONTROL_MAP[path].name_suffix == suffix
            )
            if tag_name not in ("input", "textarea"):
                raise AutocompleteControlNotFound()
            if mapping.css_class and mapping.css_class not in classes:
                raise AutocompleteControlNotFound()
            if mapping.aria_autocomplete and aria_autocomplete != mapping.aria_autocomplete:
                raise AutocompleteControlNotFound()
            frame_index = list(self._page.frames).index(frame)
            frame_label = "Document principal" if frame_index == 0 else f"Frame {frame_index}"
            info = AutocompleteControlInfo(
                field_id=field_id,
                label=AUTOCOMPLETE_FIELD_LABELS[field_id],
                suffix=suffix,
                tag_name=tag_name,
                element_id=locator.get_attribute("id"),
                class_names=classes[:12],
                role=role,
                aria_autocomplete=aria_autocomplete,
                aria_haspopup=locator.get_attribute("aria-haspopup"),
                aria_controls=locator.get_attribute("aria-controls"),
                aria_owns=locator.get_attribute("aria-owns"),
                aria_activedescendant=locator.get_attribute("aria-activedescendant"),
                aria_expanded=locator.get_attribute("aria-expanded"),
                autocomplete_attribute=locator.get_attribute("autocomplete"),
                frame_label=frame_label,
                enabled=True,
                yui_global_available=bool(frame.evaluate("() => typeof window.YUI === 'function'")),
            )
            return ResolvedAutocompleteControl(info, locator, frame)
        except AutocompleteControlNotFound:
            raise
        except Exception:
            raise AutocompleteControlNotFound() from None

    def page_marker(self) -> str:
        if self._page is None or self._page.is_closed():
            return ""
        try:
            return sanitize_current_url(self._page.url, self._portal_url)
        except Exception:
            return ""

    def current_value(self, handle: Any) -> str:
        try:
            return handle.input_value(timeout=1_000)
        except Exception:
            raise AutocompleteControlNotFound() from None

    def type_sequentially(self, handle: Any, text: str) -> None:
        try:
            handle.press_sequentially(text, delay=12, timeout=3_000)
        except Exception:
            raise AutocompleteControlNotFound() from None

    def inspect(self, control: ResolvedAutocompleteControl) -> AutocompleteSnapshot:
        try:
            raw = control.handle.evaluate(AUTOCOMPLETE_DOM_SNAPSHOT_SCRIPT)
            containers = tuple(
                AutocompleteContainerInfo(
                    tag_name=item["tagName"],
                    element_id=item["elementId"],
                    class_names=tuple(item["classNames"]),
                    role=item["role"],
                    aria_expanded=item["ariaExpanded"],
                    visible=bool(item["visible"]),
                    relation=item["relation"],
                    selector=item["selector"],
                )
                for item in raw.get("containers", ())
            )
            suggestions = tuple(
                AutocompleteSuggestion(
                    index=int(item["index"]),
                    text=str(item["text"]),
                    tag_name=str(item["tagName"]),
                    class_names=tuple(item["classNames"]),
                    role=item["role"],
                    aria_selected=item["ariaSelected"],
                    data_attribute_names=tuple(item["dataAttributeNames"]),
                    safe_to_select=bool(item["safeToSelect"]),
                    selector=str(item["selector"]),
                )
                for item in raw.get("suggestions", ())
            )
            info = control.info
            refreshed = AutocompleteControlInfo(
                field_id=info.field_id,
                label=info.label,
                suffix=info.suffix,
                tag_name=raw["input"]["tagName"],
                element_id=raw["input"]["elementId"],
                class_names=tuple(raw["input"]["classNames"]),
                role=raw["input"]["role"],
                aria_autocomplete=raw["input"]["ariaAutocomplete"],
                aria_haspopup=raw["input"]["ariaHaspopup"],
                aria_controls=raw["input"]["ariaControls"],
                aria_owns=raw["input"]["ariaOwns"],
                aria_activedescendant=raw["input"]["ariaActiveDescendant"],
                aria_expanded=raw["input"]["ariaExpanded"],
                autocomplete_attribute=raw["input"]["autocompleteAttribute"],
                frame_label=info.frame_label,
                enabled=bool(raw["input"]["enabled"]),
                yui_global_available=bool(raw["yuiGlobalAvailable"]),
            )
            return AutocompleteSnapshot(
                control=refreshed,
                containers=containers,
                suggestions=suggestions,
                page_marker=self.page_marker(),
                nearby_controls=tuple(str(value) for value in raw.get("nearbyControls", ())),
            )
        except Exception:
            raise AutocompleteControlNotFound() from None

    def wait(self, milliseconds: int) -> None:
        if self._page is None or self._page.is_closed():
            raise AutocompletePageChanged()
        self._page.wait_for_timeout(max(0, min(milliseconds, 500)))

    def click_suggestion(self, control: ResolvedAutocompleteControl, suggestion: AutocompleteSuggestion) -> None:
        try:
            if control.frame is None:
                raise AutocompleteControlNotFound()
            locator = control.frame.locator(suggestion.selector)
            if locator.count() != 1 or not locator.is_visible():
                raise AutocompleteControlNotFound()
            safe = locator.evaluate(
                "element => !element.matches('a[href],[role=link],button,[role=button],input[type=submit],input[type=reset]') && "
                "!element.closest('a[href],[role=link],button,[role=button],input[type=submit],input[type=reset]') && "
                "!element.querySelector('a[href],[role=link],button,[role=button],input[type=submit],input[type=reset]')"
            )
            text = " ".join((locator.inner_text(timeout=1_000) or "").split())
            if not safe or text != suggestion.text:
                raise AutocompleteControlNotFound()
            locator.click(timeout=2_000)
        except AutocompleteControlNotFound:
            raise
        except Exception:
            raise AutocompleteControlNotFound() from None

    def clear(self, control: ResolvedAutocompleteControl) -> None:
        try:
            control.handle.fill("", timeout=2_000)
        except Exception:
            raise AutocompleteControlNotFound() from None
