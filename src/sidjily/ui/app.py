"""Interface Tkinter initiale : création et suivi local des recherches."""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any

from sidjily.models import Search, Status
from sidjily.sidjilcom.session import SessionOperationError, SessionState, SidjilcomSessionManager
from sidjily.task_manager import InvalidTransition, TaskManager


STATUS_LABELS = {
    Status.PENDING: "En attente",
    Status.RUNNING: "En cours",
    Status.COMPLETED: "Terminée",
    Status.FAILED: "Échec",
    Status.RETRY: "À réessayer",
    Status.SUSPENDED: "Suspendue",
    Status.CANCELLED: "Annulée",
}

SESSION_LABELS = {
    SessionState.DISCONNECTED: ("🔴 Non connecté", "#b42318"),
    SessionState.CONNECTING: ("🟡 Connexion en cours", "#946200"),
    SessionState.WAITING_FOR_LOGIN: ("🟡 Connexion en attente", "#946200"),
    SessionState.CONNECTED: ("🟢 Connecté", "#027a48"),
    SessionState.SESSION_EXPIRED: ("🔴 Session expirée", "#b42318"),
    SessionState.ERROR: ("🔴 Erreur", "#b42318"),
    SessionState.DISCONNECTING: ("🟡 Fermeture en cours", "#946200"),
}


