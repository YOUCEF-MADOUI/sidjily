"""Cycle de vie thread-safe de la session manuelle Sidjilcom."""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from sidjily.sidjilcom.browser import BrowserAdapter, PlaywrightBrowser
from sidjily.sidjilcom.config import SessionConfig
from sidjily.sidjilcom.selectors import PageEvidence


class SessionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    WAITING_FOR_LOGIN = "waiting_for_login"
    CONNECTED = "connected"
    SESSION_EXPIRED = "session_expired"
    ERROR = "error"
    DISCONNECTING = "disconnecting"


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    state: SessionState
    message: str


def classify_session(evidence: PageEvidence, *, previously_connected: bool) -> SessionState:
    """Retourne un état conservateur : aucun marqueur positif, aucun faux « connecté »."""
    if evidence.has_expired_notice or (previously_connected and evidence.has_login_form):
        return SessionState.SESSION_EXPIRED
    if evidence.on_portal and evidence.has_authenticated_marker and not evidence.has_login_form:
        return SessionState.CONNECTED
    return SessionState.WAITING_FOR_LOGIN


class SidjilcomSessionManager:
    """Ouvre un navigateur isolé et laisse l'utilisateur effectuer lui-même la connexion.

    Toute interaction Playwright est confinée à un thread dédié. Le gestionnaire ne lit
    jamais les champs de formulaire, cookies, stockage local ou mots de passe.
    """

    _MESSAGES = {
        SessionState.DISCONNECTED: "Aucune session Sidjilcom ouverte.",
        SessionState.CONNECTING: "Ouverture du navigateur SIDJILY…",
        SessionState.WAITING_FOR_LOGIN: "Connectez-vous manuellement dans la fenêtre Sidjilcom.",
        SessionState.CONNECTED: "Session Sidjilcom active.",
        SessionState.SESSION_EXPIRED: "Votre session Sidjilcom a expiré. Veuillez vous reconnecter.",
        SessionState.ERROR: "Impossible d'utiliser le navigateur Sidjilcom. Vérifiez Playwright, Chromium et votre connexion.",
        SessionState.DISCONNECTING: "Fermeture du navigateur SIDJILY…",
    }

    def __init__(
        self,
        config: SessionConfig | None = None,
        *,
        browser_factory: Callable[[], BrowserAdapter] = PlaywrightBrowser,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config or SessionConfig()
        self._browser_factory = browser_factory
        self._logger = logger or logging.getLogger("sidjily.session")
        self._lock = threading.RLock()
        self._commands: queue.Queue[str] = queue.Queue()
        self._disconnect_requested = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot = SessionSnapshot(SessionState.DISCONNECTED, self._MESSAGES[SessionState.DISCONNECTED])
        self._previously_connected = self.config.connected_marker_path.is_file()

    @property
    def snapshot(self) -> SessionSnapshot:
        with self._lock:
            return self._snapshot

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def connect(self) -> None:
        """Ouvre le profil dédié; une session déjà ouverte n'est jamais doublée."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                if self._snapshot.state == SessionState.SESSION_EXPIRED:
                    self._commands.put("verify")
                return
            while True:
                try:
                    self._commands.get_nowait()
                except queue.Empty:
                    break
            self._disconnect_requested.clear()
            self._previously_connected = self.config.connected_marker_path.is_file()
            self._set_state_locked(SessionState.CONNECTING)
            self._thread = threading.Thread(
                target=self._run_browser,
                name="sidjily-sidjilcom-session",
                daemon=True,
            )
            self._thread.start()

    def verify_session(self) -> None:
        """Demande un contrôle immédiat, sans démarrer de navigateur caché."""
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                return
            self._commands.put("verify")

    def disconnect(self) -> None:
        """Ferme le contexte Chromium en conservant sur disque le profil SIDJILY."""
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._set_state_locked(SessionState.DISCONNECTED)
                return
            self._disconnect_requested.set()
            self._set_state_locked(SessionState.DISCONNECTING)
            self._commands.put("disconnect")

    def close(self, timeout: float = 8.0) -> bool:
        """Demande une fermeture propre et attend au plus `timeout` secondes."""
        self.disconnect()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(timeout, 0.0))
        return not self.is_running

    def _run_browser(self) -> None:
        browser: BrowserAdapter | None = None
        failure: Exception | None = None
        try:
            browser = self._browser_factory()
            browser.open(self.config)
            self._set_state(SessionState.WAITING_FOR_LOGIN)
            while not self._disconnect_requested.is_set():
                try:
                    command = self._commands.get(timeout=self.config.poll_interval_seconds)
                except queue.Empty:
                    command = "poll"
                if command == "disconnect" or self._disconnect_requested.is_set():
                    break
                if command in ("poll", "verify"):
                    self._check_page(browser)
        except Exception as exc:  # détails volontairement exclus des logs et de l'interface
            failure = exc
            if not self._disconnect_requested.is_set():
                if isinstance(exc, ModuleNotFoundError) and exc.name == "playwright":
                    self._set_state(SessionState.ERROR, "Playwright n'est pas installé. Réinstallez SIDJILY avec ses dépendances.")
                else:
                    self._logger.error("Échec de session navigateur (type=%s).", type(exc).__name__)
                    self._set_state(SessionState.ERROR)
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception as exc:
                    self._logger.error("Échec de fermeture du navigateur (type=%s).", type(exc).__name__)
                    if failure is None and not self._disconnect_requested.is_set():
                        self._set_state(SessionState.ERROR)
            with self._lock:
                if self._disconnect_requested.is_set():
                    self._previously_connected = False
                    self._set_state_locked(SessionState.DISCONNECTED)
                if self._thread is threading.current_thread():
                    self._thread = None

    def _check_page(self, browser: BrowserAdapter) -> None:
        evidence = browser.inspect(self.config)
        with self._lock:
            was_connected = self._previously_connected
        state = classify_session(evidence, previously_connected=was_connected)
        newly_connected = False
        with self._lock:
            if state == SessionState.CONNECTED:
                newly_connected = not self._previously_connected
                self._previously_connected = True
        if newly_connected:
            self._persist_connected_marker()
        with self._lock:
            self._set_state_locked(state)

    def _persist_connected_marker(self) -> None:
        marker = self.config.connected_marker_path
        try:
            marker.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            marker.touch(mode=0o600, exist_ok=True)
        except OSError as exc:
            self._logger.warning("Indicateur de session non persistant (type=%s).", type(exc).__name__)

    def _set_state(self, state: SessionState, message: str | None = None) -> None:
        with self._lock:
            self._set_state_locked(state, message)

    def _set_state_locked(self, state: SessionState, message: str | None = None) -> None:
        old_state = self._snapshot.state
        self._snapshot = SessionSnapshot(state, message or self._MESSAGES[state])
        if state != old_state:
            # Ne jamais journaliser l'URL, le texte de page, le profil ou les données de compte.
            self._logger.info("État de session Sidjilcom : %s", state.value)
