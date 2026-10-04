"""Adaptateur Playwright conservant le profil isolé propre à SIDJILY."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin

from sidjily.sidjilcom.config import SessionConfig
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
        """Retourne uniquement des métadonnées de navigation et libellés, jamais des valeurs."""
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
        fields = self._page.locator("input:visible, select:visible, textarea:visible").evaluate_all(
            """elements => elements
                .filter(element => (element.getAttribute('type') || '').toLowerCase() !== 'password')
                .map(element => {
                    const labels = Array.from(element.labels || [])
                        .map(label => label.innerText.trim()).filter(Boolean);
                    const label = element.getAttribute('aria-label') || labels[0] ||
                        element.getAttribute('placeholder') || 'Champ sans libellé';
                    const type = element.tagName.toLowerCase() === 'select' ? 'select' :
                        (element.getAttribute('type') || element.tagName.toLowerCase());
                    return `${label.slice(0, 100)} [${type}]`;
                })"""
        )
        unique_fields = tuple(dict.fromkeys(str(field) for field in fields if field))[:80]
        title = re.sub(r"\s+", " ", self._page.title()).strip()[:160]
        return PageDiagnostics(
            url=sanitize_current_url(current_url, config.url),
            title=title,
            section=identify_section(current_url),
            navigation_items=tuple(navigation_items),
            visible_fields=unique_fields,
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
