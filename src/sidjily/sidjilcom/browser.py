"""Adaptateur Playwright conservant le profil isolé propre à SIDJILY."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urljoin, urlsplit

from sidjily.sidjilcom.autocomplete import (
    AutocompleteObservation,
    AutocompleteResetResult,
    AutocompleteSelectionResult,
    AutocompleteTestError,
    AutocompleteTester,
)
from sidjily.sidjilcom.autocomplete_playwright import PlaywrightAutocompleteDriver
from sidjily.sidjilcom.config import SessionConfig
from sidjily.sidjilcom.diagnostics import (
    FormDiagnosticBundle,
    build_form_diagnostics,
    format_diagnostic,
    format_search_mode_comparison,
    sanitize_metadata_text,
)
from sidjily.sidjilcom.criteria import SearchMode
from sidjily.sidjilcom.search import (
    SearchExecutionError,
    SearchExecutor,
    SearchNavigationUnexpected,
    SearchObservation,
    SearchStep,
    validate_first_controlled_search,
)
from sidjily.sidjilcom.search_playwright import PlaywrightSearchDriver, session_expired_visible
from sidjily.sidjilcom.search_form_diagnostics import (
    format_real_form_report,
    snapshot_signature,
)
from sidjily.sidjilcom.selectors import (
    DASHBOARD_ROUTE,
    DEFAULT_ENTERPRISE_SEARCH_ROUTE,
    NAVIGATION_LABEL_PATTERNS,
    PageEvidence,
    collect_page_evidence,
    identify_section,
    is_portal_host,
    sanitize_current_url,
)


@dataclass(frozen=True, slots=True)
class NavigationItem:
    label: str
    url: str


@dataclass(frozen=True, slots=True)
class PageDiagnostics:
    url: str
    title: str
    section: str
    navigation_items: tuple[NavigationItem, ...]
    visible_fields: tuple[str, ...]
    form_diagnostics: FormDiagnosticBundle = FormDiagnosticBundle((), ())
    report: str = ""
    portlet_scope_found: bool = False
    portlet_scopes: tuple[tuple[str, str, str, str], ...] = ()


class SearchModeAnalysisError(RuntimeError):
    """Échec d'analyse des modes, avec un message qui ne reflète pas le contenu de page."""


SEARCH_MODE_PORTLET_MARKER = "dz_cnrc_sidjilcom_recherchedetaillee_portlet_RechercheDetailleePortlet"
SEARCH_MODE_LABELS = ("PERSONNES PHYSIQUES", "PERSONNES MORALES")


@dataclass(frozen=True, slots=True)
class _SearchModeCandidate:
    label: str
    frame: Any
    selector: str
    text: str
    text_source: str
    tag_name: str
    href: str
    href_allowed: bool
    has_href_parameters: bool
    visible: bool
    enabled: bool
    clickable: bool
    parent: str
    context: str
    paired_context: bool
    in_portlet: bool
    frame_name: str


def normalize_search_mode_label(text: object) -> str:
    """Normalise espaces Unicode, contenu multiligne et casse pour comparer un libellé exact."""
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    return " ".join(normalized.split()).casefold()


def _safe_search_mode_href(href: str, config: SessionConfig) -> str:
    """N'émet jamais query/fragment ni segment de chemin ressemblant à un identifiant."""
    sanitized = sanitize_current_url(href, config.url)
    parsed = urlsplit(sanitized)
    if re.search(r"token|session|auth|secret|csrf|cookie|password|credential|email|phone|\d{6,}|[A-Fa-f0-9]{32,}", parsed.path, re.IGNORECASE):
        return f"{parsed.scheme}://{parsed.netloc}/[chemin masqué]"
    return sanitized


