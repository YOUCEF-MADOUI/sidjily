"""Adaptateur Playwright conservant le profil isolé propre à SIDJILY."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

from sidjily.sidjilcom.config import SessionConfig
from sidjily.sidjilcom.diagnostics import (
    FormDiagnosticBundle,
    build_form_diagnostics,
    format_diagnostic,
    format_search_mode_comparison,
    sanitize_metadata_text,
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


class SearchModeAnalysisError(RuntimeError):
    """Échec d'analyse des modes, avec un message qui ne reflète pas le contenu de page."""


SEARCH_MODE_STABILITY_SCRIPT = r"""async () => {
    const marker = 'dz_cnrc_sidjilcom_recherchedetaillee_portlet_RechercheDetailleePortlet';
    const portlet = document.querySelector(`[id*="${marker}"], [class*="${marker}"]`);
    const root = portlet?.parentElement || portlet || document.body;
    if (!root) return false;
    return await new Promise(resolve => {
        let quietTimer;
        let timeoutTimer;
        let settled = false;
        const observer = new MutationObserver(schedule);
        const finish = stable => {
            if (settled) return;
            settled = true;
            observer.disconnect();
            clearTimeout(quietTimer);
            clearTimeout(timeoutTimer);
            resolve(stable);
        };
        function schedule() {
            clearTimeout(quietTimer);
            quietTimer = setTimeout(() => finish(true), 900);
        }
        observer.observe(root, {subtree: true, childList: true, attributes: true, characterData: true});
        timeoutTimer = setTimeout(() => finish(false), 15000);
        schedule();
    });
}"""


DOM_SNAPSHOT_SCRIPT = r"""(options = {}) => {
    const sensitiveName = /password|passwd|token|secret|csrf|cookie|session|auth|credential|bearer|api.?key|access.?key/i;
    const allowedOptionLabels = ['type de personne', 'personne physique', 'personne morale',
        'wilaya', 'commune', 'secteur', 'activite', 'forme juridique', 'conformite',
        'etat commercant', 'nationalite', 'qualite'];
    const sensitiveOptionLabels = ['nom', 'prenom', 'email', 'e-mail', 'telephone', 'date',
        'numero', 'inscription', 'raison sociale', 'commercial', 'dirigeant', 'adresse', 'nif', 'nis'];
    const safeDataAttribute = /^data-(?:test(?:id)?|qa|cy|automation-id|field(?:-name)?|role|select2-id|ajax(?:--?(?:url|type|method))?|api(?:-(?:url|method))?|endpoint(?:-url)?|url|href|method|remote|controller|component|widget)$/i;
    const visible = element => {
        const style = window.getComputedStyle(element);
        return !!(element.getClientRects().length && style.visibility !== 'hidden' &&
            style.display !== 'none' && style.opacity !== '0' &&
            !element.closest('[hidden], [aria-hidden="true"]'));
    };
    const dataAttributes = element => {
        const result = {};
        for (const attribute of Array.from(element.attributes)) {
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
        if (!form) return {id: 'hors-formulaire', title: 'Hors formulaire', action: '', method: ''};
        const id = form.id || `formulaire-${query('form').indexOf(form) + 1}`;
        const action = element.getAttribute('formaction') ?? form.getAttribute('action') ?? '';
        const method = element.getAttribute('formmethod') ?? form.getAttribute('method') ?? 'GET';
        return {
            id,
            title: form.getAttribute('aria-label') || form.getAttribute('title') ||
                form.querySelector('legend')?.innerText?.trim() || `Formulaire ${id}`,
            action: safeDestination(action),
            method: method.toUpperCase()
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
    if (!scope) return {forms: [], fields: [], buttons: [], clickables: [], scope_found: false};
    const query = selector => [
        ...(scope.nodeType === Node.ELEMENT_NODE && scope.matches(selector) ? [scope] : []),
        ...Array.from(scope.querySelectorAll(selector))
    ];
    const forms = query('form').map((form, index) => ({
        form_id: form.id || `formulaire-${index + 1}`,
        form_title: form.getAttribute('aria-label') || form.getAttribute('title') ||
            form.querySelector('legend')?.innerText?.trim() || `Formulaire ${index + 1}`,
        action: safeDestination(form.getAttribute('action') || ''),
        method: (form.getAttribute('method') || 'GET').toUpperCase()
    }));
    const fields = [];
    const buttons = [];
    const clickables = [];
    const selector = [
        'input', 'select', 'textarea', 'button', '[role="textbox"]', '[role="combobox"]',
        '[role="searchbox"]', '[role="button"]', '[role="link"]', '[role="menuitem"]',
        '[contenteditable="true"]', 'a[href]', '[onclick]', '[tabindex]'
    ].join(',');
    for (const element of query(selector)) {
        const tag = element.tagName.toLowerCase();
        const type = (element.getAttribute('type') || (tag === 'input' ? 'text' : tag)).toLowerCase();
        const name = element.getAttribute('name') || '';
        if (type === 'password' || type === 'hidden' || sensitiveName.test(name)) continue;
        const role = element.getAttribute('role') || '';
        const form = formInfo(element);
        const label = associatedText(element);
        const record = {
            label,
            associated_text: label,
            html_type: type,
            role: role || (element.isContentEditable ? 'textbox' : ''),
            name,
            id: element.id || '',
            class_name: element.getAttribute('class') || '',
            tag_name: tag,
            href: safeHref(element),
            aria_label: element.getAttribute('aria-label') || '',
            aria_labelledby: element.getAttribute('aria-labelledby') || '',
            aria_autocomplete: element.getAttribute('aria-autocomplete') || '',
            placeholder: element.getAttribute('placeholder') || '',
            data_attributes: dataAttributes(element),
            required: !!element.required || element.hasAttribute('required') ||
                element.getAttribute('aria-required') === 'true',
            disabled: !!element.disabled || element.getAttribute('aria-disabled') === 'true',
            visible: visible(element),
            hierarchy: hierarchy(element),
            form_id: form.id,
            form_title: form.title,
            form_action: form.action,
            form_method: form.method,
            onclick_present: element.hasAttribute('onclick'),
            onclick_handler: inlineHandlerName(element),
            option_count: tag === 'select' ? element.options.length : 0,
            options: tag === 'select' && mayReadOptionText(label)
                ? Array.from(element.options).map(option => option.innerText?.trim() || '') : []
        };
        const isButton = tag === 'button' ||
            (tag === 'input' && ['submit', 'button', 'reset', 'image'].includes(type)) || role === 'button';
        const tabIndex = element.getAttribute('tabindex');
        const potentiallyClickable = isButton || tag === 'a' || role === 'link' || role === 'menuitem' ||
            element.hasAttribute('onclick') || (tabIndex !== null && Number(tabIndex) >= 0);
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
    return {forms, fields, buttons, clickables, scope_found: true};
}"""


