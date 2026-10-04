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
        """Retourne un rapport DOM structurel sans lire les valeurs des contrôles."""
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

        raw = self._page.locator("input, select, textarea, button").evaluate_all(
            """elements => {
                const visible = element => {
                    const style = window.getComputedStyle(element);
                    return !!(element.getClientRects().length && style.visibility !== 'hidden' &&
                        style.display !== 'none' && !element.closest('[hidden]'));
                };
                const dataAttributes = element => {
                    const allowed = ['data-testid', 'data-test', 'data-qa', 'data-cy',
                        'data-field', 'data-field-name', 'data-role'];
                    const result = {};
                    for (const name of allowed) {
                        const value = element.getAttribute(name);
                        if (value) result[name] = value;
                    }
                    return result;
                };
                const hierarchy = element => {
                    const sets = [];
                    let set = element.closest('fieldset');
                    while (set) {
                        sets.unshift(set);
                        set = set.parentElement?.closest('fieldset') || null;
                    }
                    return sets.map(item => item.querySelector(':scope > legend')?.innerText?.trim() || '')
                        .filter(Boolean).slice(0, 8);
                };
                const formInfo = element => {
                    const form = element.closest('form');
                    return {
                        id: form?.id || (form ? `formulaire-${Array.from(document.forms).indexOf(form) + 1}` : 'hors-formulaire'),
                        title: form?.getAttribute('aria-label') || form?.getAttribute('title') ||
                            form?.querySelector('legend')?.innerText?.trim() || 'Formulaire'
                    };
                };
                const allowedOptionLabels = ['type de personne', 'personne physique', 'personne morale',
                    'wilaya', 'commune', 'secteur', 'activite', 'forme juridique', 'conformite',
                    'etat commercant', 'nationalite', 'qualite'];
                const sensitiveOptionLabels = ['nom', 'prenom', 'email', 'e-mail', 'telephone', 'date',
                    'numero', 'inscription', 'raison sociale', 'commercial', 'dirigeant', 'adresse', 'nif', 'nis'];
                const mayReadOptionText = label => {
                    const normalized = label.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase();
                    const location = normalized.includes('wilaya') || normalized.includes('commune');
                    if (sensitiveOptionLabels.some(word => normalized.includes(word)) && !location) return false;
                    return allowedOptionLabels.some(word => normalized.includes(word));
                };
                const controls = [];
                const buttons = [];
                for (const element of elements) {
                    if (!visible(element)) continue;
                    const tag = element.tagName.toLowerCase();
                    const type = (element.getAttribute('type') || (tag === 'input' ? 'text' : tag)).toLowerCase();
                    if (tag === 'button' || (tag === 'input' && ['submit', 'button', 'reset'].includes(type))) {
                        buttons.push({
                            text: element.getAttribute('aria-label') || element.innerText?.trim() ||
                                element.getAttribute('title') || '',
                            html_type: type === 'button' && tag === 'button' ?
                                (element.getAttribute('type') || 'submit') : type,
                            role: element.getAttribute('role') || 'button',
                            name: element.getAttribute('name') || '',
                            id: element.id || '',
                            data_attributes: dataAttributes(element),
                            disabled: !!element.disabled,
                            form_title: formInfo(element).title,
                            hierarchy: hierarchy(element)
                        });
                        continue;
                    }
                    if (type === 'password' || type === 'hidden' || element.matches('[type="hidden"]')) continue;
                    const labels = Array.from(element.labels || [])
                        .map(label => label.innerText?.trim() || '').filter(Boolean);
                    const labelledBy = (element.getAttribute('aria-labelledby') || '').split(/\s+/)
                        .map(id => document.getElementById(id)?.innerText?.trim() || '').filter(Boolean);
                    const label = element.getAttribute('aria-label') || labels.join(' ') ||
                        labelledBy.join(' ') || element.getAttribute('placeholder') || '';
                    const form = formInfo(element);
                    const options = tag === 'select' && mayReadOptionText(label) ?
                        Array.from(element.options).slice(0, 100)
                            .map(option => option.innerText?.trim() || '') : [];
                    controls.push({
                        label,
                        associated_text: label,
                        html_type: type,
                        role: element.getAttribute('role') || '',
                        name: element.getAttribute('name') || '',
                        id: element.id || '',
                        placeholder: element.getAttribute('placeholder') || '',
                        data_attributes: dataAttributes(element),
                        disabled: !!element.disabled,
                        option_count: tag === 'select' ? element.options.length : 0,
                        options,
                        form_id: form.id,
                        form_title: form.title,
                        hierarchy: hierarchy(element)
                    });
                }
                return {fields: controls, buttons};
            }"""
        )
        bundle = build_form_diagnostics(raw)
        title = sanitize_metadata_text(self._page.title(), 160)
        page = PageDiagnostics(
            url=sanitize_current_url(current_url, config.url),
            title=title,
            section=identify_section(current_url),
            navigation_items=tuple(navigation_items),
            visible_fields=tuple(
                dict.fromkeys(f"{field.label} [{field.html_type}]" for form in bundle.forms for field in form.fields)
            )[:80],
            form_diagnostics=bundle,
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