class SidjilyApp:
    def __init__(
        self,
        root: tk.Tk,
        manager: TaskManager,
        session_manager: SidjilcomSessionManager | None = None,
    ):
        self.root = root
        self.manager = manager
        self.session_manager = session_manager or SidjilcomSessionManager()
        self._pending_session_operation: Any = None
        self._closing = False
        self.root.title("SIDJILY — Gestion des recherches")
        self.root.geometry("940x600")
        self.root.minsize(760, 460)
        self.selected_search_id: str | None = None
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()
        self.refresh()
        self._poll_session()

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=18)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="SIDJILY", font=("Segoe UI", 22, "bold")).pack(anchor="w")
        ttk.Label(
            container,
            text="Session Sidjilcom manuelle disponible — recherche et collecte prévues dans les prochaines étapes.",
            wraplength=880,
        ).pack(anchor="w", pady=(2, 12))

        session_panel = ttk.LabelFrame(container, text="Connexion Sidjilcom", padding=10)
        session_panel.pack(fill="x", pady=(0, 12))
        session_info = ttk.Frame(session_panel)
        session_info.pack(side="left", fill="x", expand=True)
        self.session_indicator = ttk.Label(session_info, text="🔴 Non connecté", font=("Segoe UI", 10, "bold"))
        self.session_indicator.pack(anchor="w")
        self.session_message = ttk.Label(session_info, text="Aucune session Sidjilcom ouverte.", wraplength=520)
        self.session_message.pack(anchor="w", pady=(3, 0))
        session_buttons = ttk.Frame(session_panel)
        session_buttons.pack(side="right", padx=(10, 0))
        self.connect_button = ttk.Button(
            session_buttons, text="Ouvrir Sidjilcom", command=self._connect_sidjilcom
        )
        self.connect_button.pack(side="left")
        self.verify_button = ttk.Button(
            session_buttons, text="Vérifier la session", command=self._verify_sidjilcom
        )
        self.verify_button.pack(side="left", padx=6)
        self.disconnect_button = ttk.Button(
            session_buttons, text="Déconnecter", command=self._disconnect_sidjilcom
        )
        self.disconnect_button.pack(side="left")

        navigation_panel = ttk.LabelFrame(container, text="Navigation et diagnostic (sans recherche)", padding=8)
        navigation_panel.pack(fill="x", pady=(0, 12))
        navigation_buttons = ttk.Frame(navigation_panel)
        navigation_buttons.pack(fill="x")
        self.home_button = ttk.Button(
            navigation_buttons, text="Accueil Sidjilcom", command=self._go_home
        )
        self.home_button.pack(side="left")
        self.dashboard_button = ttk.Button(
            navigation_buttons, text="Tableau de bord", command=self._open_dashboard
        )
        self.dashboard_button.pack(side="left", padx=8)
        self.enterprise_search_button = ttk.Button(
            navigation_buttons, text="Trouver une entreprise", command=self._open_enterprise_search
        )
        self.enterprise_search_button.pack(side="left", padx=8)
        self.diagnostics_button = ttk.Button(
            navigation_buttons, text="Diagnostiquer la page", command=self._diagnose_page
        )
        self.diagnostics_button.pack(side="left")
        self.search_modes_button = ttk.Button(
            navigation_buttons,
            text="Analyser personnes physiques / morales",
            command=self._diagnose_search_modes,
        )
        self.search_modes_button.pack(side="left", padx=8)
        self.diagnostic_output = ttk.Label(
            navigation_panel,
            text="Aucun diagnostic. Aucune valeur de champ, cookie ou jeton n'est collecté; aucune recherche n'est lancée.",
            wraplength=880,
            justify="left",
        )
        self.diagnostic_output.pack(anchor="w", pady=(6, 0))
        report_toolbar = ttk.Frame(navigation_panel)
        report_toolbar.pack(fill="x", pady=(6, 4))
        self.copy_diagnostic_button = ttk.Button(
            report_toolbar, text="Copier le diagnostic", command=self._copy_diagnostic, state="disabled"
        )
        self.copy_diagnostic_button.pack(side="left")
        self.save_diagnostic_button = ttk.Button(
            report_toolbar, text="Enregistrer le diagnostic", command=self._save_diagnostic, state="disabled"
        )
        self.save_diagnostic_button.pack(side="left", padx=8)
        report_frame = ttk.Frame(navigation_panel)
        report_frame.pack(fill="x")
        self.diagnostic_text = tk.Text(report_frame, height=6, wrap="word", state="disabled", font=("Consolas", 9))
        self.diagnostic_text.pack(side="left", fill="x", expand=True)
        report_scrollbar = ttk.Scrollbar(report_frame, orient="vertical", command=self.diagnostic_text.yview)
        report_scrollbar.pack(side="right", fill="y")
        self.diagnostic_text.configure(yscrollcommand=report_scrollbar.set)
        self._diagnostic_report = ""

        toolbar = ttk.Frame(container)
        toolbar.pack(fill="x", pady=(0, 10))
        ttk.Button(toolbar, text="Nouvelle recherche", command=self.new_search).pack(side="left")
        ttk.Button(toolbar, text="Continuer la recherche", command=self.resume_selected).pack(side="left", padx=8)
        ttk.Button(toolbar, text="Suspendre", command=self.pause_selected).pack(side="left")
        ttk.Button(toolbar, text="Actualiser", command=self.refresh).pack(side="right")

        columns = ("status", "progress", "created", "criteria")
        self.table = ttk.Treeview(container, columns=columns, show="tree headings", selectmode="browse")
        self.table.heading("#0", text="Recherche")
        self.table.heading("status", text="État")
        self.table.heading("progress", text="Tâches terminées")
        self.table.heading("created", text="Créée le (UTC)")
        self.table.heading("criteria", text="Critères")
        self.table.column("#0", width=210, minwidth=150)
        self.table.column("status", width=110, anchor="center")
        self.table.column("progress", width=125, anchor="center")
        self.table.column("created", width=150)
        self.table.column("criteria", width=300)
        self.table.pack(fill="both", expand=True)
        self.table.bind("<<TreeviewSelect>>", self._on_select)

        self.details = ttk.Label(container, text="Sélectionnez une recherche pour afficher son journal.", wraplength=890)
        self.details.pack(anchor="w", pady=(12, 0))
        self.footer = ttk.Label(container, text="Données enregistrées localement dans SQLite.", foreground="#555555")
        self.footer.pack(anchor="w", pady=(8, 0))

    def refresh(self) -> None:
        for item in self.table.get_children():
            self.table.delete(item)
        searches = self.manager.list_searches()
        for search in searches:
            tasks = self.manager.list_tasks(search.id)
            completed = sum(task.status == Status.COMPLETED for task in tasks)
            criteria = self._format_criteria(search.criteria)
            self.table.insert(
                "", "end", iid=search.id,
                text=search.name,
                values=(STATUS_LABELS[search.status], f"{completed}/{len(tasks)}", search.created_at[:19], criteria),
            )
        if self.selected_search_id and self.selected_search_id in self.table.get_children():
            self.table.selection_set(self.selected_search_id)
            self._show_events(self.selected_search_id)
        else:
            self.selected_search_id = None
            self.details.configure(text="Sélectionnez une recherche pour afficher son journal.")

    def new_search(self) -> None:
        dialog = _NewSearchDialog(self.root)
        self.root.wait_window(dialog.window)
        if dialog.result is None:
            return
        name, criteria = dialog.result
        self.manager.create_search(name, criteria)
        self.refresh()
        self.footer.configure(text="Recherche enregistrée en attente. Aucun accès réseau n'est effectué dans cette version.")

    def resume_selected(self) -> None:
        search = self._selected_search()
        if search is None:
            return
        try:
            self.manager.resume_search(search.id)
        except InvalidTransition as exc:
            messagebox.showinfo("Reprise impossible", str(exc), parent=self.root)
            return
        self.refresh()
        messagebox.showinfo(
            "Recherche prête",
            "La progression persistée est conservée et la recherche est remise en attente. "
            "Le moteur de collecte Sidjilcom n'est pas encore activé dans cette première étape.",
            parent=self.root,
        )

    def pause_selected(self) -> None:
        search = self._selected_search()
        if search is None:
            return
        try:
            self.manager.pause_search(search.id)
        except InvalidTransition as exc:
            messagebox.showinfo("Suspension impossible", str(exc), parent=self.root)
            return
        self.refresh()

    def _selected_search(self) -> Search | None:
        selected = self.table.selection()
        if not selected:
            messagebox.showinfo("Aucune sélection", "Sélectionnez d'abord une recherche.", parent=self.root)
            return None
        return self.manager.get_search(selected[0])

    def _on_select(self, _event: Any) -> None:
        selected = self.table.selection()
        self.selected_search_id = selected[0] if selected else None
        if self.selected_search_id:
            self._show_events(self.selected_search_id)

    def _show_events(self, search_id: str) -> None:
        events = self.manager.list_events(search_id, limit=4)
        if not events:
            self.details.configure(text="Aucun événement enregistré pour cette recherche.")
            return
        recent = "   •   ".join(f"{event['created_at'][11:19]} {event['message']}" for event in events[-3:])
        self.details.configure(text=f"Journal récent : {recent}")

    @staticmethod
    def _format_criteria(criteria: dict[str, Any]) -> str:
        return " · ".join(f"{key}: {value}" for key, value in criteria.items() if value) or "—"

    def _connect_sidjilcom(self) -> None:
        self.session_manager.connect()
        self._refresh_session_status()

    def _verify_sidjilcom(self) -> None:
        self.session_manager.verify_session()
        self._refresh_session_status()

    def _disconnect_sidjilcom(self) -> None:
        self.session_manager.disconnect()
        self._refresh_session_status()

    def _go_home(self) -> None:
        self._request_session_operation(self.session_manager.go_home(), "Retour à l'accueil")

    def _open_dashboard(self) -> None:
        self._request_session_operation(self.session_manager.open_dashboard(), "Ouverture du tableau de bord")

    def _open_enterprise_search(self) -> None:
        self._request_session_operation(
            self.session_manager.open_enterprise_search(), "Ouverture de Trouver une entreprise"
        )

    def _diagnose_page(self) -> None:
        self._request_session_operation(self.session_manager.diagnose_page(), "Diagnostic de la page")

    def _diagnose_search_modes(self) -> None:
        self._request_session_operation(
            self.session_manager.diagnose_search_modes(),
            "Analyse des modes (aucune recherche ne sera soumise)",
        )

    def _set_diagnostic_report(self, report: str) -> None:
        self._diagnostic_report = report
        self.diagnostic_text.configure(state="normal")
        self.diagnostic_text.delete("1.0", "end")
        if report:
            self.diagnostic_text.insert("1.0", report)
        else:
            self.diagnostic_text.insert("1.0", "Aucun rapport de diagnostic disponible.")
        self.diagnostic_text.configure(state="disabled")
        state = "normal" if report else "disabled"
        self.copy_diagnostic_button.configure(state=state)
        self.save_diagnostic_button.configure(state=state)

    def _copy_diagnostic(self) -> None:
        if not self._diagnostic_report:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self._diagnostic_report)
        self.root.update_idletasks()
        self.diagnostic_output.configure(text="Diagnostic structurel copié dans le presse-papiers.")

    def _save_diagnostic(self) -> None:
        if not self._diagnostic_report:
            return
        destination = filedialog.asksaveasfilename(
            parent=self.root,
            title="Enregistrer le diagnostic Sidjilcom",
            defaultextension=".txt",
            initialfile="sidjily-diagnostic.txt",
            filetypes=(("Rapport texte", "*.txt"), ("Tous les fichiers", "*.*")),
        )
        if not destination:
            return
        try:
            with open(destination, "w", encoding="utf-8", newline="\n") as report_file:
                report_file.write(self._diagnostic_report)
        except OSError as exc:
            messagebox.showerror("Enregistrement impossible", str(exc), parent=self.root)
            return
        self.diagnostic_output.configure(text=f"Diagnostic enregistré : {destination}")

    def _request_session_operation(self, future: Any, label: str) -> None:
        if self._pending_session_operation is not None and not self._pending_session_operation.done():
            return
        self._pending_session_operation = future
        self._set_diagnostic_report("")
        self.diagnostic_output.configure(text=f"{label}…")
        self._refresh_session_status()

    def _consume_session_operation(self) -> None:
        future = self._pending_session_operation
        if future is None or not future.done():
            return
        self._pending_session_operation = None
        try:
            result = future.result()
        except SessionOperationError as exc:
            message = str(exc)
            if "DIAGNOSTIC DE DÉTECTION DES MODES" in message:
                self._set_diagnostic_report(message)
                self.diagnostic_output.configure(
                    text="Détection des modes incomplète; le rapport de candidats est affiché ci-dessous."
                )
            else:
                self._set_diagnostic_report("")
                self.diagnostic_output.configure(text=message)
        except Exception:
            self._set_diagnostic_report("")
            self.diagnostic_output.configure(text="Diagnostic impossible; consultez l'état du navigateur et du réseau.")
        else:
            page = result.page
            state_label = SESSION_LABELS[result.state][0]
            nav = ", ".join(f"{item.label}: {item.url}" for item in page.navigation_items) or "aucun lien reconnu"
            fields = ", ".join(page.visible_fields[:24]) or "aucun champ visible identifié"
            self._set_diagnostic_report(page.report)
            self.diagnostic_output.configure(
                text=(
                    f"État : {state_label} · "
                    f"Section : {page.section}\nURL : {page.url}\nTitre : {page.title or '—'}\n"
                    f"Navigation reconnue : {nav}\nChamps visibles (libellés uniquement) : {fields}"
                )
            )
        self._refresh_session_status()

    def _refresh_session_status(self) -> None:
        snapshot = self.session_manager.snapshot
        label, color = SESSION_LABELS[snapshot.state]
        self.session_indicator.configure(text=label, foreground=color)
        self.session_message.configure(text=snapshot.message)
        running = self.session_manager.is_running
        busy = snapshot.state in (SessionState.CONNECTING, SessionState.DISCONNECTING)
        self.connect_button.configure(
            state="disabled" if busy or snapshot.state == SessionState.CONNECTED else "normal"
        )
        self.verify_button.configure(state="normal" if running and not busy else "disabled")
        self.disconnect_button.configure(state="normal" if running and not busy else "disabled")
        operation_pending = (
            self._pending_session_operation is not None
            and not self._pending_session_operation.done()
        )
        navigation_enabled = running and not busy and not operation_pending and snapshot.state != SessionState.ERROR
        state = "normal" if navigation_enabled else "disabled"
        self.home_button.configure(state=state)
        self.dashboard_button.configure(state=state)
        self.enterprise_search_button.configure(state=state)
        self.diagnostics_button.configure(state=state)
        self.search_modes_button.configure(state=state)

    def _poll_session(self) -> None:
        self._refresh_session_status()
        self._consume_session_operation()
        if not self._closing:
            self.root.after(500, self._poll_session)

    def _on_close(self) -> None:
        self._closing = True
        self.session_manager.disconnect()
        self._finish_close_after_session()

    def _finish_close_after_session(self) -> None:
        if self.session_manager.is_running:
            self.root.after(100, self._finish_close_after_session)
        else:
            self.root.destroy()