class BrowserAdapter(Protocol):
    """Interface injectable pour tests et évolution du navigateur."""

    def open(self, config: SessionConfig) -> None: ...

    def inspect(self, config: SessionConfig) -> PageEvidence: ...

    def probe_authenticated_route(self, config: SessionConfig) -> None: ...

    def go_home(self, config: SessionConfig) -> None: ...

    def navigate_to_enterprise_search(self, config: SessionConfig) -> None: ...

    def navigate_to_dashboard(self, config: SessionConfig) -> None: ...

    def diagnostics(self, config: SessionConfig, *, portlet_only: bool = False) -> PageDiagnostics: ...

    def diagnose_search_modes(self, config: SessionConfig) -> PageDiagnostics: ...

    def close(self) -> None: ...


class PlaywrightBrowser:
    """Navigateur Chromium visible, lancé dans un répertoire de profil SIDJILY dédié."""

    def __init__(self) -> None:
        self._playwright = None
        self._context = None
        self._page = None

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
        visible_text = self._page.locator("body").inner_text(timeout=5_000)
        has_password_field = self._page.locator('input[type="password"]:visible').count() > 0
        has_visible_form_controls = self._page.locator(
            'input:visible, select:visible, textarea:visible'
        ).count() > 0
        has_signout_control = self._page.locator(
            'a[href*="logout" i]:visible, a[href*="signout" i]:visible, '
            'button[aria-label*="déconnexion" i]:visible, a[aria-label*="déconnexion" i]:visible, '
            '[data-testid="user-menu"]:visible'
        ).count() > 0
        return collect_page_evidence(
            current_url=self._page.url,
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

    def diagnostics(self, config: SessionConfig, *, portlet_only: bool = False) -> PageDiagnostics:
        """Inspecte la page ou uniquement le portlet demandé, sans lire/modifier de valeur."""
        if self._page is None:
            raise RuntimeError("Navigateur non démarré.")
        current_url = self._page.url
        navigation_items: list[NavigationItem] = []
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
        for frame_index, frame in enumerate(list(self._page.frames)):
            fallback_name = "Document principal" if frame_index == 0 else f"Frame {frame_index}"
            frame_name = fallback_name
            frame_url = "URL indisponible"
            try:
                frame_name = sanitize_metadata_text(frame.name, 100) or fallback_name
                frame_url = sanitize_current_url(frame.url, config.url)
                snapshot = (
                    frame.evaluate(DOM_SNAPSHOT_SCRIPT, {"portletOnly": True})
                    if portlet_only else frame.evaluate(DOM_SNAPSHOT_SCRIPT)
                )
                if not isinstance(snapshot, dict):
                    raise TypeError("snapshot DOM indisponible")
                if portlet_only and snapshot.get("scope_found") is True:
                    portlet_scope_found = True
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
        )

    def _search_mode_link(self, label: str, config: SessionConfig) -> tuple[Any, Any]:
        if self._page is None:
            raise SearchModeAnalysisError("Navigateur non démarré.")
        pattern = re.compile(rf"^\s*{re.escape(label)}\s*$", re.IGNORECASE)
        matches: list[tuple[Any, Any]] = []
        for frame in list(self._page.frames):
            try:
                candidates = frame.get_by_role("link", name=pattern)
                count = candidates.count()
                for index in range(min(count, 3)):
                    candidate = candidates.nth(index)
                    href = candidate.get_attribute("href") or ""
                    if not href:
                        continue
                    destination = urljoin(frame.url, href)
                    parsed = urlsplit(destination)
                    tag_name = candidate.evaluate("element => element.tagName.toLowerCase()")
                    try:
                        allowed_port = parsed.port in (None, 443)
                    except ValueError:
                        allowed_port = False
                    if (
                        tag_name == "a"
                        and is_portal_host(destination, config.url)
                        and not parsed.username
                        and not parsed.password
                        and allowed_port
                        and not parsed.query
                        and not parsed.fragment
                        and parsed.path.rstrip("/") == DEFAULT_ENTERPRISE_SEARCH_ROUTE.rstrip("/")
                    ):
                        matches.append((frame, candidate))
            except Exception:
                continue
        if len(matches) != 1:
            raise SearchModeAnalysisError("Le lien exact du mode demandé est absent ou ambigu.")
        return matches[0]

    @staticmethod
    def _wait_for_search_portlet(frame: Any) -> None:
        try:
            stable = frame.evaluate(SEARCH_MODE_STABILITY_SCRIPT)
        except Exception:
            raise SearchModeAnalysisError("Le contenu du portlet n'a pas pu être stabilisé.") from None
        if not stable:
            raise SearchModeAnalysisError("Le contenu du portlet n'a pas été stabilisé dans le délai prévu.")

    def _capture_search_mode(self, label: str, config: SessionConfig) -> PageDiagnostics:
        frame, link = self._search_mode_link(label, config)
        try:
            link.click(timeout=min(config.navigation_timeout_ms, 8_000))
        except Exception:
            raise SearchModeAnalysisError("La sélection du mode demandé a échoué.") from None
        self._wait_for_search_portlet(frame)
        snapshot = self.diagnostics(config, portlet_only=True)
        if not snapshot.portlet_scope_found:
            raise SearchModeAnalysisError("Le portlet de recherche n'a pas été identifié dans le document.")
        return snapshot

    def diagnose_search_modes(self, config: SessionConfig) -> PageDiagnostics:
        """Sélectionne une fois chaque mode et compare ses métadonnées sans soumettre le formulaire."""
        if self._page is None:
            raise SearchModeAnalysisError("Navigateur non démarré.")
        current = urlsplit(self._page.url)
        if (
            not is_portal_host(self._page.url, config.url)
            or current.path.rstrip("/") != DEFAULT_ENTERPRISE_SEARCH_ROUTE.rstrip("/")
        ):
            raise SearchModeAnalysisError("Ouvrez la page Trouver une entreprise avant l'analyse des modes.")

        physical = self._capture_search_mode("PERSONNES PHYSIQUES", config)
        try:
            legal = self._capture_search_mode("PERSONNES MORALES", config)
        except SearchModeAnalysisError:
            # Les portlets peuvent remplacer la navigation de choix. Recharger la route connue
            # restaure le point de départ sans lire ni modifier l'état des champs.
            try:
                self._page.goto(
                    urljoin(config.url, DEFAULT_ENTERPRISE_SEARCH_ROUTE),
                    wait_until="domcontentloaded",
                    timeout=config.navigation_timeout_ms,
                )
                self._wait_for_search_portlet(self._page.main_frame)
                legal = self._capture_search_mode("PERSONNES MORALES", config)
            except Exception:
                raise SearchModeAnalysisError("Le second mode n'a pas pu être analysé.") from None

        comparison = format_search_mode_comparison(
            physical.form_diagnostics, legal.form_diagnostics
        )
        report = (
            "ANALYSE DES FORMULAIRES SIDJILCOM\n"
            "Sélection des deux modes seulement; aucun critère saisi et aucun bouton de recherche activé.\n\n"
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

    def close(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
        finally:
            self._context = None
            self._page = None
            if self._playwright is not None:
                self._playwright.stop()
            self._playwright = None