SEARCH_MODE_CANDIDATES_SCRIPT = r"""({labels, portletMarker}) => {
    const normalize = value => String(value || '').normalize('NFKC')
        .replace(/[\u00a0\u2000-\u200a\u202f\u205f\u3000]/g, ' ')
        .replace(/\s+/gu, ' ').trim().toLocaleUpperCase('fr');
    const expected = new Map(labels.map(label => [normalize(label), label]));
    const interactiveSelector = 'a, area, button, [role="link"], [role="button"], [role="tab"], ' +
        '[role="menuitem"], [role="option"], [onclick], [tabindex]:not([tabindex="-1"])';
    const safeToken = value => {
        const text = String(value || '');
        return text.length <= 60 && /^[A-Za-z][A-Za-z0-9_:-]*$/.test(text) &&
            !/(token|session|auth|secret|csrf|cookie|password|credential|user|account|profile|email|phone|personal)/i.test(text) &&
            !/\d{6,}/.test(text);
    };
    const describe = element => {
        if (!element || element.nodeType !== Node.ELEMENT_NODE) return 'inconnu';
        const tag = element.tagName.toLowerCase();
        const classes = Array.from(element.classList || [])
            .filter(name => safeToken(name) && /(mode|choice|tab|link|nav|menu|portlet|search|button|btn|container|wrapper|panel|item|active|option)/i.test(name))
            .slice(0, 2).map(name => `.${name}`).join('');
        return `${tag}${classes}`;
    };
    const selectorFor = element => {
        const parts = [];
        let current = element;
        while (current && current.nodeType === Node.ELEMENT_NODE && parts.length < 32) {
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
    const visible = element => {
        let current = element;
        while (current && current.nodeType === Node.ELEMENT_NODE) {
            const style = window.getComputedStyle(current);
            if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0' ||
                current.hasAttribute('hidden') || current.getAttribute('aria-hidden') === 'true') return false;
            current = current.parentElement;
        }
        return !!element.getClientRects().length;
    };
    const labelFor = element => {
        const rendered = normalize(element.innerText || '');
        if (expected.has(rendered)) return {label: expected.get(rendered), source: 'texte visible'};
        const textContent = normalize(element.textContent || '');
        if (expected.has(textContent)) return {label: expected.get(textContent), source: 'texte des éléments enfants'};
        const aria = normalize(element.getAttribute('aria-label') || '');
        if (expected.has(aria)) return {label: expected.get(aria), source: 'aria-label'};
        const title = normalize(element.getAttribute('title') || '');
        if (expected.has(title)) return {label: expected.get(title), source: 'title accessible'};
        const alt = normalize(element.getAttribute('alt') || '');
        if (expected.has(alt)) return {label: expected.get(alt), source: 'texte alternatif accessible'};
        const labelledBy = (element.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean)
            .map(id => document.getElementById(id)?.innerText || '').join(' ');
        const accessible = normalize(labelledBy);
        if (expected.has(accessible)) return {label: expected.get(accessible), source: 'aria-labelledby'};
        return null;
    };
    const cursorTarget = element => {
        let current = element;
        for (let depth = 0; current && depth < 5; depth++, current = current.parentElement) {
            if (window.getComputedStyle(current).cursor === 'pointer') return current;
        }
        return null;
    };
    const candidates = new Map(labels.map(label => [label, new Map()]));
    const add = (target, label, source) => {
        const map = candidates.get(label);
        if (!map || map.has(target)) return;
        let href = '';
        let hrefAllowed = true;
        let hasHrefParameters = false;
        if (target instanceof HTMLAnchorElement || target instanceof HTMLAreaElement) {
            if (target.hasAttribute('href')) {
                const credentialMarker = target.matches('[href*="@"], [href*="%40" i]');
                const protocol = target.protocol.toLowerCase();
                const hostname = target.hostname.toLowerCase();
                const port = target.port;
                const internal = protocol === 'https:' && hostname === 'sidjilcom.cnrc.dz' &&
                    (!port || port === '443') && !credentialMarker;
                hrefAllowed = internal;
                hasHrefParameters = target.matches('[href*="?"], [href*="#"]');
                href = internal
                    ? `${protocol}//${hostname}${target.pathname}`
                    : `${protocol}//${hostname || '[hôte masqué]'}/[chemin masqué]`;
            }
        } else if (target.hasAttribute('href')) {
            // Un href non standard n'est pas extrait; seuls les liens HTML standard sont validés.
            hrefAllowed = false;
            hasHrefParameters = target.matches('[href*="?"], [href*="#"]');
            href = '[href non standard masqué]';
        }
        const disabled = !!target.disabled || target.matches(':disabled') ||
            target.getAttribute('aria-disabled') === 'true' ||
            !!target.closest('[aria-disabled="true"]');
        const tag = target.tagName.toLowerCase();
        const role = (target.getAttribute('role') || '').toLowerCase();
        const buttonType = (target.getAttribute('type') || 'submit').toLowerCase();
        const inputType = (target.getAttribute('type') || 'text').toLowerCase();
        const forbiddenFormButton = (tag === 'button' && ['submit', 'reset'].includes(buttonType)) ||
            (tag === 'input' && ['submit', 'reset', 'image'].includes(inputType));
        const nativeClickTarget = (tag === 'a' || tag === 'area') && target.hasAttribute('href') ||
            tag === 'button' && !forbiddenFormButton;
        const handlerClickTarget = target.hasAttribute('onclick') ||
            (target.hasAttribute('tabindex') && Number(target.getAttribute('tabindex')) >= 0) ||
            ['link', 'button', 'tab', 'menuitem', 'option'].includes(role) ||
            window.getComputedStyle(target).cursor === 'pointer';
        const reallyClickable = !forbiddenFormButton && (nativeClickTarget || handlerClickTarget);
        if (!reallyClickable) return;
        const portlet = target.closest(`[id*="${portletMarker}"], [class*="${portletMarker}"]`);
        map.set(target, {
            label, text: label, text_source: source,
            tag_name: tag, href, href_allowed: hrefAllowed,
            has_href_parameters: hasHrefParameters, visible: visible(target), enabled: !disabled,
            clickable: true, selector: selectorFor(target), parent: describe(target.parentElement),
            context: `parent proche ${describe(target.parentElement)}`,
            paired_context: false, in_portlet: !!portlet, _target: target
        });
    };

    const targets = new Set(document.querySelectorAll(interactiveSelector));
    // Rechercher également les zones pointer sans role/onClick explicite, sans lire le texte
    // général de la page : seuls les candidats interactifs et leurs descendants sont inspectés.
    for (const element of document.querySelectorAll('*')) {
        if (window.getComputedStyle(element).cursor !== 'pointer') continue;
        const parent = element.parentElement;
        if (!parent || window.getComputedStyle(parent).cursor !== 'pointer') targets.add(element);
    }
    for (const target of targets) {
        const ownMatch = labelFor(target);
        const children = Array.from(target.querySelectorAll('*'));
        const childMatches = children.map(child => ({child, match: labelFor(child)}))
            .filter(item => item.match);
        const targetIsSemantic = target.matches(interactiveSelector);
        const ownDuplicatedByChild = ownMatch && childMatches.some(item => item.match.label === ownMatch.label);
        if (ownMatch && (targetIsSemantic || !ownDuplicatedByChild)) {
            add(target, ownMatch.label, ownMatch.source);
        }
        for (const {child, match: childMatch} of childMatches) {
            const clickTarget = child.closest(interactiveSelector) || cursorTarget(child) || target;
            add(clickTarget, childMatch.label, childMatch.source);
        }
    }

    for (const [label, map] of candidates) {
        const counterpart = labels.find(other => other !== label);
        const otherTargets = Array.from(candidates.get(counterpart).entries())
            .filter(([_other, record]) => record.clickable)
            .map(([other]) => other);
        for (const [target, record] of map) {
            let ancestor = target.parentElement;
            for (let depth = 0; ancestor && depth < 6; depth++, ancestor = ancestor.parentElement) {
                const tag = ancestor.tagName.toLowerCase();
                if (tag === 'body' || tag === 'html') break;
                if (otherTargets.some(other => ancestor.contains(other))) {
                    record.paired_context = true;
                    record.context = `conteneur partagé ${describe(ancestor)}`;
                    break;
                }
            }
            delete record._target;
        }
    }
    return Array.from(candidates.values()).flatMap(map => Array.from(map.values()));
}"""


SEARCH_MODE_STABILITY_SCRIPT = r"""async () => {
    const marker = 'dz_cnrc_sidjilcom_recherchedetaillee_portlet_RechercheDetailleePortlet';
    const portletSelector = `[id*="${marker}"], [class*="${marker}"]`;
    const loadingSelector = '[aria-busy="true"], [role="progressbar"], .loading, .loader, .spinner, ' +
        '[class*="loading" i], [class*="loader" i], [class*="spinner" i]';
    const controlSelector = 'input:not([type="hidden"]):not([type="password"]), select, textarea, button, ' +
        '[role="textbox"], [role="combobox"], [role="searchbox"]';
    const timeoutMs = 20000;
    const quietMs = 900;
    const sampleMs = 250;
    const visible = element => {
        const style = window.getComputedStyle(element);
        return !!(element.getClientRects().length && style.display !== 'none' &&
            style.visibility !== 'hidden' && style.opacity !== '0' &&
            !element.closest('[hidden], [aria-hidden="true"]'));
    };
    const currentPortlet = () => document.querySelector(portletSelector);
    const formCount = root => (root.matches('form') ? 1 : 0) + root.querySelectorAll('form').length;
    const controlCount = root => (root.matches(controlSelector) ? 1 : 0) +
        root.querySelectorAll(controlSelector).length;
    const hasVisibleLoader = root => {
        const indicators = [
            ...(root.matches(loadingSelector) ? [root] : []),
            ...Array.from(root.querySelectorAll(loadingSelector))
        ];
        return indicators.some(visible);
    };
    return await new Promise(resolve => {
        let settled = false;
        let sampleTimer;
        let timeoutTimer;
        let lastMutationAt = performance.now();
        let previousControlCount = -1;
        let stableSamples = 0;
        const finish = stable => {
            if (settled) return;
            settled = true;
            observer.disconnect();
            document.removeEventListener('readystatechange', scheduleSample);
            clearTimeout(sampleTimer);
            clearTimeout(timeoutTimer);
            resolve(stable);
        };
        const observer = new MutationObserver(() => {
            lastMutationAt = performance.now();
            previousControlCount = -1;
            stableSamples = 0;
            scheduleSample();
        });
        function scheduleSample() {
            if (settled || sampleTimer) return;
            sampleTimer = setTimeout(sample, sampleMs);
        }
        function sample() {
            sampleTimer = null;
            if (settled) return;
            const root = currentPortlet();
            if (!root || document.readyState === 'loading' || formCount(root) === 0 || hasVisibleLoader(root)) {
                previousControlCount = -1;
                stableSamples = 0;
                scheduleSample();
                return;
            }
            const count = controlCount(root);
            if (count === 0) {
                previousControlCount = -1;
                stableSamples = 0;
                scheduleSample();
                return;
            }
            stableSamples = count === previousControlCount ? stableSamples + 1 : 0;
            previousControlCount = count;
            if (stableSamples >= 2 && performance.now() - lastMutationAt >= quietMs) {
                finish(true);
                return;
            }
            scheduleSample();
        }
        observer.observe(document.documentElement, {
            subtree: true, childList: true, attributes: true, characterData: true
        });
        document.addEventListener('readystatechange', scheduleSample);
        timeoutTimer = setTimeout(() => finish(false), timeoutMs);
        scheduleSample();
    });
}"""


