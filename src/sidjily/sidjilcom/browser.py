"""Adaptateur Playwright conservant le profil isolé propre à SIDJILY."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from sidjily.sidjilcom.config import SessionConfig
from sidjily.sidjilcom.selectors import PageEvidence, collect_page_evidence


class BrowserAdapter(Protocol):
    """Interface injectable pour tests et évolution du navigateur."""

    def open(self, config: SessionConfig) -> None: ...

    def inspect(self, config: SessionConfig) -> PageEvidence: ...

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
