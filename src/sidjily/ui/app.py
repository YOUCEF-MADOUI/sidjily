"""Interface Tkinter initiale : création et suivi local des recherches."""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any

from sidjily.models import Search, Status
from sidjily.sidjilcom.criteria import (
    CriteriaValidationError,
    SearchCriteria,
    SearchMode,
    criteria_from_mapping,
    criteria_to_mapping,
    format_criteria_preview,
)
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
        self.manager.create_search(name, criteria_to_mapping(criteria))
        self.refresh()
        self.footer.configure(
            text="Brouillon enregistré localement. L'aperçu n'a envoyé aucune requête Sidjilcom."
        )

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
    """Saisie locale, validation et aperçu des critères (sans appel au portail)."""

    def __init__(self, parent: tk.Tk):
        self.result: tuple[str, SearchCriteria] | None = None
        self.window = tk.Toplevel(parent)
        self.window.title("Préparer une recherche Sidjilcom")
        self.window.transient(parent)
        self.window.grab_set()
        self.window.geometry("1080x800")
        self.window.minsize(900, 650)

        self.name = tk.StringVar(value="Nouvelle recherche")
        self.mode = tk.StringVar(value="")
        self._variables: dict[str, tk.StringVar] = {}
        self._specific_variables: dict[str, tk.StringVar] = {}
        self._watched: set[str] = set()

        frame = ttk.Frame(self.window, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(3, weight=1)
        ttk.Label(frame, text="Préparer une recherche", font=("Segoe UI", 15, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )

        identity = ttk.Frame(frame)
        identity.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        identity.columnconfigure(1, weight=1)
        ttk.Label(identity, text="Nom du brouillon").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(identity, textvariable=self.name, width=38).grid(row=0, column=1, sticky="ew", padx=(0, 18))
        ttk.Label(identity, text="Mode obligatoire").grid(row=0, column=2, sticky="w", padx=(0, 8))
        ttk.Combobox(
            identity,
            textvariable=self.mode,
            values=("Personne physique", "Personne morale"),
            state="readonly",
            width=24,
        ).grid(row=0, column=3, sticky="w")

        common_frame = ttk.LabelFrame(frame, text="Critères communs — tous facultatifs", padding=8)
        common_frame.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        common_frame.columnconfigure(1, weight=1)
        common_frame.columnconfigure(3, weight=1)
        common_specs = (
            ("nrc1", "N° d'inscription — nrc1", "text", ()),
            ("nrc2", "N° d'inscription — nrc2", "disabled_select", ()),
            ("nrc3", "N° d'inscription — nrc3", "text", ()),
            ("nrc4", "N° d'inscription — nrc4", "text", ()),
            ("nrc5", "N° d'inscription — nrc5", "disabled_select", ()),
            ("commune_wilaya", "Commune/Wilaya d'inscription", "autocomplete", ()),
            ("secteur_activite", "Secteur d'activité", "disabled_select", ()),
            ("activite", "Activité", "autocomplete", ()),
            ("date_inscription_du", "Date d'inscription — Du", "text", ()),
            ("date_inscription_au", "Date d'inscription — Au", "text", ()),
            ("conformite_rc", "Conformité RC", "disabled_select", ()),
            ("etat_commercant", "État commerçant", "disabled_select", ()),
        )
        for index, (key, label, kind, choices) in enumerate(common_specs):
            self._add_control(
                common_frame, index // 2, (index % 2) * 2,
                key, label, kind, choices, self._variables,
            )
        ttk.Label(
            common_frame,
            text=(
                "Dates au format AAAA-MM-JJ. nrc1 à nrc5 restent des composants distincts; "
                "leur signification doit être confirmée avant toute saisie automatique."
            ),
            wraplength=950,
            foreground="#555555",
        ).grid(row=(len(common_specs) + 1) // 2, column=0, columnspan=4, sticky="w", pady=(7, 0))

        self.specific_frame = ttk.LabelFrame(frame, text="Critères spécifiques au mode", padding=8)
        self.specific_frame.grid(row=3, column=0, sticky="nsew", pady=(0, 8))
        self.specific_frame.columnconfigure(1, weight=1)
        self.specific_frame.columnconfigure(3, weight=1)
        self.mode_hint = ttk.Label(
            self.specific_frame,
            text="Choisissez Personne physique ou Personne morale pour afficher ses critères.",
            wraplength=950,
        )
        self.mode_hint.grid(row=0, column=0, columnspan=4, sticky="w")

        preview_frame = ttk.LabelFrame(frame, text="Aperçu local — aucune recherche ne sera lancée", padding=8)
        preview_frame.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        self.preview = tk.Text(preview_frame, height=6, wrap="word", state="disabled", font=("Consolas", 9))
        self.preview.pack(fill="both", expand=True)
        self.status = ttk.Label(frame, text="Choisissez un mode, puis préparez un aperçu.", wraplength=950)
        self.status.grid(row=5, column=0, sticky="w", pady=(0, 6))

        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=0, sticky="e")
        ttk.Button(buttons, text="Annuler", command=self.window.destroy).pack(side="right")
        self.save_button = ttk.Button(
            buttons, text="Enregistrer le brouillon", command=self._save_draft, state="disabled"
        )
        self.save_button.pack(side="right", padx=8)
        ttk.Button(buttons, text="Préparer la recherche", command=self._prepare_preview).pack(side="right")

        self._watch(self.name)
        self._watch(self.mode)
        self.mode.trace_add("write", self._on_mode_change)
        self.window.bind("<Return>", lambda _event: self._prepare_preview())
        self.window.bind("<Escape>", lambda _event: self.window.destroy())

    def _watch(self, variable: tk.StringVar) -> None:
        key = str(variable)
        if key not in self._watched:
            variable.trace_add("write", self._invalidate_preview)
            self._watched.add(key)

    def _add_control(
        self,
        parent: ttk.LabelFrame,
        row: int,
        column: int,
        key: str,
        label: str,
        kind: str,
        choices: tuple[str, ...],
        storage: dict[str, tk.StringVar],
    ) -> None:
        variable = tk.StringVar()
        storage[key] = variable
        self._variables[key] = variable
        self._watch(variable)
        rendered_label = label
        if kind == "autocomplete":
            rendered_label += " · suggestions non interrogées"
        elif kind == "disabled_select":
            rendered_label += " · options non fournies"
        ttk.Label(parent, text=rendered_label).grid(
            row=row, column=column, sticky="w", padx=(2, 7), pady=3
        )
        if kind == "enum":
            ttk.Combobox(
                parent, textvariable=variable, values=("",) + choices,
                state="readonly", width=25,
            ).grid(row=row, column=column + 1, sticky="ew", padx=(0, 12), pady=3)
        elif kind == "disabled_select":
            ttk.Combobox(
                parent, textvariable=variable, values=(), state="disabled", width=25,
            ).grid(row=row, column=column + 1, sticky="ew", padx=(0, 12), pady=3)
        else:
            ttk.Entry(parent, textvariable=variable, width=28).grid(
                row=row, column=column + 1, sticky="ew", padx=(0, 12), pady=3
            )

    def _on_mode_change(self, *_args: object) -> None:
        self._invalidate_preview()
        self._render_specific_fields()

    def _render_specific_fields(self) -> None:
        for child in self.specific_frame.winfo_children():
            child.destroy()
        self._specific_variables.clear()
        mode_specs: dict[str, tuple[tuple[str, str, str, tuple[str, ...]], ...]] = {
            "Personne physique": (
                ("nom", "Nom", "text", ()),
                ("prenom", "Prénom", "text", ()),
                ("nom_commercial", "Nom commercial", "text", ()),
                ("date_naissance", "Date de naissance", "text", ()),
                ("presume", "Présumé", "disabled_select", ()),
                ("nationalite", "Nationalité", "autocomplete", ()),
            ),
            "Personne morale": (
                ("raison_sociale", "Raison Sociale / Nom commercial", "text", ()),
                ("forme_juridique", "Forme Juridique", "disabled_select", ()),
                ("nom_prenom_dirigeant", "Nom / Prénom du dirigeant", "text", ()),
                ("date_naissance_dirigeant", "Date de naissance du dirigeant", "text", ()),
                ("presume", "Présumé", "disabled_select", ()),
                ("nationalite", "Nationalité", "autocomplete", ()),
                ("qualite", "Qualité", "disabled_select", ()),
            ),
        }
        specs = mode_specs.get(self.mode.get())
        if specs is None:
            self.mode_hint = ttk.Label(
                self.specific_frame,
                text="Choisissez Personne physique ou Personne morale pour afficher ses critères.",
                wraplength=950,
            )
            self.mode_hint.grid(row=0, column=0, columnspan=4, sticky="w")
            return
        self.mode_hint = ttk.Label(
            self.specific_frame,
            text="Tous ces critères sont facultatifs. Les options non fournies restent désactivées.",
            wraplength=950,
        )
        self.mode_hint.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 4))
        for index, (key, label, kind, choices) in enumerate(specs):
            self._add_control(
                self.specific_frame, 1 + index // 2, (index % 2) * 2,
                key, label, kind, choices, self._specific_variables,
            )

    def _invalidate_preview(self, *_args: object) -> None:
        if hasattr(self, "save_button"):
            self.save_button.configure(state="disabled")
            self.status.configure(text="Les critères ont changé; préparez à nouveau l'aperçu.")
            self._set_preview("")

    def _build_criteria(self) -> SearchCriteria:
        mode_by_label = {
            "Personne physique": SearchMode.PERSONNE_PHYSIQUE,
            "Personne morale": SearchMode.PERSONNE_MORALE,
        }
        mode = mode_by_label.get(self.mode.get())
        if mode is None:
            raise CriteriaValidationError("Le mode de recherche est obligatoire.")
        payload: dict[str, Any] = {
            "mode": mode.value,
            "numero_inscription": {
                key: self._variables[key].get().strip()
                for key in ("nrc1", "nrc2", "nrc3", "nrc4", "nrc5")
            },
            "commune_wilaya": self._variables["commune_wilaya"].get(),
            "secteur_activite": self._variables["secteur_activite"].get(),
            "activite": self._variables["activite"].get(),
            "date_inscription_du": self._variables["date_inscription_du"].get(),
            "date_inscription_au": self._variables["date_inscription_au"].get(),
            "conformite_rc": self._variables["conformite_rc"].get(),
            "etat_commercant": self._variables["etat_commercant"].get(),
        }
        payload.update({key: variable.get() for key, variable in self._specific_variables.items()})
        return criteria_from_mapping(payload)

    def _prepare_preview(self) -> None:
        if not self.name.get().strip():
            messagebox.showwarning("Nom requis", "Saisissez un nom pour ce brouillon.", parent=self.window)
            return
        try:
            criteria = self._build_criteria()
            preview_text = format_criteria_preview(criteria)
        except CriteriaValidationError as exc:
            self.save_button.configure(state="disabled")
            self.status.configure(text="Les critères ne sont pas valides.")
            messagebox.showwarning("Critères invalides", str(exc), parent=self.window)
            return
        self._set_preview(preview_text)
        self.status.configure(text="Aperçu prêt; aucune requête ou autocomplétion distante n'a été déclenchée.")
        self.save_button.configure(state="normal")

    def _set_preview(self, text: str) -> None:
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        if text:
            self.preview.insert("1.0", text)
        self.preview.configure(state="disabled")

    def _save_draft(self) -> None:
        name = self.name.get().strip()
        if not name:
            messagebox.showwarning("Nom requis", "Saisissez un nom pour ce brouillon.", parent=self.window)
            return
        try:
            criteria = self._build_criteria()
        except CriteriaValidationError as exc:
            self.save_button.configure(state="disabled")
            messagebox.showwarning("Critères invalides", str(exc), parent=self.window)
            return
        self.result = name, criteria
        self.window.destroy()
