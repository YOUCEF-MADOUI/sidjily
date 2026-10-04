"""Point d'entrée de l'application de bureau."""

from __future__ import annotations

import logging

from sidjily.database import Database
from sidjily.logging_config import configure_logging
from sidjily.paths import user_data_dir
from sidjily.sidjilcom.config import SessionConfig
from sidjily.sidjilcom.session import SidjilcomSessionManager
from sidjily.task_manager import TaskManager


def main() -> None:
    data_dir = user_data_dir()
    logger = configure_logging(data_dir / "logs")
    try:
        manager = TaskManager(Database(data_dir / "sidjily.sqlite3"))
        recovered = manager.recover_interrupted()
        if recovered:
            logger.warning("%s tâche(s) interrompue(s) restaurée(s) pour reprise.", recovered)
        try:
            import tkinter as tk
            from sidjily.ui.app import SidjilyApp
        except ModuleNotFoundError as exc:
            if exc.name != "tkinter":
                raise
            raise RuntimeError(
                "Tkinter est requis pour l'interface. Sous Windows, installez Python avec le composant Tcl/Tk."
            ) from exc
        session_manager = SidjilcomSessionManager(SessionConfig.from_environment())
        root = tk.Tk()
        SidjilyApp(root, manager, session_manager)
        root.mainloop()
    except Exception:
        logger.exception("Erreur fatale de l'application.")
        raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
