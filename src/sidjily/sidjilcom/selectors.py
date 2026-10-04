"""Signaux DOM explicites pour les états de session (isolés ici pour évolution)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class PageEvidence:
    """Signaux non sensibles résumés depuis la page; aucun texte/secret n'est conservé."""

    on_portal: bool
    has_login_form: bool
    has_authenticated_marker: bool
    has_expired_notice: bool


_LOGIN_ROUTE_MARKERS = ("/login", "/signin", "/sign-in", "/connexion", "/auth")

_EXPIRED_PHRASES = (
    "session expiree",
    "votre session a expire",
    "session has expired",
    "session expired",
    "please log in again",
    "please login again",
    "veuillez vous reconnecter",
    "vous devez vous reconnecter",
    "انتهت الجلسة",
    "يرجى تسجيل الدخول مرة أخرى",
    "الرجاء تسجيل الدخول مجددا",
)
_AUTHENTICATED_PHRASES = (
    "deconnexion",
    "se deconnecter",
    "log out",
    "logout",
    "sign out",
    "تسجيل الخروج",
)


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", without_accents)


def is_portal_host(current_url: str, portal_url: str) -> bool:
    """N'accepte que l'hôte officiel configuré; les redirections tierces restent en attente."""
    current_host = (urlsplit(current_url).hostname or "").lower().rstrip(".")
    portal_host = (urlsplit(portal_url).hostname or "").lower().rstrip(".")
    return bool(portal_host and current_host == portal_host)


def collect_page_evidence(
    *,
    current_url: str,
    portal_url: str,
    visible_text: str,
    has_password_field: bool,
    has_signout_control: bool,
) -> PageEvidence:
    """Traduit quelques indices sémantiques en booléens sans exposer le contenu de page."""
    text = _normalize(visible_text)
    path = urlsplit(current_url).path.casefold()
    has_login_route = any(marker in path for marker in _LOGIN_ROUTE_MARKERS)
    generic_french_expiry = re.search(
        r"\bsession(?:\s+[\w-]+){0,3}\s+(?:a expire|expiree)\b", text
    ) is not None
    return PageEvidence(
        on_portal=is_portal_host(current_url, portal_url),
        has_login_form=has_password_field or has_login_route,
        has_authenticated_marker=has_signout_control or any(phrase in text for phrase in _AUTHENTICATED_PHRASES),
        has_expired_notice=generic_french_expiry or any(phrase in text for phrase in _EXPIRED_PHRASES),
    )