DOM_SNAPSHOT_SCRIPT = r"""(options = {}) => {
    const sensitiveName = /password|passwd|token|secret|csrf|cookie|session|auth|credential|bearer|api.?key|access.?key/i;
    const allowedOptionLabels = ['type de personne', 'personne physique', 'personne morale',
        'wilaya', 'commune', 'secteur', 'activite', 'forme juridique', 'conformite',
        'etat commercant', 'nationalite', 'qualite'];
    const sensitiveOptionLabels = ['nom', 'prenom', 'email', 'e-mail', 'telephone', 'date',
        'numero', 'inscription', 'raison sociale', 'commercial', 'dirigeant', 'adresse', 'nif', 'nis'];
    const safeDataAttribute = /^data-(?:test(?:id)?|qa|cy|automation-id|field(?:-name)?|role|select2-id|ajax(?:--?(?:url|type|method))?|api(?:-(?:url|method))?|endpoint(?:-url)?|url|href|method|remote|controller|component|widget|target|parent(?:-id)?|depends-on|dependent-on|dependency|cascade)$/i;
    const visible = element => {
        const style = window.getComputedStyle(element);
        return !!(element.getClientRects().length && style.visibility !== 'hidden' &&
            style.display !== 'none' && style.opacity !== '0' &&
            !element.closest('[hidden], [aria-hidden="true"]'));
    };
    const dataAttributes = element => {
        const result = {};
        for (const attribute of Array.from(element.attributes)) {
            if (options.formOnly && /url|href|endpoint|ajax|api|remote|request/i.test(attribute.name)) continue;
            if (safeDataAttribute.test(attribute.name) && !sensitiveName.test(attribute.name)) {
                const rawValue = element.getAttribute(attribute.name) || '';
                const endpointAttribute = /url|href/i.test(attribute.name) ||
                    (/^data-(api|ajax|endpoint|remote)$/i.test(attribute.name) &&
                        (rawValue.startsWith('/') || rawValue.toLowerCase().startsWith('https://')));
                const metadataValue = endpointAttribute ? safeDestination(rawValue) : rawValue;
                if (sensitiveName.test(metadataValue) || /\b[^\s@]+@[^\s@]+\.[^\s@]+\b|\d{6,}|\beyJ[A-Za-z0-9_-]{12,}\./i.test(metadataValue)) {
                    continue;
                }
                result[attribute.name] = metadataValue;
            }
        }
        return result;
    };
    const safeDestination = rawHref => {
        const pathOnly = (rawHref || '').split(/[?#]/, 1)[0];
        if (!pathOnly) return '';
        try {
            const destination = new URL(pathOnly, window.location.href);
            if (!['https:', 'http:'].includes(destination.protocol)) return '';
            if (destination.protocol === 'https:' && destination.hostname.toLowerCase() === 'sidjilcom.cnrc.dz') {
                return `${destination.protocol}//${destination.hostname}${destination.pathname}`;
            }
            return `${destination.protocol}//${destination.host}/[chemin masqué]`;
        } catch (_error) {
            return '';
        }
    };
    const safeHref = element => safeDestination(element.getAttribute('href') || '');
    const inlineHandlerName = element => {
        const handler = element.getAttribute('onclick') || '';
        if (!handler) return '';
        const match = handler.trim().match(/^(?:return\s+)?([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\(\s*\)\s*;?$/);
        return match ? `${match[1]}()` : 'code masqué';
    };
    const hierarchy = element => {
        const path = [];
        let current = element;
        while (current && current.nodeType === Node.ELEMENT_NODE && path.length < 9) {
            const tag = current.tagName.toLowerCase();
            const id = current.id ? `#${current.id}` : '';
            const classes = Array.from(current.classList || []).slice(0, 3)
                .map(name => `.${name}`).join('');
            path.unshift(`${tag}${id}${classes}`);
            if (tag === 'body') break;
            current = current.parentElement;
        }
        return path;
    };
    const containerInfo = element => {
        let container = element.closest('fieldset, section, [role="group"], [role="region"]');
        if (!container) {
            for (let current = element.parentElement, depth = 0; current && depth < 7; current = current.parentElement, depth++) {
                const heading = Array.from(current.children).find(child =>
                    /^(?:H[1-6]|LEGEND)$/i.test(child.tagName));
                if (heading) { container = current; break; }
            }
        }
        if (!container) return {tag: '', id: '', class_name: '', role: '', heading: '', visible: false};
        const heading = container.querySelector('legend, h1, h2, h3, h4, h5, h6');
        return {
            tag: container.tagName.toLowerCase(), id: container.id || '',
            class_name: container.getAttribute('class') || '',
            role: container.getAttribute('role') || '',
            heading: heading && visible(heading) ? (heading.innerText || heading.textContent || '').trim() : '',
            visible: visible(container)
        };
    };
    const sectionInfo = element => {
        for (let current = element.parentElement, depth = 0; current && depth < 10; current = current.parentElement, depth++) {
            const semanticSection = current.matches('section, [role="region"]');
            const directHeading = Array.from(current.children).find(child =>
                /^(?:H[1-6]|LEGEND)$/i.test(child.tagName));
            const heading = semanticSection
                ? current.querySelector('h1, h2, h3, h4, h5, h6, legend')
                : directHeading;
            if (!heading || !visible(heading)) continue;
            return {
                tag: current.tagName.toLowerCase(), id: current.id || '',
                class_name: current.getAttribute('class') || '',
                role: current.getAttribute('role') || '',
                heading: (heading.innerText || heading.textContent || '').trim(),
                visible: visible(current)
            };
        }
        return {tag: '', id: '', class_name: '', role: '', heading: '', visible: false};
    };
    const associatedText = element => {
        const labels = Array.from(element.labels || [])
            .filter(label => scope.contains(label))
            .map(label => label.innerText?.trim() || '').filter(Boolean);
        if (!labels.length && element.id) {
            for (const label of scope.querySelectorAll('label[for]')) {
                if (label.getAttribute('for') === element.id && label.innerText?.trim()) {
                    labels.push(label.innerText.trim());
                }
            }
        }
        const labelledBy = (element.getAttribute('aria-labelledby') || '').split(/\s+/)
            .filter(Boolean)
            .map(id => {
                const referenced = document.getElementById(id);
                return referenced && scope.contains(referenced) ? referenced.innerText?.trim() || '' : '';
            }).filter(Boolean);
        return labels.join(' ') || labelledBy.join(' ') ||
            element.getAttribute('aria-label') || element.getAttribute('placeholder') || '';
    };
    const formInfo = element => {
        const form = element.closest('form') || element.form || null;
        if (!form) return {id: 'hors-formulaire', element_id: '', title: 'Hors formulaire', name: '', action: '', method: '', class_name: '', role: '', visible: false};
        const id = form.id || `formulaire-${query('form').indexOf(form) + 1}`;
        const action = element.getAttribute('formaction') ?? form.getAttribute('action') ?? '';
        const method = element.getAttribute('formmethod') ?? form.getAttribute('method') ?? 'GET';
        return {
            id,
            element_id: form.id || '',
            title: form.getAttribute('aria-label') || form.getAttribute('title') ||
                form.querySelector('legend')?.innerText?.trim() || `Formulaire ${id}`,
            name: form.getAttribute('name') || '',
            action: safeDestination(action),
            method: method.toUpperCase(),
            class_name: form.getAttribute('class') || '',
            role: form.getAttribute('role') || '',
            visible: visible(form)
        };
    };
    const mayReadOptionText = label => {
        const normalized = label.normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
        const location = normalized.includes('wilaya') || normalized.includes('commune');
        const hasSensitiveLabel = sensitiveOptionLabels.some(word =>
            normalized.includes(word) && !(location && word === 'inscription'));
        if (hasSensitiveLabel) return false;
        return allowedOptionLabels.some(word => normalized.includes(word));
    };
    const portletSelector = '[id*="dz_cnrc_sidjilcom_recherchedetaillee_portlet_RechercheDetailleePortlet"], ' +
        '[class*="dz_cnrc_sidjilcom_recherchedetaillee_portlet_RechercheDetailleePortlet"]';
    const scope = options.portletOnly ? document.querySelector(portletSelector) : document;
    if (!scope) return {forms: [], fields: [], buttons: [], clickables: [], scope_found: false, scope_info: null};
    const scopeInfo = options.portletOnly && scope.nodeType === Node.ELEMENT_NODE ? {
        tag_name: scope.tagName.toLowerCase(), id: scope.id || '', class_name: scope.getAttribute('class') || ''
    } : null;
    const query = selector => [
        ...(scope.nodeType === Node.ELEMENT_NODE && scope.matches(selector) ? [scope] : []),
        ...Array.from(scope.querySelectorAll(selector))
    ];
    const forms = query('form').map((form, index) => ({
        form_id: form.id || `formulaire-${index + 1}`,
        form_element_id: form.id || '',
        form_title: form.getAttribute('aria-label') || form.getAttribute('title') ||
            form.querySelector('legend')?.innerText?.trim() || `Formulaire ${index + 1}`,
        form_name: form.getAttribute('name') || '',
        action: safeDestination(form.getAttribute('action') || ''),
        method: (form.getAttribute('method') || 'GET').toUpperCase(),
        class_name: form.getAttribute('class') || '',
        role: form.getAttribute('role') || '',
        visible: visible(form)
    }));
    const fields = [];
    const buttons = [];
    const clickables = [];
    const selector = (options.formOnly ? [
        'input', 'select', 'textarea', 'button', '[role="textbox"]', '[role="combobox"]',
        '[role="searchbox"]', '[role="button"]', '[contenteditable="true"]'
    ] : [
        'input', 'select', 'textarea', 'button', '[role="textbox"]', '[role="combobox"]',
        '[role="searchbox"]', '[role="button"]', '[role="link"]', '[role="menuitem"]',
        '[contenteditable="true"]', 'a[href]', '[onclick]', '[tabindex]'
    ]).join(',');
    for (const element of query(selector)) {
        const tag = element.tagName.toLowerCase();
        const type = (element.getAttribute('type') || (tag === 'input' ? 'text' : tag)).toLowerCase();
        const name = element.getAttribute('name') || '';
        if (type === 'password' || type === 'hidden' || sensitiveName.test(name)) continue;
        const role = element.getAttribute('role') || '';
        const form = formInfo(element);
        const label = associatedText(element);
        const listId = element.getAttribute('list') || '';
        const referencedDataList = listId ? document.getElementById(listId) : null;
        const dataListInScope = referencedDataList &&
            (scope.nodeType === Node.DOCUMENT_NODE || scope.contains(referencedDataList));
        const dataList = dataListInScope ? referencedDataList : null;
        const record = {
            label,
            associated_text: label,
            html_type: type,
            role: role || (element.isContentEditable ? 'textbox' : ''),
            name,
            id: element.id || '',
            class_name: element.getAttribute('class') || '',
            tag_name: tag,
            href: options.formOnly ? '' : safeHref(element),
            aria_label: element.getAttribute('aria-label') || '',
            aria_labelledby: element.getAttribute('aria-labelledby') || '',
            aria_autocomplete: element.getAttribute('aria-autocomplete') || '',
            list_id: listId,
            placeholder: element.getAttribute('placeholder') || '',
            data_attributes: dataAttributes(element),
            required: !!element.required || element.hasAttribute('required') ||
                element.getAttribute('aria-required') === 'true',
            readonly: !!element.readOnly || element.hasAttribute('readonly') ||
                element.getAttribute('aria-readonly') === 'true',
            disabled: !!element.disabled || element.getAttribute('aria-disabled') === 'true',
            visible: visible(element),
            hierarchy: hierarchy(element),
            container: containerInfo(element),
            section: sectionInfo(element),
            form_id: form.id,
            form_element_id: form.element_id,
            form_title: form.title,
            form_name: form.name,
            form_action: form.action,
            form_method: form.method,
            form_class_name: form.class_name,
            form_role: form.role,
            form_visible: form.visible,
            onclick_present: element.hasAttribute('onclick'),
            onclick_handler: options.formOnly ? '' : inlineHandlerName(element),
            option_count: tag === 'select' ? element.options.length : (dataList?.options.length || 0),
            options: mayReadOptionText(label) && tag === 'select'
                ? Array.from(element.options).map(option => option.innerText?.trim() || '')
                : mayReadOptionText(label) && dataList
                    ? Array.from(dataList.options).map(option => option.label?.trim() || option.innerText?.trim() || '')
                    : []
        };
        const isButton = tag === 'button' ||
            (tag === 'input' && ['submit', 'button', 'reset', 'image'].includes(type)) || role === 'button';
        const tabIndex = element.getAttribute('tabindex');
        const potentiallyClickable = !options.formOnly && (isButton || tag === 'a' || role === 'link' || role === 'menuitem' ||
            element.hasAttribute('onclick') || (tabIndex !== null && Number(tabIndex) >= 0));
        if (options.formOnly && isButton && !(element.form || element.closest('form'))) continue;
        if (isButton) {
            buttons.push({...record, text: element.innerText?.trim() ||
                element.getAttribute('aria-label') || element.getAttribute('title') || '', nature: 'bouton'});
        } else if (tag === 'input' || tag === 'select' || tag === 'textarea' ||
            ['textbox', 'combobox', 'searchbox'].includes(role) || element.isContentEditable) {
            fields.push(record);
        }
        if (potentiallyClickable) {
            clickables.push({...record, text: element.innerText?.trim() ||
                element.getAttribute('aria-label') || element.getAttribute('title') || '', nature: 'élément cliquable'});
        }
    }
    return {forms, fields, buttons, clickables, scope_found: true, scope_info: scopeInfo};
}"""


