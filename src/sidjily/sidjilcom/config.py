"""Configuration sûre du navigateur Sidjilcom."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from sidjily.paths import browser_profile_dir

DEFAULT_SIDJILCOM_URL = "https://sidjilcom.cnrc.dz/"


def _is_inside_git_worktree(path: Path) -> bool:
    candidate = path.resolve()
    for parent in (candidate, *candidate.parents):
        if (parent / ".git").exists():
            return True
    return False


def _overlaps_personal_chrome_profile(path: Path) -> bool:
    candidate = path.resolve()
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    known_profiles = [
        config_home / "google-chrome",
        config_home / "chromium",
        Path.home() / "Library" / "Application Support" / "Google" / "Chrome",
    ]
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        known_profiles.append(Path(local_app_data) / "Google" / "Chrome" / "User Data")
    for known in known_profiles:
        known = known.resolve()
        if candidate == known or candidate in known.parents or known in candidate.parents:
            return True
    return False


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Valeur booléenne invalide pour {name}.")


@dataclass(frozen=True, slots=True)
class SessionConfig:
    """Options de session. Le profil est propre à SIDJILY et reste sur la machine."""

    url: str = DEFAULT_SIDJILCOM_URL
    profile_path: Path | None = None
    headless: bool = False
    poll_interval_seconds: float = 1.5
    navigation_timeout_ms: int = 45_000

    def __post_init__(self) -> None:
        parsed = urlsplit(self.url)
        try:
            allowed_port = parsed.port in (None, 443)
        except ValueError:
            allowed_port = False
        if (
            parsed.scheme != "https"
            or parsed.hostname != "sidjilcom.cnrc.dz"
            or not allowed_port
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("L'URL doit être le portail HTTPS officiel Sidjilcom, sans identifiants ni paramètres.")
        if self.poll_interval_seconds <= 0:
            raise ValueError("L'intervalle de vérification doit être positif.")
        if self.navigation_timeout_ms <= 0:
            raise ValueError("Le délai de navigation doit être positif.")
        profile = Path(self.profile_path).expanduser() if self.profile_path is not None else browser_profile_dir()
        if _is_inside_git_worktree(profile):
            raise ValueError("Le profil navigateur doit être placé hors du dépôt Git.")
        if _overlaps_personal_chrome_profile(profile):
            raise ValueError("Le profil SIDJILY ne peut pas réutiliser le profil personnel Chrome/Chromium.")
        if self.profile_path is not None:
            object.__setattr__(self, "profile_path", profile)

    @property
    def resolved_profile_path(self) -> Path:
        path = self.profile_path or browser_profile_dir()
        return path.expanduser().resolve()

    @property
    def connected_marker_path(self) -> Path:
        """Indicateur local non secret pour reconnaître une nouvelle authentification au redémarrage."""
        return self.resolved_profile_path / ".sidjily-session-established"

    @classmethod
    def from_environment(cls) -> "SessionConfig":
        """Charge les options non secrètes, sans accepter de credentials dans l'URL."""
        profile = os.environ.get("SIDJILY_BROWSER_PROFILE")
        return cls(
            url=os.environ.get("SIDJILY_SIDJILCOM_URL", DEFAULT_SIDJILCOM_URL),
            profile_path=Path(profile).expanduser() if profile else None,
            headless=_env_bool("SIDJILY_BROWSER_HEADLESS", False),
        )
