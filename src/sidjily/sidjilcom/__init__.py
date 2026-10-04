"""Intégration de session manuelle et isolée pour Sidjilcom."""

from sidjily.sidjilcom.browser import NavigationItem, PageDiagnostics
from sidjily.sidjilcom.config import SessionConfig
from sidjily.sidjilcom.session import (
    SessionDiagnostics,
    SessionOperationError,
    SessionSnapshot,
    SessionState,
    SidjilcomSessionManager,
)

__all__ = [
    "NavigationItem",
    "PageDiagnostics",
    "SessionConfig",
    "SessionDiagnostics",
    "SessionOperationError",
    "SessionSnapshot",
    "SessionState",
    "SidjilcomSessionManager",
]