class BrowserAdapter(Protocol):
    """Interface injectable pour tests et évolution du navigateur."""

    def open(self, config: SessionConfig) -> None: ...

    def inspect(self, config: SessionConfig) -> PageEvidence: ...

    def probe_authenticated_route(self, config: SessionConfig) -> None: ...

    def go_home(self, config: SessionConfig) -> None: ...

    def navigate_to_enterprise_search(self, config: SessionConfig) -> None: ...

    def navigate_to_dashboard(self, config: SessionConfig) -> None: ...

    def diagnostics(
        self, config: SessionConfig, *, portlet_only: bool = False, form_only: bool = False
    ) -> PageDiagnostics: ...

    def diagnose_search_modes(self, config: SessionConfig) -> PageDiagnostics: ...

    def diagnose_real_search_form(self, config: SessionConfig, stage: str) -> PageDiagnostics: ...

    def prepare_autocomplete_test(self, config: SessionConfig, field_id: str, query: str) -> AutocompleteObservation: ...

    def select_autocomplete_suggestion(
        self, config: SessionConfig, token: str, suggestion_index: int
    ) -> AutocompleteSelectionResult: ...

    def reset_autocomplete_test(self, config: SessionConfig, token: str) -> AutocompleteResetResult: ...

    def execute_search(
        self,
        config: SessionConfig,
        criteria: Any,
        *,
        confirmed: bool = False,
        on_step: Any = None,
    ) -> SearchObservation: ...

    def close(self) -> None: ...


