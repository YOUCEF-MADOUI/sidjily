"""Signaux DOM explicites pour les états de session (isolés ici pour évolution)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit, urlunsplit

DEFAULT_ENTERPRISE_SEARCH_ROUTE = "/fr/group/sidjilcom/repertoire-des-commercants"
AMBULANT_SEARCH_ROUTE = "/fr/web/sidjilcom/rechercheambulant"
ACTIVITY_NOMENCLATURE_ROUTE = "/fr/web/sidjilcom/nomenclature-de-vos-activites"
DASHBOARD_ROUTE = "/group/sidjilcom/mon-tableau-de-bord"
SEARCH_MODE_PORTLET_MARKER = "dz_cnrc_sidjilcom_recherchedetaillee_portlet_RechercheDetailleePortlet"

NAVIGATION_LABEL_PATTERNS = {
    "Accueil": re.compile(r"^\s*accueil\s*$", re.IGNORECASE),
    "Tableau de bord": re.compile(r"tableau\s+de\s+bord|nos\s+abonn[eé]s", re.IGNORECASE),
    "Trouver une entreprise": re.compile(r"trouver\s+une\s+entreprise", re.IGNORECASE),
    "Recherche Commerçant Ambulant": re.compile(r"recherche\s+commerçant\s+ambulant", re.IGNORECASE),
    "Nomenclature de vos activités": re.compile(r"nomenclature\s+de\s+vos\s+activités", re.IGNORECASE),
}


@dataclass(frozen=True, slots=True)
class PageEvidence:
    """Signaux non sensibles résumés depuis la page; aucun texte/secret n'est conservé."""

    on_portal: bool
    has_login_form: bool
    has_authenticated_marker: bool
    has_expired_notice: bool
    on_enterprise_search_page: bool = False
    on_dashboard_page: bool = False


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
    current = urlsplit(current_url)
    portal = urlsplit(portal_url)
    current_host = (current.hostname or "").lower().rstrip(".")
    portal_host = (portal.hostname or "").lower().rstrip(".")
    return bool(current.scheme == "https" and portal_host and current_host == portal_host)


def collect_page_evidence(
    *,
    current_url: str,
    portal_url: str,
    visible_text: str,
    has_password_field: bool,
    has_signout_control: bool,
    has_visible_form_controls: bool = False,
) -> PageEvidence:
    """Traduit quelques indices sémantiques en booléens sans exposer le contenu de page."""
    text = _normalize(visible_text)
    path = unquote(urlsplit(current_url).path).casefold().rstrip("/")
    has_login_route = any(marker in path for marker in _LOGIN_ROUTE_MARKERS)
    on_search_route = path.endswith("/repertoire-des-commercants")
    has_search_content = any(
        marker in text
        for marker in ("raison sociale", "numero d'inscription", "nom commercial")
    )
    on_search_page = not has_login_route and (
        (on_search_route and has_visible_form_controls) or (has_search_content and has_visible_form_controls)
    )
    on_dashboard_page = (
        not has_login_route
        and path.endswith("/mon-tableau-de-bord")
        and any(marker in text for marker in ("tableau de bord", "nos abonnes"))
    )
    generic_french_expiry = re.search(
        r"\bsession(?:\s+[\w-]+){0,3}\s+(?:a expire|expiree)\b", text
    ) is not None
    return PageEvidence(
        on_portal=is_portal_host(current_url, portal_url),
        has_login_form=has_password_field or has_login_route,
        has_authenticated_marker=has_signout_control or any(phrase in text for phrase in _AUTHENTICATED_PHRASES),
        has_expired_notice=generic_french_expiry or any(phrase in text for phrase in _EXPIRED_PHRASES),
        on_enterprise_search_page=on_search_page,
        on_dashboard_page=on_dashboard_page,
    )


def identify_section(current_url: str) -> str:
    """Identifie uniquement les routes Sidjilcom connues; ne devine pas le reste."""
    parsed = urlsplit(current_url)
    if parsed.hostname != "sidjilcom.cnrc.dz" or parsed.scheme != "https":
        return "Page externe"
    path = unquote(parsed.path).rstrip("/").casefold()
    if path in ("", "/"):
        return "Accueil"
    routes = (
        ("/repertoire-des-commercants", "Trouver une entreprise"),
        ("/rechercheambulant", "Recherche Commerçant Ambulant"),
        ("/nomenclature-de-vos-activites", "Nomenclature de vos activités"),
        ("/mon-tableau-de-bord", "Tableau de bord"),
    )
    for route, label in routes:
        if path.endswith(route):
            return label
    if any(marker in path for marker in _LOGIN_ROUTE_MARKERS):
        return "Authentification"
    return "Page Sidjilcom non identifiée"


def sanitize_current_url(current_url: str, portal_url: str) -> str:
    """Retire query/fragment et masque le chemin si le portail redirige vers un tiers."""
    parsed = urlsplit(current_url)
    host = parsed.hostname or ""
    if not host:
        return "URL indisponible"
    netloc = host
    if parsed.port and parsed.port not in (80, 443):
        netloc = f"{host}:{parsed.port}"
    path = unquote(parsed.path) if is_portal_host(current_url, portal_url) else "/[chemin masqué]"
    return urlunsplit((parsed.scheme, netloc, path or "/", "", ""))