class _NewSearchDialog:
    def __init__(self, parent: tk.Tk):
        self.result: tuple[str, dict[str, Any]] | None = None
        self.window = tk.Toplevel(parent)
        self.window.title("Nouvelle recherche")
        self.window.transient(parent)
        self.window.grab_set()
        self.window.resizable(False, False)
        frame = ttk.Frame(self.window, padding=16)
        frame.pack(fill="both", expand=True)
        self.name = tk.StringVar(value="Nouvelle recherche")
        self.person_type = tk.StringVar(value="Personne morale")
        self.activity = tk.StringVar()
        self.wilaya = tk.StringVar()
        self.commune = tk.StringVar()
        self.period = tk.StringVar()
        fields = [
            ("Nom de la recherche", self.name),
            ("Type de personne", self.person_type),
            ("Code activité", self.activity),
            ("Wilaya", self.wilaya),
            ("Commune", self.commune),
            ("Période (facultatif)", self.period),
        ]
        for row, (label, variable) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
            if label == "Type de personne":
                ttk.Combobox(
                    frame, textvariable=variable, values=("Personne morale", "Personne physique"),
                    state="readonly", width=32,
                ).grid(row=row, column=1, sticky="ew", pady=5)
            else:
                ttk.Entry(frame, textvariable=variable, width=36).grid(row=row, column=1, sticky="ew", pady=5)
        buttons = ttk.Frame(frame)
        buttons.grid(row=len(fields), column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Annuler", command=self.window.destroy).pack(side="right")
        ttk.Button(buttons, text="Créer", command=self._submit).pack(side="right", padx=8)
        self.window.bind("<Return>", lambda _event: self._submit())

    def _submit(self) -> None:
        name = self.name.get().strip()
        if not name:
            messagebox.showwarning("Nom requis", "Saisissez un nom pour cette recherche.", parent=self.window)
            return
        criteria = {
            "type_personne": self.person_type.get(),
            "code_activite": self.activity.get().strip() or None,
            "wilaya": self.wilaya.get().strip() or None,
            "commune": self.commune.get().strip() or None,
            "periode": self.period.get().strip() or None,
        }
        self.result = name, criteria
        self.window.destroy()
