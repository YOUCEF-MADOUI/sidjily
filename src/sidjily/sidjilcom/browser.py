"""Adaptateur Playwright conservant le profil isolé propre à SIDJILY."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin

from sidjily.sidjilcom.config import SessionConfig
from sidjily.sidjilcom.diagnostics import (
    FormDiagnosticBundle,
    build_form_diagnostics,
    format_diagnostic,
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


DOM_SNAPSHOT_SCRIPT = r"""() => {
    const sensitiveName = /password|passwd|token|secret|csrf|cookie|session|auth/i;
    const allowedOptionLabels = ['type de personne', 'personne physique', 'personne morale',
        'wilaya', 'commune', 'secteur', 'activite', 'forme juridique', 'conformite',
        'etat commercant', 'nationalite', 'qualite'];
    const sensitiveOptionLabels = ['nom', 'prenom', 'email', 'e-mail', 'telephone', 'date',
        'numero', 'inscription', 'raison sociale', 'commercial', 'dirigeant', 'adresse', 'nif', 'nis'];
    const visible = element => {
        const style = window.getComputedStyle(element);
        return !!(element.getClientRects().length && style.visibility !== 'hidden' &&
            style.display !== 'none' && style.opacity !== '0' &&
            !element.closest('[hidden], [aria-hidden="true"]'));
    };
    const dataAttributes = element => {
        const result = {};
        for (const attribute of Array.from(element.attributes)) {
            if (attribute.name.startsWith('data-') && !sensitiveName.test(attribute.name) &&
                attribute.name.length <= 64) {
                result[attribute.name] = element.getAttribute(attribute.name);
            }
        }
        return result;
    };
    const safeHref = element => {
        const rawHref = element.getAttribute('href') || '';
        const pathOnly = rawHref.split(/[?#]/, 1)[0];
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
            .map(label => label.innerText?.trim() || '').filter(Boolean);
        if (!labels.length && element.id) {
            for (const label of document.querySelectorAll('label[for]')) {
                if (label.getAttribute('for') === element.id && label.innerText?.trim()) {
                    labels.push(label.innerText.trim());
                }
            }
        }
        const labelledBy = (element.getAttribute('aria-labelledby') || '').split(/\s+/)
            .filter(Boolean)
            .map(id => document.getElementById(id)?.innerText?.trim() || '').filter(Boolean);
        return labels.join(' ') || labelledBy.join(' ') ||
            element.getAttribute('aria-label') || element.getAttribute('placeholder') || '';
    };
    const formInfo = element => {
        const form = element.closest('form') || element.form || null;
        if (!form) return {id: 'hors-formulaire', title: 'Hors formulaire'};
        const id = form.id || `formulaire-${Array.from(document.forms).indexOf(form) + 1}`;
        return {
            id,
            title: form.getAttribute('aria-label') || form.getAttribute('title') ||
                form.querySelector('legend')?.innerText?.trim() || `Formulaire ${id}`
        };
    };
    const mayReadOptionText = label => {
        const normalized = label.normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
        const location = normalized.includes('wilaya') || normalized.includes('commune');
        if (sensitiveOptionLabels.some(word => normalized.includes(word)) && !location) return false;
        return allowedOptionLabels.some(word => normalized.includes(word));
    };
    const forms = Array.from(document.querySelectorAll('form')).map((form, index) => ({
        form_id: form.id || `formulaire-${index + 1}`,
        form_title: form.getAttribute('aria-label') || form.getAttribute('title') ||
            form.querySelector('legend')?.innerText?.trim() || `Formulaire ${index + 1}`
    }));
    const fields = [];
    const buttons = [];
    const clickables = [];
    const selector = [
        'input', 'select', 'textarea', 'button', '[role="textbox"]', '[role="combobox"]',
        '[role="searchbox"]', '[role="button"]', '[role="link"]', '[role="menuitem"]',
        '[contenteditable="true"]', 'a[href]', '[onclick]', '[tabindex]'
    ].join(',');
    for (const element of document.querySelectorAll(selector)) {
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
            placeholder: element.getAttribute('placeholder') || '',
            data_attributes: dataAttributes(element),
            disabled: !!element.disabled || element.getAttribute('aria-disabled') === 'true',
            visible: visible(element),
            hierarchy: hierarchy(element),
            form_id: form.id,
            form_title: form.title,
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
    return {forms, fields, buttons, clickables};
}"""


class BrowserAdapter(Protocol):
    """Interface injectable pour tests et évolution du navigateur."""

    def open(self, config: SessionConfig) -> None: ...

    def inspect(self, config: SessionConfig) -> PageEvidence: ...

    def go_home(self, config: SessionConfig) -> None: ...

    def navigate_to_enterprise_search(self, config: SessionConfig) -> None: ...

    def navigate_to_dashboard(self, config: SessionConfig) -> None: ...

    def diagnostics(self, config: SessionConfig) -> PageDiagnostics: ...

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

    def diagnostics(self, config: SessionConfig) -> PageDiagnostics:
        """Inspecte la page et ses frames, sans lire/modifier aucune valeur de formulaire."""
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
        for frame_index, frame in enumerate(list(self._page.frames)):
            fallback_name = "Document principal" if frame_index == 0 else f"Frame {frame_index}"
            frame_name = fallback_name
            frame_url = "URL indisponible"
            try:
                frame_name = sanitize_metadata_text(frame.name, 100) or fallback_name
                frame_url = sanitize_current_url(frame.url, config.url)
                snapshot = frame.evaluate(DOM_SNAPSHOT_SCRIPT)
                if not isinstance(snapshot, dict):
                    raise TypeError("snapshot DOM indisponible")
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
        )
        return PageDiagnostics(
            url=page.url,
            title=page.title,
            section=page.section,
            navigation_items=page.navigation_items,
            visible_fields=page.visible_fields,
            form_diagnostics=bundle,
            report=format_diagnostic(page, bundle),
        )

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