class PlaywrightBrowser:
    """Navigateur Chromium visible, lancé dans un répertoire de profil SIDJILY dédié."""

    def __init__(self) -> None:
        self._playwright = None
        self._context = None
        self._page = None
        self._autocomplete_tester: AutocompleteTester | None = None
        self._real_form_snapshots: list[tuple[str, tuple[tuple[str, ...], ...]]] = []

    def open(self, config: SessionConfig) -> None:
        # Import paresseux : les tests mockés et les fonctions hors session ne lancent pas Chromium.
        from playwright.sync_api import sync_playwright

        profile: Path = config.resolved_profile_path
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            profile.chmod(0o700)
        except OSError:
            # Les ACL Windows sont gérées par le profil utilisateur du système.
            pass
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=config.headless,
            accept_downloads=False,
            timeout=config.navigation_timeout_ms,
            args=["--no-first-run"],
        )
        pages = self._context.pages
        self._page = pages[0] if pages else self._context.new_page()
        self._page.goto(config.url, wait_until="domcontentloaded", timeout=config.navigation_timeout_ms)
        self._autocomplete_tester = AutocompleteTester(
            PlaywrightAutocompleteDriver(self._page, config.url)
        )

    def probe_authenticated_route(self, config: SessionConfig) -> None:
        """Vérifie l'accès réel en ouvrant la page protégée, sans cliquer ni soumettre."""
        if self._page is None:
            raise RuntimeError("Navigateur non démarré.")
        target = urljoin(config.url, DEFAULT_ENTERPRISE_SEARCH_ROUTE)
        if not is_portal_host(target, config.url):
            raise RuntimeError("La destination de vérification n'appartient pas au portail officiel.")
        # Une navigation GET vers le formulaire protégé distingue une session valide d'une
        # redirection login; aucun formulaire n'est rempli et aucune recherche n'est envoyée.
        self._page.goto(target, wait_until="domcontentloaded", timeout=config.navigation_timeout_ms)

    def inspect(self, config: SessionConfig) -> PageEvidence:
        if self._page is None:
            raise RuntimeError("Navigateur non démarré.")
        current_url = self._page.url
        has_password_field = self._page.locator('input[type="password"]:visible').count() > 0
        has_visible_form_controls = self._page.locator(
            'input:visible, select:visible, textarea:visible'
        ).count() > 0
        has_signout_control = self._page.locator(
            'a[href*="logout" i]:visible, a[href*="signout" i]:visible, '
            'button[aria-label*="déconnexion" i]:visible, a[aria-label*="déconnexion" i]:visible, '
            '[data-testid="user-menu"]:visible'
        ).count() > 0
        parsed = urlsplit(current_url)
        if (
            is_portal_host(current_url, config.url)
            and parsed.path.rstrip("/") == DEFAULT_ENTERPRISE_SEARCH_ROUTE.rstrip("/")
        ):
            # Sur la recherche (y compris sa page de résultats), le polling de session
            # ne parcourt jamais le texte des lignes d'entreprise.
            expired = session_expired_visible(self._page)
            evidence = collect_page_evidence(
                current_url=current_url,
                portal_url=config.url,
                visible_text="",
                has_password_field=has_password_field,
                has_signout_control=has_signout_control,
                has_visible_form_controls=has_visible_form_controls,
            )
            return replace(evidence, has_expired_notice=expired)
        visible_text = self._page.locator("body").inner_text(timeout=5_000)
        return collect_page_evidence(
            current_url=current_url,
            portal_url=config.url,
            visible_text=visible_text,
            has_password_field=has_password_field,
            has_signout_control=has_signout_control,
            has_visible_form_controls=has_visible_form_controls,
        )

    def go_home(self, config: SessionConfig) -> None:
        if self._page is None:
            raise RuntimeError("Navigateur non démarré.")
        self._page.goto(config.url, wait_until="domcontentloaded", timeout=config.navigation_timeout_ms)

    def navigate_to_enterprise_search(self, config: SessionConfig) -> None:
        """Ouvre « Trouver une entreprise » sans remplir ni soumettre le formulaire."""
        self._navigate_to_target(
            config,
            label="Trouver une entreprise",
            expected_section="Trouver une entreprise",
            fallback_route=DEFAULT_ENTERPRISE_SEARCH_ROUTE,
        )

    def navigate_to_dashboard(self, config: SessionConfig) -> None:
        """Ouvre le tableau de bord des abonnés via son lien ou sa route observée."""
        self._navigate_to_target(
            config,
            label="Tableau de bord",
            expected_section="Tableau de bord",
            fallback_route=DASHBOARD_ROUTE,
        )

    def _navigate_to_target(
        self,
        config: SessionConfig,
        *,
        label: str,
        expected_section: str,
        fallback_route: str,
    ) -> None:
        if self._page is None:
            raise RuntimeError("Navigateur non démarré.")
        pattern = NAVIGATION_LABEL_PATTERNS[label]
        # On ne clique que sur un lien de navigation; aucun bouton de formulaire n'est activé.
        for role in ("link",):
            candidates = self._page.get_by_role(role, name=pattern)
            for index in range(min(candidates.count(), 5)):
                candidate = candidates.nth(index)
                href = candidate.get_attribute("href") if role == "link" else None
                if href and not is_portal_host(urljoin(self._page.url, href), config.url):
                    continue
                try:
                    bounded_timeout = min(config.navigation_timeout_ms, 8_000)
                    candidate.click(timeout=bounded_timeout)
                    self._page.wait_for_load_state("domcontentloaded", timeout=bounded_timeout)
                    if identify_section(self._page.url) in (expected_section, "Authentification"):
                        return
                except Exception:
                    # Un lien cassé ou absent déclenche le fallback vers la route observée.
                    pass
                break
        # Les routes de fallback ont été relevées à partir des liens publics du portail.
        target = urljoin(config.url, fallback_route)
        if not is_portal_host(target, config.url):
            raise RuntimeError("La destination n'appartient pas au portail officiel.")
        self._page.goto(target, wait_until="domcontentloaded", timeout=config.navigation_timeout_ms)

    def diagnostics(
        self, config: SessionConfig, *, portlet_only: bool = False, form_only: bool = False
    ) -> PageDiagnostics:
        """Inspecte la page ou le portlet; form_only exclut liens, endpoints/API et contenu hors formulaire."""
        if self._page is None:
            raise RuntimeError("Navigateur non démarré.")
        current_url = self._page.url
        navigation_items: list[NavigationItem] = []
        if not form_only:
            for label, pattern in NAVIGATION_LABEL_PATTERNS.items():
                candidates = self._page.get_by_role("link", name=pattern)
                for index in range(min(candidates.count(), 3)):
                    href = candidates.nth(index).get_attribute("href")
                    if not href:
                        continue
                    destination = urljoin(current_url, href)
                    if not is_portal_host(destination, config.url):
                        continue
                    item = NavigationItem(label, sanitize_current_url(destination, config.url))
                    if item not in navigation_items:
                        navigation_items.append(item)

        raw: dict[str, list[dict[str, object]]] = {
            "frames": [], "form_entries": [], "fields": [], "buttons": [], "clickables": []
        }
        portlet_scope_found = not portlet_only
        portlet_scopes: list[tuple[str, str, str, str]] = []
        for frame_index, frame in enumerate(list(self._page.frames)):
            fallback_name = "Document principal" if frame_index == 0 else f"Frame {frame_index}"
            frame_name = fallback_name
            frame_url = "URL indisponible"
            try:
                frame_name = sanitize_metadata_text(frame.name, 100) or fallback_name
                frame_url = sanitize_current_url(frame.url, config.url)
                snapshot = (
                    frame.evaluate(
                        DOM_SNAPSHOT_SCRIPT,
                        {"portletOnly": portlet_only, "formOnly": form_only},
                    )
                    if portlet_only or form_only else frame.evaluate(DOM_SNAPSHOT_SCRIPT)
                )
                if not isinstance(snapshot, dict):
                    raise TypeError("snapshot DOM indisponible")
                if portlet_only and snapshot.get("scope_found") is True:
                    portlet_scope_found = True
                    scope_info = snapshot.get("scope_info")
                    if isinstance(scope_info, dict):
                        portlet_scopes.append((
                            frame_name,
                            sanitize_metadata_text(scope_info.get("tag_name", ""), 40),
                            sanitize_metadata_text(scope_info.get("id", ""), 120),
                            sanitize_metadata_text(scope_info.get("class_name", ""), 160),
                        ))
            except Exception:
                # Ne pas exposer l'exception : elle pourrait contenir une URL ou des données de page.
                raw["frames"].append({
                    "name": frame_name,
                    "url": frame_url,
                    "accessible": False,
                    "form_count": 0,
                    "control_count": 0,
                })
                continue

            frame_forms = snapshot.get("forms", [])
            frame_fields = snapshot.get("fields", [])
            frame_buttons = snapshot.get("buttons", [])
            frame_clickables = snapshot.get("clickables", [])
            frame_forms = frame_forms if isinstance(frame_forms, list) else []
            frame_fields = frame_fields if isinstance(frame_fields, list) else []
            frame_buttons = frame_buttons if isinstance(frame_buttons, list) else []
            frame_clickables = frame_clickables if isinstance(frame_clickables, list) else []
            raw["frames"].append({
                "name": frame_name,
                "url": frame_url,
                "accessible": True,
                "form_count": len(frame_forms),
                "control_count": len(frame_fields) + len(frame_buttons),
            })
            for collection_name, collection in (
                ("form_entries", frame_forms),
                ("fields", frame_fields),
                ("buttons", frame_buttons),
                ("clickables", frame_clickables),
            ):
                for item in collection:
                    if not isinstance(item, dict):
                        continue
                    entry = dict(item)
                    entry["frame_name"] = frame_name
                    entry["frame_url"] = frame_url
                    raw[collection_name].append(entry)

        bundle = build_form_diagnostics(raw)
        title = sanitize_metadata_text(self._page.title(), 160)
        page = PageDiagnostics(
            url=sanitize_current_url(current_url, config.url),
            title=title,
            section=identify_section(current_url),
            navigation_items=tuple(navigation_items),
            visible_fields=tuple(
                dict.fromkeys(
                    f"{field.label} [{field.html_type}]"
                    for field in bundle.all_controls if field.visible
                )
            )[:120],
            form_diagnostics=bundle,
            report="",
            portlet_scope_found=portlet_scope_found,
            portlet_scopes=tuple(portlet_scopes),
        )
        return PageDiagnostics(
            url=page.url,
            title=page.title,
            section=page.section,
            navigation_items=page.navigation_items,
            visible_fields=page.visible_fields,
            form_diagnostics=bundle,
            report=format_diagnostic(page, bundle),
            portlet_scope_found=portlet_scope_found,
            portlet_scopes=tuple(portlet_scopes),
        )

    def diagnose_real_search_form(self, config: SessionConfig, stage: str) -> PageDiagnostics:
        """Capture et compare la structure du formulaire actuellement ouvert, sans action DOM."""
        if self._page is None or self._page.is_closed():
            raise RuntimeError("La page Chromium SIDJILY n'est pas disponible.")
        page = self.diagnostics(config, portlet_only=True, form_only=True)
        if not page.portlet_scope_found:
            # Repli de lecture seule pour observer un marqueur/portlet changé, sans naviguer.
            page = replace(
                self.diagnostics(config, portlet_only=False, form_only=True),
                portlet_scope_found=False,
            )
        driver = PlaywrightSearchDriver(self._page, config.url, lambda _mode: None, self._autocomplete_tester)
        preflight = driver.diagnose_preflight()
        signature = snapshot_signature(page.form_diagnostics, preflight)
        previous = self._real_form_snapshots[-1] if self._real_form_snapshots else None
        report = format_real_form_report(
            page, page.form_diagnostics, stage, preflight,
            previous=(previous[0], previous[1]) if previous else None,
        )
        self._real_form_snapshots.append((stage, signature))
        if len(self._real_form_snapshots) > 20:
            self._real_form_snapshots = self._real_form_snapshots[-20:]
        return replace(page, report=report)

    def _discover_search_mode_candidates(
        self, config: SessionConfig
    ) -> dict[str, list[_SearchModeCandidate]]:
        if self._page is None:
            raise SearchModeAnalysisError("Navigateur non démarré.")
        found: dict[str, list[_SearchModeCandidate]] = {label: [] for label in SEARCH_MODE_LABELS}
        for frame_index, frame in enumerate(list(self._page.frames)):
            fallback_name = "Document principal" if frame_index == 0 else f"Frame {frame_index}"
            try:
                frame_name = fallback_name
                raw_candidates = frame.evaluate(
                    SEARCH_MODE_CANDIDATES_SCRIPT,
                    {"labels": list(SEARCH_MODE_LABELS), "portletMarker": SEARCH_MODE_PORTLET_MARKER},
                )
                if not isinstance(raw_candidates, list):
                    continue
                for raw in raw_candidates:
                    if not isinstance(raw, dict):
                        continue
                    label = raw.get("label")
                    if label not in found:
                        continue
                    selector = raw.get("selector")
                    if not isinstance(selector, str) or not selector:
                        continue
                    if any(item.frame is frame and item.selector == selector for item in found[label]):
                        continue
                    safe_href = str(raw.get("href") or "")
                    if safe_href and safe_href.startswith(("https://", "http://")):
                        safe_href = _safe_search_mode_href(safe_href, config)
                    found[label].append(
                        _SearchModeCandidate(
                            label=label,
                            frame=frame,
                            selector=selector,
                            text=sanitize_metadata_text(raw.get("text"), 80) or label,
                            text_source=sanitize_metadata_text(raw.get("text_source"), 60) or "texte exact",
                            tag_name=sanitize_metadata_text(raw.get("tag_name"), 40) or "inconnu",
                            href=safe_href,
                            href_allowed=bool(raw.get("href_allowed", False)),
                            has_href_parameters=bool(raw.get("has_href_parameters", False)),
                            visible=bool(raw.get("visible", False)),
                            enabled=bool(raw.get("enabled", False)),
                            clickable=bool(raw.get("clickable", False)),
                            parent=sanitize_metadata_text(raw.get("parent"), 120) or "inconnu",
                            context=sanitize_metadata_text(raw.get("context"), 160) or "inconnu",
                            paired_context=bool(raw.get("paired_context", False)),
                            in_portlet=bool(raw.get("in_portlet", False)),
                            frame_name=frame_name,
                        )
                    )
            except Exception:
                # Un frame inaccessible est ignoré, sans journaliser URL ou contenu.
                continue
        return found

    @staticmethod
    def _choose_search_mode_candidate(
        candidates: list[_SearchModeCandidate],
    ) -> tuple[_SearchModeCandidate | None, str]:
        if not candidates:
            return None, "Aucun élément ne porte le libellé complet exact."
        eligible = [
            item for item in candidates
            if item.visible and item.enabled and item.clickable and item.href_allowed
        ]
        if not eligible:
            return None, "Aucun candidat n'est à la fois visible, activé, cliquable et sûr selon son href."

        # Les indices de conteneur commun et de destination sont des préférences seulement :
        # ils ne sont jamais une condition obligatoire de détection.
        paired = [item for item in eligible if item.paired_context]
        if paired:
            eligible = paired
        in_portlet = [item for item in eligible if item.in_portlet]
        if in_portlet:
            eligible = in_portlet
        tag_priority = {"a": 3, "area": 3, "button": 2}
        best_priority = max(tag_priority.get(item.tag_name, 1) for item in eligible)
        eligible = [item for item in eligible if tag_priority.get(item.tag_name, 1) == best_priority]

        if len(eligible) == 1:
            chosen = eligible[0]
            reasons = ["visible, activé et cliquable", "href sûr"]
            if chosen.paired_context:
                reasons.append("même contexte DOM proche que l'autre mode")
            if chosen.in_portlet:
                reasons.append("dans le portlet attendu (indice facultatif)")
            if len(candidates) > 1:
                reasons.append(f"préféré parmi {len(candidates)} candidats après comparaison visibilité/contexte/href")
            return chosen, "; ".join(reasons) + "."

        destinations = {item.href for item in eligible}
        contexts = {(id(item.frame), item.parent, item.context) for item in eligible}
        if len(destinations) == 1 and len(contexts) == 1:
            chosen = eligible[0]
            return chosen, (
                f"{len(eligible)} occurrences visibles ont le même href et le même contexte; "
                "premier élément DOM retenu comme départage déterministe."
            )
        return None, (
            f"{len(eligible)} candidats restent équivalents après comparaison; href ou contexte DOM distinct."
        )

    @staticmethod
    def _format_mode_candidate_report(
        candidates_by_label: dict[str, list[_SearchModeCandidate]],
        choices: dict[str, tuple[_SearchModeCandidate | None, str]],
    ) -> str:
        lines = ["DIAGNOSTIC DE DÉTECTION DES MODES"]
        for label in SEARCH_MODE_LABELS:
            candidates = candidates_by_label.get(label, [])
            chosen, reason = choices.get(label, (None, "Aucune décision."))
            representative = chosen or (candidates[0] if candidates else None)
            lines.extend((
                f"\nMODE {label}",
                f"- trouvé : {'oui' if candidates else 'non'}",
                f"- texte : {representative.text if representative else label}",
                f"- source du texte : {representative.text_source if representative else '—'}",
                f"- tag : {representative.tag_name if representative else '—'}",
                f"- href : {(representative.href or 'non renseigné') if representative else '—'}",
                f"- visible : {'oui' if representative and representative.visible else 'non' if representative else '—'}",
                f"- enabled : {'oui' if representative and representative.enabled else 'non' if representative else '—'}",
                f"- clickable : {'oui' if representative and representative.clickable else 'non' if representative else '—'}",
                f"- parent proche : {representative.parent if representative else '—'}",
                f"- contexte DOM minimal : {representative.context if representative else '—'}",
                f"- raison du choix : {reason}",
                f"CANDIDATS {label} : {len(candidates)}",
            ))
            for index, item in enumerate(candidates, start=1):
                href = item.href or "non renseigné"
                if item.has_href_parameters:
                    href += " (paramètres/fragment omis)"
                lines.append(
                    f"  {index}. texte={item.text}; tag={item.tag_name}; href={href}; "
                    f"visible={'oui' if item.visible else 'non'}; enabled={'oui' if item.enabled else 'non'}; "
                    f"clickable={'oui' if item.clickable else 'non'}; frame={item.frame_name}; "
                    f"parent={item.parent}; contexte={item.context}"
                )
        return "\n".join(lines)

    def _click_mode_candidate(self, candidate: _SearchModeCandidate, config: SessionConfig) -> None:
        try:
            current = candidate.frame.evaluate(
                SEARCH_MODE_CANDIDATES_SCRIPT,
                {"labels": list(SEARCH_MODE_LABELS), "portletMarker": SEARCH_MODE_PORTLET_MARKER},
            )
            still_matches = isinstance(current, list) and any(
                isinstance(item, dict)
                and item.get("label") == candidate.label
                and item.get("selector") == candidate.selector
                and item.get("visible") is True
                and item.get("enabled") is True
                and item.get("clickable") is True
                and item.get("href_allowed") is True
                for item in current
            )
            if not still_matches:
                raise SearchModeAnalysisError("Le candidat a changé ou n'est plus visible/activé.")
            locator = candidate.frame.locator(candidate.selector)
            if locator.count() != 1 or not locator.is_visible() or not locator.is_enabled():
                raise SearchModeAnalysisError("Le candidat a changé ou n'est plus visible/activé.")
            locator.click(timeout=min(config.navigation_timeout_ms, 8_000))
        except SearchModeAnalysisError:
            raise
        except Exception:
            raise SearchModeAnalysisError("Le lien du mode a été identifié mais son clic n'a pas abouti.") from None

    @staticmethod
    def _wait_for_search_portlet(frame: Any) -> None:
        try:
            stable = frame.evaluate(SEARCH_MODE_STABILITY_SCRIPT)
        except Exception:
            raise SearchModeAnalysisError("Le contenu du portlet n'a pas pu être stabilisé.") from None
        if not stable:
            raise SearchModeAnalysisError("Le contenu du portlet n'a pas été stabilisé dans le délai prévu.")

    def _capture_search_mode(
        self,
        label: str,
        config: SessionConfig,
        candidate: _SearchModeCandidate | None = None,
        diagnostic_report: str = "",
    ) -> PageDiagnostics:
        if candidate is None:
            candidates = self._discover_search_mode_candidates(config)
            candidate, reason = self._choose_search_mode_candidate(candidates.get(label, []))
            diagnostic_report = self._format_mode_candidate_report(
                candidates,
                {label: (candidate, reason)},
            )
            if candidate is None:
                raise SearchModeAnalysisError(
                    "Le mode demandé n'a pas de candidat cliquable non ambigu.\n" + diagnostic_report
                )
        try:
            self._click_mode_candidate(candidate, config)
        except SearchModeAnalysisError as exc:
            raise SearchModeAnalysisError(f"{exc}\n\n{diagnostic_report}") from None
        try:
            self._wait_for_search_portlet(candidate.frame)
            snapshot = self.diagnostics(config, portlet_only=True)
        except SearchModeAnalysisError as exc:
            raise SearchModeAnalysisError(f"{exc}\n\n{diagnostic_report}") from None
        except Exception:
            raise SearchModeAnalysisError(
                "La capture structurelle après le clic n'a pas abouti.\n\n" + diagnostic_report
            ) from None
        if not snapshot.portlet_scope_found:
            raise SearchModeAnalysisError(
                "Le portlet de recherche n'a pas été identifié après sélection du mode.\n\n" + diagnostic_report
            )
        return snapshot

    def diagnose_search_modes(self, config: SessionConfig) -> PageDiagnostics:
        """Identifie les libellés visibles, puis analyse les deux modes sans soumettre de recherche."""
        if self._page is None:
            raise SearchModeAnalysisError("Navigateur non démarré.")
        current = urlsplit(self._page.url)
        if (
            not is_portal_host(self._page.url, config.url)
            or current.path.rstrip("/") != DEFAULT_ENTERPRISE_SEARCH_ROUTE.rstrip("/")
        ):
            raise SearchModeAnalysisError("Ouvrez la page Trouver une entreprise avant l'analyse des modes.")

        candidates = self._discover_search_mode_candidates(config)
        choices = {
            label: self._choose_search_mode_candidate(candidates.get(label, []))
            for label in SEARCH_MODE_LABELS
        }
        detection_report = self._format_mode_candidate_report(candidates, choices)
        if any(candidate is None for candidate, _reason in choices.values()):
            raise SearchModeAnalysisError(
                "Détection incomplète ou ambiguë; aucun mode n'a été cliqué.\n\n" + detection_report
            )

        physical_choice = choices[SEARCH_MODE_LABELS[0]][0]
        legal_choice = choices[SEARCH_MODE_LABELS[1]][0]
        assert physical_choice is not None and legal_choice is not None
        physical = self._capture_search_mode(
            SEARCH_MODE_LABELS[0], config, physical_choice, detection_report
        )
        try:
            legal = self._capture_search_mode(
                SEARCH_MODE_LABELS[1], config, legal_choice, detection_report
            )
        except SearchModeAnalysisError:
            # Une sélection peut remplacer le menu. Recharger seulement la route visible
            # autorisée puis redétecter le second lien; aucun endpoint/API n'est appelé.
            try:
                self._page.goto(
                    urljoin(config.url, DEFAULT_ENTERPRISE_SEARCH_ROUTE),
                    wait_until="domcontentloaded",
                    timeout=config.navigation_timeout_ms,
                )
                legal = self._capture_search_mode(
                    SEARCH_MODE_LABELS[1], config, diagnostic_report=detection_report
                )
            except Exception:
                raise SearchModeAnalysisError(
                    "Le second mode n'a pas pu être analysé.\n\n" + detection_report
                ) from None

        comparison = format_search_mode_comparison(
            physical.form_diagnostics, legal.form_diagnostics
        )
        report = (
            "ANALYSE DES FORMULAIRES SIDJILCOM\n"
            "Sélection des deux modes seulement; aucun critère saisi et aucun bouton de recherche activé.\n\n"
            f"{detection_report}\n\n"
            "===== PERSONNES PHYSIQUES =====\n"
            f"{physical.report}\n\n"
            "===== PERSONNES MORALES =====\n"
            f"{legal.report}\n\n"
            f"{comparison}"
        )
        visible_fields = tuple(
            f"{mode} · {field}"
            for mode, page in (("PERSONNES PHYSIQUES", physical), ("PERSONNES MORALES", legal))
            for field in page.visible_fields
        )
        return replace(legal, visible_fields=visible_fields, report=report)

    def _select_mode_for_search(self, config: SessionConfig, mode: SearchMode) -> None:
        """Sélectionne uniquement le mode demandé via le lien visible déjà analysé."""
        if self._page is None or self._page.is_closed():
            raise SearchExecutionError("Navigateur Sidjilcom indisponible.", code="browser_unavailable")
        current = urlsplit(self._page.url)
        if (
            not is_portal_host(self._page.url, config.url)
            or current.path.rstrip("/") != DEFAULT_ENTERPRISE_SEARCH_ROUTE.rstrip("/")
        ):
            raise SearchExecutionError(
                "Ouvrez Trouver une entreprise dans le navigateur SIDJILY avant de lancer la recherche.",
                code="wrong_page",
            )
        label = (
            SEARCH_MODE_LABELS[0]
            if mode is SearchMode.PERSONNE_PHYSIQUE
            else SEARCH_MODE_LABELS[1]
        )
        candidates = self._discover_search_mode_candidates(config)
        candidate, _reason = self._choose_search_mode_candidate(candidates.get(label, []))
        if candidate is None:
            raise SearchExecutionError(
                "Le mode demandé est absent ou ambigu; aucune recherche n'a été lancée.",
                code="mode_unavailable",
            )
        try:
            self._click_mode_candidate(candidate, config)
            self._wait_for_search_portlet(candidate.frame)
            if self._page is None or self._page.is_closed():
                raise SearchNavigationUnexpected()
            if not is_portal_host(self._page.url, config.url):
                raise SearchNavigationUnexpected()
        except SearchExecutionError:
            raise
        except SearchModeAnalysisError:
            raise SearchExecutionError(
                "Le formulaire du mode demandé n'a pas pu être activé sans ambiguïté.",
                code="mode_selection_failed",
            ) from None

    def execute_search(
        self,
        config: SessionConfig,
        criteria: Any,
        *,
        confirmed: bool = False,
        on_step: Callable[[SearchStep], None] | None = None,
        on_pre_submit: Callable[[dict[str, object]], None] | None = None,
    ) -> SearchObservation:
        if self._page is None or self._page.is_closed() or self._autocomplete_tester is None:
            raise SearchExecutionError("Navigateur Sidjilcom indisponible.", code="browser_unavailable")
        validate_first_controlled_search(criteria)
        driver = PlaywrightSearchDriver(
            self._page,
            config.url,
            lambda mode: self._select_mode_for_search(config, mode),
            self._autocomplete_tester,
            timeout_ms=config.navigation_timeout_ms,
        )
        return SearchExecutor(driver).execute(
            criteria, confirmed=confirmed, on_step=on_step, on_pre_submit=on_pre_submit
        )

    def diagnose_search_results(self, config: SessionConfig) -> SearchObservation:
        """Lit les métadonnées structurelles de la page courante sans soumettre ni naviguer."""
        if self._page is None or self._page.is_closed():
            raise SearchExecutionError("Navigateur Sidjilcom indisponible.", code="browser_unavailable")
        driver = PlaywrightSearchDriver(
            self._page, config.url, lambda _mode: None, self._autocomplete_tester,
            timeout_ms=config.navigation_timeout_ms,
        )
        return driver.observe_results()

    def prepare_autocomplete_test(
        self, config: SessionConfig, field_id: str, query: str
    ) -> AutocompleteObservation:
        tester = self._require_autocomplete_tester(config)
        return tester.prepare(field_id, query)

    def select_autocomplete_suggestion(
        self, config: SessionConfig, token: str, suggestion_index: int
    ) -> AutocompleteSelectionResult:
        tester = self._require_autocomplete_tester(config)
        return tester.select(token, suggestion_index)

    def reset_autocomplete_test(self, config: SessionConfig, token: str) -> AutocompleteResetResult:
        tester = self._require_autocomplete_tester(config)
        return tester.reset(token)

    def _require_autocomplete_tester(self, config: SessionConfig) -> AutocompleteTester:
        if self._page is None or self._page.is_closed() or self._autocomplete_tester is None:
            raise AutocompleteTestError("Navigateur Sidjilcom indisponible.")
        if (
            not is_portal_host(self._page.url, config.url)
            or identify_section(self._page.url) != "Trouver une entreprise"
        ):
            raise AutocompleteTestError(
                "Ouvrez la page Trouver une entreprise avant de tester une autocomplétion."
            )
        return self._autocomplete_tester

    def close(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
        finally:
            self._context = None
            self._page = None
            self._autocomplete_tester = None
            if self._playwright is not None:
                self._playwright.stop()
            self._playwright = None
