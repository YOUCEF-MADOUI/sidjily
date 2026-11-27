"""Interface Tkinter initiale : création et suivi local des recherches."""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any

from sidjily.export import write_csv
from sidjily.models import Search, Status
from sidjily.sidjilcom.autocomplete import (
    AUTOCOMPLETE_FIELD_LABELS,
    AutocompleteObservation,
    AutocompleteSelectionResult,
    AutocompleteTestStatus,
)
from sidjily.sidjilcom.criteria import (
    SIDJILCOM_CONTROL_MAP,
    ComponentType,
    CriteriaValidationError,
    SearchCriteria,
    SearchMode,
    apply_observed_controls,
    criteria_from_mapping,
    criteria_to_mapping,
    format_criteria_preview,
)
from sidjily.sidjilcom.preparation import (
    ADVANCED_FIELDS,
    CONFIRMED_AUTOCOMPLETE_FIELDS,
    FIELD_LABELS,
    PRIMARY_FIELDS,
    count_filled_criteria,
    draft_summary_record,
    format_preparation_summary,
)
from sidjily.sidjilcom.search import (
    SearchExecutionError,
    SearchObservation,
    validate_first_controlled_search,
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

CONTROLLED_STEP_LABELS = {
    "prepared": "Préparée",
    "mode_selection": "Sélection du mode",
    "form_validation": "Validation du formulaire",
    "filling_criteria": "Saisie des critères",
    "submitting": "Soumission en cours — résultat potentiellement inconnu",
    "submitted": "Soumise — observation en cours",
    "observing_results": "Observation des résultats",
    "results_detected": "Résultats détectés",
    "no_results": "Aucun résultat",
    "result_unknown": "Résultat inconnu — ne pas relancer",
    "result_too_large": "Refusée par le portail : plus de 300 résultats — affinez (années)",
    "failed": "Échec avant soumission",
}

OBSERVED_CONTROLS_SETTING = "sidjilcom_observed_controls"
MAX_RESULT_PAGES = 50

FORM_DIAGNOSTIC_STAGES = {
    "Après navigation": "after_navigation",
    "Après sélection de la Wilaya": "after_wilaya_selection",
    "Juste avant soumission (sans soumettre)": "before_submission",
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
        self._pending_search_operation: Any = None
        self._pending_search_id: str | None = None
        self._pending_result_diagnostic_id: str | None = None
        self._pending_preparation_requested = False
        self._pending_preparation_draft: Search | None = None
        self._pending_navigation_action = ""
        self._pending_pages_context: tuple[str, tuple[str, ...], int] | None = None
        self._pending_details_context: tuple[str, Any] | None = None
        self._closing = False
        try:
            apply_observed_controls(self.manager.load_setting(OBSERVED_CONTROLS_SETTING, {}))
        except Exception:
            pass
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
            text="Préparez vos critères localement. Une recherche réelle ne part qu'après confirmation explicite dans SIDJILY.",
            wraplength=880,
        ).pack(anchor="w", pady=(2, 12))

        session_panel = ttk.LabelFrame(container, text="CONNEXION SIDJILCOM", padding=10)
        session_panel.pack(fill="x", pady=(0, 10))
        session_info = ttk.Frame(session_panel)
        session_info.pack(side="left", fill="x", expand=True)
        self.session_indicator = ttk.Label(
            session_info, text="🔴 Non connecté", font=("Segoe UI", 10, "bold")
        )
        self.session_indicator.pack(anchor="w")
        self.session_message = ttk.Label(
            session_info,
            text="Aucune session Sidjilcom ouverte.",
            wraplength=520,
        )
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

        search_panel = ttk.LabelFrame(container, text="RECHERCHE", padding=10)
        search_panel.pack(fill="x", pady=(0, 10))
        ttk.Label(
            search_panel,
            text="Trouver une entreprise vérifie la session et ouvre le portail; Nouvelle recherche ouvre directement le formulaire SIDJILY.",
            wraplength=850,
        ).pack(anchor="w", pady=(0, 8))
        search_buttons = ttk.Frame(search_panel)
        search_buttons.pack(fill="x")
        self.enterprise_search_button = ttk.Button(
            search_buttons, text="Trouver une entreprise", command=self._open_enterprise_search
        )
        self.enterprise_search_button.pack(side="left")
        self.new_search_button = ttk.Button(
            search_buttons, text="Nouvelle recherche", command=self.new_search
        )
        self.new_search_button.pack(side="left", padx=8)
        self.continue_button = ttk.Button(
            search_buttons, text="Continuer la recherche", command=self.resume_selected
        )
        self.continue_button.pack(side="left")
        self.capture_controls_button = ttk.Button(
            search_buttons, text="Capturer les listes du portail", command=self._capture_search_controls
        )
        self.capture_controls_button.pack(side="left", padx=8)
        self.catalog_status = ttk.Label(search_panel, text=self._catalog_status_text(), foreground="#555555")
        self.catalog_status.pack(anchor="w", pady=(7, 0))
        ttk.Label(
            search_panel,
            text="L'aperçu et l'enregistrement sont locaux. Une recherche réelle n'est lancée qu'après confirmation explicite.",
            foreground="#555555",
        ).pack(anchor="w", pady=(2, 0))

        self.developer_toggle = ttk.Button(
            container, text="Outils développeur  ▸", command=self._toggle_developer_tools
        )
        self.developer_toggle.pack(anchor="w", pady=(0, 5))
        self.developer_content = ttk.LabelFrame(container, text="Outils développeur", padding=8)
        self._developer_tools_visible = False
        self._build_developer_tools(self.developer_content)

        history_toolbar = ttk.Frame(container)
        history_toolbar.pack(fill="x", pady=(3, 4))
        ttk.Label(history_toolbar, text="Recherches enregistrées / brouillons", font=("Segoe UI", 10, "bold")).pack(side="left")
        self.pause_button = ttk.Button(history_toolbar, text="Suspendre", command=self.pause_selected)
        self.pause_button.pack(side="right")
        ttk.Button(history_toolbar, text="Actualiser", command=self.refresh).pack(side="right", padx=7)
        self.export_csv_button = ttk.Button(
            history_toolbar, text="Exporter CSV", command=self._export_csv, state="disabled"
        )
        self.export_csv_button.pack(side="right")
        self.results_button = ttk.Button(
            history_toolbar, text="Résultats et fiches", command=self._open_results_window, state="disabled"
        )
        self.results_button.pack(side="right", padx=7)

        columns = ("status", "progress", "created", "criteria")
        self.table = ttk.Treeview(container, columns=columns, show="tree headings", selectmode="browse")
        self.table.heading("#0", text="Recherche / brouillon")
        self.table.heading("status", text="État")
        self.table.heading("progress", text="Tâches terminées")
        self.table.heading("created", text="Créée le (UTC)")
        self.table.heading("criteria", text="Critères")
        self.table.column("#0", width=220, minwidth=150)
        self.table.column("status", width=110, anchor="center")
        self.table.column("progress", width=125, anchor="center")
        self.table.column("created", width=150)
        self.table.column("criteria", width=300)
        self.table.pack(fill="both", expand=True)
        self.table.bind("<<TreeviewSelect>>", self._on_select)

        self.details = ttk.Label(
            container,
            text="Sélectionnez un brouillon pour voir son récapitulatif et le reprendre.",
            wraplength=890,
        )
        self.details.pack(anchor="w", pady=(10, 0))
        self.footer = ttk.Label(
            container,
            text="Brouillons et critères enregistrés localement dans SQLite.",
            foreground="#555555",
        )
        self.footer.pack(anchor="w", pady=(6, 0))

    def _build_developer_tools(self, parent: ttk.LabelFrame) -> None:
        navigation_panel = ttk.Frame(parent)
        navigation_panel.pack(fill="x")
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
        self.autocomplete_button = ttk.Button(
            navigation_buttons,
            text="Tester une autocomplétion",
            command=self._test_autocomplete,
        )
        self.autocomplete_button.pack(side="left", padx=8)
        self.result_observation_button = ttk.Button(
            navigation_buttons,
            text="Diagnostiquer le résultat (lecture seule)",
            command=self._diagnose_current_search_results,
        )
        self.result_observation_button.pack(side="left", padx=8)

        form_diagnostic_controls = ttk.Frame(parent)
        form_diagnostic_controls.pack(fill="x", pady=(7, 0))
        ttk.Label(form_diagnostic_controls, text="Capture à comparer :").pack(side="left")
        self.form_diagnostic_stage = ttk.Combobox(
            form_diagnostic_controls,
            values=tuple(FORM_DIAGNOSTIC_STAGES),
            state="readonly",
            width=40,
        )
        self.form_diagnostic_stage.set("Après navigation")
        self.form_diagnostic_stage.pack(side="left", padx=8)
        self.real_form_diagnostic_button = ttk.Button(
            form_diagnostic_controls,
            text="Diagnostiquer le formulaire réel",
            command=self._diagnose_real_search_form,
        )
        self.real_form_diagnostic_button.pack(side="left")

        self.diagnostic_output = ttk.Label(
            parent,
            text="Aucun diagnostic. Aucune valeur de champ, cookie ou jeton n'est collecté; aucune recherche n'est lancée.",
            wraplength=880,
            justify="left",
        )
        self.diagnostic_output.pack(anchor="w", pady=(7, 0))
        report_toolbar = ttk.Frame(parent)
        report_toolbar.pack(fill="x", pady=(6, 4))
        self.copy_diagnostic_button = ttk.Button(
            report_toolbar, text="Copier le diagnostic", command=self._copy_diagnostic, state="disabled"
        )
        self.copy_diagnostic_button.pack(side="left")
        self.save_diagnostic_button = ttk.Button(
            report_toolbar, text="Enregistrer le diagnostic", command=self._save_diagnostic, state="disabled"
        )
        self.save_diagnostic_button.pack(side="left", padx=8)
        report_frame = ttk.Frame(parent)
        report_frame.pack(fill="x")
        self.diagnostic_text = tk.Text(
            report_frame, height=6, wrap="word", state="disabled", font=("Consolas", 9)
        )
        self.diagnostic_text.pack(side="left", fill="x", expand=True)
        report_scrollbar = ttk.Scrollbar(report_frame, orient="vertical", command=self.diagnostic_text.yview)
        report_scrollbar.pack(side="right", fill="y")
        self.diagnostic_text.configure(yscrollcommand=report_scrollbar.set)
        self._diagnostic_report = ""

    def _toggle_developer_tools(self) -> None:
        if self._developer_tools_visible:
            self.developer_content.pack_forget()
            self.developer_toggle.configure(text="Outils développeur  ▸")
        else:
            self.developer_content.pack(fill="x", pady=(0, 8), after=self.developer_toggle)
            self.developer_toggle.configure(text="Outils développeur  ▾")
        self._developer_tools_visible = not self._developer_tools_visible

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
        self._refresh_result_buttons()

    def new_search(self) -> None:
        """Ouvre directement le formulaire local, sans navigation Sidjilcom."""
        self._open_preparation_dialog()

    def _begin_preparation(self, draft: Search | None = None) -> None:
        snapshot = self.session_manager.snapshot
        if self._pending_session_operation is not None and not self._pending_session_operation.done():
            messagebox.showinfo("Opération en cours", "Patientez pendant l'opération Sidjilcom en cours.", parent=self.root)
            return
        self._pending_preparation_draft = draft
        if snapshot.state == SessionState.CONNECTED and self.session_manager.is_running:
            self._open_enterprise_search()
            return
        self._pending_preparation_requested = True
        if not self.session_manager.is_running:
            self.session_manager.connect()
            self._refresh_session_status()
        messagebox.showinfo(
            "Vérification de la session",
            "SIDJILY ouvre ou vérifie son Chromium dédié. Connectez-vous manuellement dans cette fenêtre; "
            "une fois la session reconnue, SIDJILY ouvrira la recherche et affichera les critères. "
            "Aucune recherche ne sera envoyée.",
            parent=self.root,
        )

    def _open_preparation_dialog(self, draft: Search | None = None) -> None:
        try:
            dialog = _NewSearchDialog(self.root, initial_search=draft)
            self.root.wait_window(dialog.window)
            if dialog.result is None:
                return
            name, criteria = dialog.result
            if dialog.launch_confirmed:
                self._start_confirmed_controlled_search(name, criteria, draft_id=dialog.draft_id)
                return
            criteria_mapping = criteria_to_mapping(criteria)
            summary = draft_summary_record(criteria)
            if dialog.draft_id and self.manager.is_draft(dialog.draft_id):
                saved_search = self.manager.update_draft(dialog.draft_id, name, criteria_mapping, summary)
                saved_message = "Brouillon mis à jour localement. Aucun envoi à Sidjilcom n'a été effectué."
            elif dialog.draft_id and self.manager.is_prepared_controlled_search(dialog.draft_id):
                saved_search = self.manager.update_prepared_controlled_search(
                    dialog.draft_id, name, criteria_mapping, summary
                )
                saved_message = "Recherche préparée mise à jour localement. Aucune soumission n'a été effectuée."
            else:
                saved_search = self.manager.create_draft(name, criteria_mapping, summary)
                saved_message = "Brouillon enregistré localement. Aucune recherche n'a été envoyée à Sidjilcom."
            self.selected_search_id = saved_search.id
            self.refresh()
            self.footer.configure(text=saved_message)
        except Exception:
            messagebox.showerror(
                "Préparateur indisponible",
                "SIDJILY n'a pas pu ouvrir ou enregistrer le formulaire de préparation. "
                "Aucune recherche réelle n'a été envoyée.",
                parent=self.root,
            )

    def new_real_search(self) -> None:
        """Parcours réel de compatibilité; reste soumis au même dialogue de confirmation."""
        if self._pending_search_id is not None or (
            self._pending_search_operation is not None and not self._pending_search_operation.done()
        ):
            messagebox.showinfo("Recherche en cours", "Une recherche contrôlée est déjà en cours.", parent=self.root)
            return
        dialog = _NewSearchDialog(self.root, launch_mode=True)
        self.root.wait_window(dialog.window)
        if dialog.result is not None and dialog.launch_confirmed:
            name, criteria = dialog.result
            self._start_confirmed_controlled_search(name, criteria, draft_id=dialog.draft_id)

    def _start_confirmed_controlled_search(
        self, name: str, criteria: SearchCriteria, *, draft_id: str | None = None
    ) -> None:
        if self.session_manager.snapshot.state != SessionState.CONNECTED or not self.session_manager.is_running:
            messagebox.showinfo(
                "Session requise",
                "Connectez-vous manuellement dans Chromium SIDJILY et ouvrez « Trouver une entreprise ». "
                "La confirmation n'a pas déclenché de soumission.",
                parent=self.root,
            )
            return
        if self._pending_search_id is not None or (
            self._pending_search_operation is not None and not self._pending_search_operation.done()
        ):
            messagebox.showinfo("Recherche en cours", "Une recherche contrôlée est déjà en cours.", parent=self.root)
            return
        try:
            state = self.manager.controlled_submission_state()
            if state == "reserved":
                raise InvalidTransition("Une recherche contrôlée est déjà réservée et en cours.")
            validate_first_controlled_search(criteria)
            mapping = criteria_to_mapping(criteria)
            if draft_id and self.manager.is_draft(draft_id):
                search = self.manager.promote_draft_to_controlled_search(draft_id, name, mapping)
            elif draft_id and self.manager.is_prepared_controlled_search(draft_id):
                search = self.manager.update_prepared_controlled_search(draft_id, name, mapping)
            else:
                search = self.manager.create_controlled_search(name, mapping)
            self.manager.start_controlled_search(search.id)
            future = self.session_manager.execute_search(
                criteria,
                confirmed=True,
                on_step=lambda step: self.manager.update_controlled_search_step(search.id, step),
                on_pre_submit=lambda diagnostic: self.manager.update_controlled_search_pre_submit_diagnostic(
                    search.id, diagnostic
                ),
                # « Trop Grand » (> 300) : découpage automatique par périodes d'inscription.
                split_periods=True,
            )
        except (ValueError, SessionOperationError, SearchExecutionError) as exc:
            messagebox.showerror("Recherche non lancée", str(exc), parent=self.root)
            return
        self.selected_search_id = search.id
        self._pending_search_id = search.id
        self._pending_search_operation = future
        self.footer.configure(
            text="Recherche contrôlée en cours. Un seul clic Rechercher est autorisé; les résultats seront collectés après soumission."
        )
        self.refresh()
        self._refresh_session_status()

    def resume_selected(self) -> None:
        selected = self._selected_search(required=False)
        draft = None
        if selected is not None and (
            self.manager.is_draft(selected.id)
            or self.manager.is_prepared_controlled_search(selected.id)
        ):
            draft = selected
        else:
            candidates = [
                *self.manager.list_drafts(limit=1),
                *self.manager.list_prepared_controlled_searches(limit=1),
            ]
            draft = max(candidates, key=lambda item: item.updated_at) if candidates else None
        if draft is None:
            messagebox.showinfo(
                "Aucune recherche préparée à continuer",
                "Sélectionnez un brouillon ou une recherche préparée, ou créez une nouvelle recherche préparatoire.",
                parent=self.root,
            )
            return
        self._open_preparation_dialog(draft)

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

    def _selected_search(self, *, required: bool = True) -> Search | None:
        selected = self.table.selection()
        if not selected:
            if required:
                messagebox.showinfo("Aucune sélection", "Sélectionnez d'abord une recherche.", parent=self.root)
            return None
        return self.manager.get_search(selected[0])

    def _on_select(self, _event: Any) -> None:
        selected = self.table.selection()
        self.selected_search_id = selected[0] if selected else None
        if self.selected_search_id:
            self._show_events(self.selected_search_id)
        self._refresh_result_buttons()

    def _show_events(self, search_id: str) -> None:
        search = self.manager.get_search(search_id)
        if self.manager.is_draft(search_id):
            summary = search.result_summary or {}
            recap = summary.get("summary", "Récapitulatif à préparer.")
            self.details.configure(
                text=(
                    f"Brouillon · créé le {search.created_at} · état : {search.status.value}\n"
                    f"{recap}"
                )
            )
            return
        step = CONTROLLED_STEP_LABELS.get(search.step, search.step)
        events = self.manager.list_events(search_id, limit=4)
        recent = "   •   ".join(f"{event['created_at'][11:19]} {event['message']}" for event in events[-3:])
        self.details.configure(
            text=(
                f"Recherche contrôlée · état : {search.status.value} · étape : {step} · créée le {search.created_at}\n"
                f"{recent or 'Aucun événement enregistré.'}"
            )
        )

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
        self._pending_preparation_requested = False
        self._pending_preparation_draft = None
        self.session_manager.disconnect()
        self._refresh_session_status()

    def _go_home(self) -> None:
        self._request_session_operation(self.session_manager.go_home(), "Retour à l'accueil")

    def _open_dashboard(self) -> None:
        self._request_session_operation(self.session_manager.open_dashboard(), "Ouverture du tableau de bord")

    def _open_enterprise_search(self) -> None:
        if self.session_manager.snapshot.state != SessionState.CONNECTED or not self.session_manager.is_running:
            self._begin_preparation()
            return
        if self._pending_session_operation is not None and not self._pending_session_operation.done():
            return
        self._pending_navigation_action = "prepare"
        self._request_session_operation(
            self.session_manager.open_enterprise_search(),
            "Ouverture de la recherche Sidjilcom (aucune soumission)",
        )

    @staticmethod
    def _catalog_status_text() -> str:
        enabled = [
            mapping.label for mapping in SIDJILCOM_CONTROL_MAP.values()
            if mapping.component_type is ComponentType.SELECT and mapping.options
        ]
        if not enabled:
            return ("Listes déroulantes : non capturées. Ouvrez « Trouver une entreprise » puis "
                    "« Capturer les listes du portail » pour les activer.")
        return f"Listes déroulantes actives ({len(enabled)}) : " + ", ".join(sorted(set(enabled)))

    def _capture_search_controls(self) -> None:
        if self.session_manager.snapshot.state != SessionState.CONNECTED:
            messagebox.showinfo(
                "Session requise",
                "Connectez-vous dans Chromium SIDJILY et ouvrez « Trouver une entreprise » avant la capture. "
                "La capture lit seulement les listes du formulaire; aucune recherche n'est lancée.",
                parent=self.root,
            )
            return
        if self._pending_session_operation is not None and not self._pending_session_operation.done():
            return
        self._pending_navigation_action = "capture_controls"
        self._request_session_operation(
            self.session_manager.capture_search_controls(),
            "Capture des listes du formulaire (aucune recherche ne sera soumise)",
        )

    def _finish_controls_capture(self, future: Any) -> None:
        try:
            observed = future.result()
        except Exception as exc:
            message = str(exc) if isinstance(exc, (SessionOperationError, SearchExecutionError)) else ""
            self._notify("Capture impossible; ouvrez « Trouver une entreprise » puis réessayez. " + message)
            self._refresh_session_status()
            return
        updated = apply_observed_controls(observed)
        self.manager.save_setting(OBSERVED_CONTROLS_SETTING, observed)
        lines = ["CAPTURE DES LISTES DU FORMULAIRE SIDJILCOM", "Aucun critère saisi, aucune recherche soumise.", ""]
        for criterion in sorted(observed):
            info = observed[criterion]
            mapping = SIDJILCOM_CONTROL_MAP.get(criterion)
            label = mapping.label if mapping else criterion
            state = "activé" if criterion in updated else "inchangé"
            options = info.get("options") or []
            detail = f"{len(options)} option(s)" if info.get("tag") == "select" else f"{info.get('tag')}/{info.get('type')}"
            lines.append(f"- {label} : {detail} · {state}")
        self._set_diagnostic_report("\n".join(lines))
        self.catalog_status.configure(text=self._catalog_status_text())
        self._notify(f"Capture terminée : {len(updated)} champ(s) activé(s). Les nouveaux formulaires les proposent.")
        self._refresh_session_status()

    def _finish_page_collection(self, future: Any) -> None:
        context = self._pending_pages_context
        self._pending_pages_context = None
        if context is None:
            return
        search_id, columns, total = context
        pages_read = 1
        interrupted = False
        try:
            pages = future.result()
        except Exception:
            pages = []
            interrupted = True
        for offset, page in enumerate(pages, start=2):
            total += self.manager.store_search_results(
                search_id, columns, page.rows, page=offset, links=page.row_links,
            )
            pages_read = offset
        suffix = " Pagination interrompue; les lignes déjà lues sont conservées." if interrupted else ""
        self._notify(f"Recherche terminée; {total} ligne(s) collectée(s) sur {pages_read} page(s).{suffix}")
        if self._diagnostic_report:
            self._set_diagnostic_report(
                self._diagnostic_report + f"\nTotal collecté : {total} ligne(s) sur {pages_read} page(s).{suffix}"
            )
        self.refresh()
        self._refresh_session_status()

    def _open_results_window(self) -> None:
        if not self.selected_search_id or self.manager.get_search_result_count(self.selected_search_id) == 0:
            messagebox.showinfo("Résultats", "Aucun résultat collecté pour cette recherche.", parent=self.root)
            return
        _ResultsWindow(self, self.selected_search_id)

    def _request_enterprise_details(self, search_id: str, items: list[tuple[str, str]], window: Any) -> bool:
        if self.session_manager.snapshot.state != SessionState.CONNECTED:
            messagebox.showinfo(
                "Session requise",
                "Connectez-vous dans Chromium SIDJILY pour lire les fiches entreprise.",
                parent=window.window,
            )
            return False
        if self._pending_session_operation is not None and not self._pending_session_operation.done():
            messagebox.showinfo("Opération en cours", "Attendez la fin de l'opération Sidjilcom en cours.", parent=window.window)
            return False
        self._pending_details_context = (search_id, window)
        self._pending_navigation_action = "read_details"
        self._pending_session_operation = self.session_manager.read_enterprise_details(items)
        self._notify(f"Lecture de {len(items)} fiche(s) entreprise en cours…")
        self._refresh_session_status()
        return True

    def _finish_enterprise_details(self, future: Any) -> None:
        context = self._pending_details_context
        self._pending_details_context = None
        if context is None:
            return
        search_id, window = context
        try:
            details = future.result()
        except Exception as exc:
            details = []
            message = str(exc) if isinstance(exc, (SessionOperationError, SearchExecutionError)) else ""
            self._notify("Lecture des fiches impossible. " + message)
        stored = self.manager.store_result_details(search_id, details)
        failures = [detail for detail in details if detail.get("error")]
        empty = sum(1 for detail in details if not detail.get("error") and not detail.get("fields"))
        if details:
            text = f"{stored} fiche(s) enregistrée(s)"
            if empty:
                text += f", dont {empty} sans champ reconnu"
            if failures:
                text += f"; {len(failures)} échec(s) : {str(failures[-1].get('error', '')).rstrip('.')}"
            self._notify(text + ".")
        try:
            if window.window.winfo_exists():
                window.reload()
                window.set_busy(False)
        except tk.TclError:
            pass
        self._refresh_session_status()

    def _start_page_collection(self, search_id: str, columns: tuple[str, ...], rows_so_far: int) -> bool:
        if self._pending_session_operation is not None and not self._pending_session_operation.done():
            return False
        self._pending_pages_context = (search_id, columns, rows_so_far)
        self._pending_navigation_action = "collect_pages"
        self._pending_session_operation = self.session_manager.collect_result_pages(MAX_RESULT_PAGES - 1)
        self._refresh_session_status()
        return True

    def _diagnose_page(self) -> None:
        self._request_session_operation(self.session_manager.diagnose_page(), "Diagnostic de la page")

    def _diagnose_search_modes(self) -> None:
        self._request_session_operation(
            self.session_manager.diagnose_search_modes(),
            "Analyse des modes (aucune recherche ne sera soumise)",
        )

    def _diagnose_current_search_results(self) -> None:
        search = self._selected_search(required=False)
        if search is None or search.step != "result_unknown":
            messagebox.showinfo(
                "Résultat non marqué comme inconnu",
                "Sélectionnez une recherche contrôlée dont l'étape est « result_unknown ». "
                "Ce diagnostic ne lance jamais de recherche.",
                parent=self.root,
            )
            return
        if self.session_manager.snapshot.state != SessionState.CONNECTED:
            messagebox.showinfo(
                "Session requise",
                "Reconnectez-vous manuellement et laissez ouverte la page Sidjilcom à diagnostiquer. "
                "Cette action est une lecture structurelle, sans nouvelle soumission.",
                parent=self.root,
            )
            return
        self._pending_result_diagnostic_id = search.id
        self._pending_navigation_action = "result_diagnostic"
        self._request_session_operation(
            self.session_manager.diagnose_search_results(),
            "Diagnostic structurel du résultat en lecture seule",
        )

    def _diagnose_real_search_form(self) -> None:
        if self.session_manager.snapshot.state != SessionState.CONNECTED:
            messagebox.showinfo(
                "Session requise",
                "Connectez-vous dans Chromium SIDJILY puis laissez ouverte la page réelle du formulaire personne morale.",
                parent=self.root,
            )
            return
        stage_label = self.form_diagnostic_stage.get()
        stage = FORM_DIAGNOSTIC_STAGES.get(stage_label)
        if not stage:
            self.diagnostic_output.configure(text="Choisissez une étape de capture valide.")
            return
        self._request_session_operation(
            self.session_manager.diagnose_real_search_form(stage),
            f"Diagnostic réel en lecture seule — {stage_label} (aucune recherche ne sera lancée)",
        )

    def _test_autocomplete(self) -> None:
        if self.session_manager.snapshot.state != SessionState.CONNECTED:
            messagebox.showinfo(
                "Session requise",
                "Connectez-vous puis ouvrez « Trouver une entreprise » avant le test.",
                parent=self.root,
            )
            return
        dialog = _AutocompleteTestDialog(self.root, self.session_manager)
        self.root.wait_window(dialog.window)

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
        self._refresh_result_buttons()

    def _refresh_result_buttons(self) -> None:
        state = "disabled"
        if self.selected_search_id:
            try:
                state = "normal" if self.manager.get_search_result_count(self.selected_search_id) > 0 else "disabled"
            except Exception:
                pass
        self.export_csv_button.configure(state=state)
        self.results_button.configure(state=state)

    def _notify(self, text: str) -> None:
        self.diagnostic_output.configure(text=text)
        self.footer.configure(text=text)

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

    def _export_csv(self) -> None:
        if not self.selected_search_id:
            return
        self._export_search_csv(self.selected_search_id, self.root)

    def _export_search_csv(self, search_id: str, parent: tk.Misc) -> None:
        results = self.manager.get_search_results(search_id)
        if not results:
            messagebox.showinfo("Export CSV", "Aucun résultat collecté pour cette recherche.", parent=parent)
            return
        destination = filedialog.asksaveasfilename(
            parent=parent,
            title="Exporter les résultats en CSV",
            defaultextension=".csv",
            initialfile=f"sidjily-{search_id[:8]}.csv",
            filetypes=(("Fichier CSV", "*.csv"), ("Tous les fichiers", "*.*")),
        )
        if not destination:
            return
        try:
            count = write_csv(destination, results)
        except OSError as exc:
            messagebox.showerror("Export impossible", str(exc), parent=parent)
            return
        self._notify(f"CSV exporté ({count} lignes) : {destination}")

    def _request_session_operation(self, future: Any, label: str) -> None:
        if self._pending_session_operation is not None and not self._pending_session_operation.done():
            return
        self._pending_session_operation = future
        if self._pending_navigation_action != "prepare":
            self._set_diagnostic_report("")
            self.diagnostic_output.configure(text=f"{label}…")
        else:
            self.footer.configure(text=f"{label}… aucune recherche ne sera envoyée.")
        self._refresh_session_status()

    def _consume_session_operation(self) -> None:
        future = self._pending_session_operation
        if future is None or not future.done():
            return
        self._pending_session_operation = None
        navigation_action = self._pending_navigation_action
        self._pending_navigation_action = ""
        if navigation_action == "capture_controls":
            self._finish_controls_capture(future)
            return
        if navigation_action == "collect_pages":
            self._finish_page_collection(future)
            return
        if navigation_action == "read_details":
            self._finish_enterprise_details(future)
            return
        try:
            result = future.result()
        except SessionOperationError as exc:
            message = str(exc)
            if navigation_action == "result_diagnostic":
                self._pending_result_diagnostic_id = None
            if navigation_action == "prepare":
                self._pending_preparation_draft = None
                self.footer.configure(text=f"Navigation impossible; aucune recherche envoyée. {message}")
                messagebox.showwarning(
                    "Recherche Sidjilcom non confirmée",
                    f"La page de recherche n'a pas pu être confirmée. {message}\n\n"
                    "Vérifiez la session puis réessayez. Aucune recherche n'a été envoyée.",
                    parent=self.root,
                )
            elif "DIAGNOSTIC DE DÉTECTION DES MODES" in message:
                self._set_diagnostic_report(message)
                self.diagnostic_output.configure(
                    text="Détection des modes incomplète; le rapport de candidats est affiché ci-dessous."
                )
            else:
                self._set_diagnostic_report("")
                self.diagnostic_output.configure(text=message)
        except Exception:
            if navigation_action == "result_diagnostic":
                self._pending_result_diagnostic_id = None
            if navigation_action == "prepare":
                self._pending_preparation_draft = None
                self.footer.configure(text="Navigation impossible; aucune recherche Sidjilcom n'a été envoyée.")
            else:
                self._set_diagnostic_report("")
                self.diagnostic_output.configure(text="Diagnostic impossible; consultez l'état du navigateur et du réseau.")
        else:
            if navigation_action == "prepare":
                draft = self._pending_preparation_draft
                self._pending_preparation_draft = None
                self.footer.configure(text="Recherche Sidjilcom ouverte dans Chromium SIDJILY; formulaire prêt pour préparation locale.")
                self._open_preparation_dialog(draft)
                self._refresh_session_status()
                return
            if navigation_action == "result_diagnostic":
                search_id = self._pending_result_diagnostic_id
                self._pending_result_diagnostic_id = None
                observation: SearchObservation = result
                summary = observation.to_mapping()
                if search_id:
                    self.manager.store_unknown_search_diagnostic(search_id, summary)
                report = [
                    "DIAGNOSTIC STRUCTUREL ULTÉRIEUR — LECTURE SEULE",
                    f"ID : {search_id or 'non associé'}",
                    f"Titre : {observation.title or '—'}",
                    f"URL assainie : {observation.sanitized_url}",
                    f"Compteur éventuel : {observation.result_count if observation.result_count is not None else 'non détecté'}",
                    f"Zone de résultat sémantique : {'oui' if observation.result_zone_found else 'non'}"
                    f"{f' ({observation.result_zone_tag})' if observation.result_zone_tag else ''}",
                    f"Tableaux visibles : {observation.table_count}",
                    f"Lignes structurelles : {observation.row_count}",
                    "Colonnes : " + (" · ".join(observation.columns) if observation.columns else "aucune détectée"),
                    f"Pagination visible : {'oui' if observation.pagination_visible else 'non'}",
                    f"Aucun résultat : {'oui' if observation.no_results else 'non'}",
                    "Erreurs : " + (" · ".join(observation.errors) if observation.errors else "aucune détectée"),
                    "Observation de la page actuellement ouverte; son rattachement au résultat initial n'est pas prouvé. "
                    "Aucune nouvelle recherche soumise, aucune cellule d'entreprise lue, aucun détail ouvert.",
                ]
                self._set_diagnostic_report("\n".join(report))
                self.diagnostic_output.configure(text="Diagnostic ultérieur enregistré sans nouvelle soumission.")
                self.refresh()
                self._refresh_session_status()
                return
            page = result.page
            state_label = SESSION_LABELS[result.state][0]
            nav = ", ".join(f"{item.label}: {item.url}" for item in page.navigation_items) or "aucun lien reconnu"
            fields = ", ".join(page.visible_fields[:24]) or "aucun champ visible identifié"
            self._set_diagnostic_report(page.report)
            if page.report.startswith("DIAGNOSTIC STRUCTUREL RÉEL"):
                summary = (
                    f"État : {state_label} · Capture en lecture seule terminée. "
                    "Aucune navigation, valeur de champ ni suggestion n'a été affichée ou modifiée."
                )
            else:
                summary = (
                    f"État : {state_label} · "
                    f"Section : {page.section}\nURL : {page.url}\nTitre : {page.title or '—'}\n"
                    f"Navigation reconnue : {nav}\nChamps visibles (libellés uniquement) : {fields}"
                )
            self.diagnostic_output.configure(text=summary)
        self._refresh_session_status()

    def _consume_controlled_search(self) -> None:
        future = self._pending_search_operation
        search_id = self._pending_search_id
        if future is None or search_id is None or not future.done():
            return
        self._pending_search_operation = None
        self._pending_search_id = None
        try:
            observation: SearchObservation = future.result()
        except (SearchExecutionError, SessionOperationError) as exc:
            error = str(exc)
            structural = getattr(exc, "diagnostic", None)
            if isinstance(structural, dict):
                try:
                    self.manager.store_controlled_search_failure_diagnostic(search_id, structural)
                except Exception:
                    pass
            self.manager.fail_controlled_search(
                search_id, error, portal_refused=getattr(exc, "code", "") == "result_too_large"
            )
            search = self.manager.get_search(search_id)
            self.selected_search_id = search_id
            unknown = search.step == "result_unknown"
            if search.step == "result_too_large":
                status_line = (
                    "Le portail a refusé d'afficher plus de 300 résultats. Créez une nouvelle recherche plus "
                    "précise, par exemple avec la période d'inscription (Du/Au) découpée par années."
                )
            elif unknown:
                status_line = (
                    "SOUMISSION POSSIBLE — résultat inconnu. Ne relancez pas la recherche. "
                    "Utilisez ensuite « Diagnostiquer le résultat (lecture seule) » si la page est toujours ouverte."
                )
            else:
                status_line = "Échec avant le clic Rechercher; aucune nouvelle tentative n'a été lancée."
            failure_diagnostic = (
                search.result_summary.get("controlled_search_failure_diagnostic", {})
                if search.result_summary else {}
            )
            detail_lines = self._format_controlled_search_failure_diagnostic(failure_diagnostic)
            self._set_diagnostic_report(
                "RECHERCHE SIDJILCOM CONTRÔLÉE — arrêtée sans nouvelle tentative\n"
                f"ID : {search_id}\nÉtape : {search.step}\n{status_line}\n"
                f"Erreur : {error}\n"
                + ("\n" + "\n".join(detail_lines) if detail_lines else "")
            )
            self._notify(f"{status_line} {error}")
            messagebox.showwarning("Recherche arrêtée", f"{status_line}\n\n{error}", parent=self.root)
        except Exception:
            error = "Erreur interne; détails techniques omis pour protéger la session et les critères."
            self.manager.fail_controlled_search(search_id, error)
            search = self.manager.get_search(search_id)
            self.selected_search_id = search_id
            unknown = search.step == "result_unknown"
            status_line = (
                "SOUMISSION POSSIBLE — résultat inconnu; ne relancez pas. Un diagnostic structurel en lecture seule est disponible."
                if unknown else "Échec avant soumission; aucune nouvelle tentative automatique."
            )
            self._set_diagnostic_report(
                "RECHERCHE SIDJILCOM CONTRÔLÉE — arrêtée sans nouvelle tentative\n"
                f"ID : {search_id}\nÉtape : {search.step}\n{status_line}\n"
                f"Erreur : {error}"
            )
            self._notify(f"{status_line} {error}")
            messagebox.showwarning("Recherche arrêtée", f"{status_line}\n\n{error}", parent=self.root)
        else:
            summary = observation.to_mapping()
            self.manager.complete_controlled_search(search_id, summary)
            self.selected_search_id = search_id
            rows_stored = 0
            columns = observation.columns
            if observation.rows and columns:
                rows_stored = self.manager.store_search_results(
                    search_id, columns, observation.rows, page=1, links=observation.row_links,
                )
            more_announced = observation.result_count is not None and observation.result_count > rows_stored
            collecting_more = (
                (observation.pagination_visible or more_announced) and rows_stored > 0
                and self._start_page_collection(search_id, columns, rows_stored)
            )
            report = [
                "RAPPORT — RECHERCHE SIDJILCOM",
                f"ID : {search_id}",
                f"Étape : {self.manager.get_search(search_id).step}",
                f"Titre : {observation.title or '—'}",
                f"URL assainie : {observation.sanitized_url}",
                f"Nombre de résultats affiché : {observation.result_count if observation.result_count is not None else 'non détecté'}",
                f"Zone de résultat sémantique : {'oui' if observation.result_zone_found else 'non'}"
                f"{f' ({observation.result_zone_tag})' if observation.result_zone_tag else ''}",
                f"Tableaux visibles : {observation.table_count}",
                f"Lignes du premier tableau : {observation.row_count}",
                "Colonnes : " + (" · ".join(columns) if columns else "aucune détectée"),
                f"Lignes collectées (page 1) : {rows_stored}",
                f"Pagination visible : {'oui' if observation.pagination_visible else 'non'}",
                f"Message aucun résultat : {'oui' if observation.no_results else 'non'}",
                "Erreurs : " + (" · ".join(observation.errors) if observation.errors else "aucune détectée"),
            ]
            if observation.period_report:
                report.append(
                    "Plus de 300 résultats : recherche découpée par période d'inscription "
                    "(10 ans, puis 5, 2, 1 an, 6 mois pour les périodes encore trop grandes) :"
                )
                report.extend(f"  · {line}" for line in observation.period_report)
            self._set_diagnostic_report("\n".join(report))
            if collecting_more:
                status_text = f"Page 1 : {rows_stored} ligne(s) collectée(s); parcours des pages suivantes en cours…"
            elif rows_stored:
                status_text = f"Recherche terminée; {rows_stored} ligne(s) collectée(s)."
            else:
                status_text = "Recherche terminée; aucune ligne collectée."
            self._notify(status_text)
        self.refresh()
        self._refresh_session_status()

    @staticmethod
    def _format_controlled_search_failure_diagnostic(diagnostic: object) -> list[str]:
        if not isinstance(diagnostic, dict):
            return []
        lines = [
            "DIAGNOSTIC STRUCTUREL (aucune valeur DOM, donnée d'entreprise ou secret conservé)",
            f"Étape de blocage : {diagnostic.get('stage') or 'inconnue'}",
            f"Raison exacte : {diagnostic.get('reason_code') or 'inconnue'}",
        ]
        page = diagnostic.get("page")
        if isinstance(page, dict):
            lines.append(
                f"Page : {page.get('section') or 'inconnue'} · {page.get('title') or 'sans titre'} · "
                f"URL : {page.get('url') or 'non reconnue'}"
            )
        def append_fields(fields: object) -> None:
            for field in fields if isinstance(fields, list) else []:
                if not isinstance(field, dict):
                    continue
                status = (
                    "valeur conforme" if field.get("value_matches_criterion") is True else
                    "valeur non conforme" if field.get("value_matches_criterion") is False else
                    "valeur non vérifiée"
                )
                lines.append(
                    f"Champ {field.get('criterion') or field.get('suffix') or 'inconnu'}: "
                    f"name={field.get('name') or '—'} · id={field.get('id') or '—'} · "
                    f"label={field.get('label') or '—'} · role={field.get('role') or '—'} · "
                    f"type={field.get('tag') or '—'}/{field.get('type') or '—'} · "
                    f"class={field.get('class') or '—'} · visible={field.get('visible', 'inconnu')} · "
                    f"disabled={field.get('disabled', 'inconnu')} · {status}"
                )

        def append_button(button: object) -> None:
            if isinstance(button, dict) and button:
                lines.append(
                    "Bouton Rechercher : "
                    f"candidats={button.get('candidate_count', 'inconnu')} · "
                    f"visible={button.get('visible', False)} · enabled={button.get('enabled', False)} · "
                    f"formulaire associé={button.get('associated', False)} · "
                    f"id={button.get('id') or '—'} · role={button.get('role') or '—'} · "
                    f"type={button.get('type') or '—'} · class={button.get('class') or '—'}"
                )

        candidate_forms = diagnostic.get("candidate_forms", [])
        if isinstance(candidate_forms, list) and candidate_forms:
            lines.append(f"Formulaires candidats observés : {len(candidate_forms)}")
            for index, candidate in enumerate(candidate_forms, start=1):
                if not isinstance(candidate, dict):
                    continue
                lines.append(
                    f"Formulaire candidat {index} : id={candidate.get('id') or '—'} · "
                    f"name={candidate.get('name') or '—'} · role={candidate.get('role') or '—'} · "
                    f"method={candidate.get('method') or '—'} · action={candidate.get('action') or '—'} · "
                    f"parent Recherche Commerçant={'oui' if candidate.get('parent_portlet_confirmed') else 'non'}"
                )
                append_fields(candidate.get("fields", []))
                append_button(candidate.get("button", {}))
                invalid_candidate = candidate.get("required_invalid_controls", [])
                if isinstance(invalid_candidate, list) and invalid_candidate:
                    lines.append("Champs obligatoires invalides : " + " · ".join(str(item) for item in invalid_candidate))
        else:
            form = diagnostic.get("form")
            if isinstance(form, dict) and form:
                lines.append(
                    "Formulaire : "
                    f"id={form.get('id') or '—'} · name={form.get('name') or '—'} · "
                    f"role={form.get('role') or '—'} · method={form.get('method') or '—'} · "
                    f"action={form.get('action') or '—'} · parent Recherche Commerçant="
                    f"{'oui' if form.get('parent_portlet_confirmed') else 'non'}"
                )
            append_fields(diagnostic.get("fields", []))
            append_button(diagnostic.get("button", {}))

        autocomplete = diagnostic.get("autocomplete")
        if isinstance(autocomplete, dict):
            lines.append(
                f"Autocomplete {autocomplete.get('field') or 'inconnu'} ({autocomplete.get('suffix') or '—'}): "
                f"{autocomplete.get('suggestion_count', 0)} suggestion(s), "
                f"{autocomplete.get('exact_code_match_count', 0)} correspondance(s) exacte(s) au code demandé."
            )
        invalid = diagnostic.get("required_invalid_controls", [])
        if isinstance(invalid, list) and invalid:
            lines.append("Champs obligatoires invalides : " + " · ".join(str(item) for item in invalid))
        return lines

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
            (self._pending_session_operation is not None and not self._pending_session_operation.done())
            or self._pending_search_id is not None
            or (self._pending_search_operation is not None and not self._pending_search_operation.done())
        )
        navigation_enabled = (
            running and not busy and not operation_pending and not self._pending_preparation_requested
            and snapshot.state == SessionState.CONNECTED
        )
        preparation_available = not operation_pending and not busy and not self._pending_preparation_requested
        self.new_search_button.configure(state="normal" if preparation_available else "disabled")
        self.enterprise_search_button.configure(state="normal" if preparation_available else "disabled")
        self.continue_button.configure(state="normal")
        state = "normal" if navigation_enabled else "disabled"
        self.home_button.configure(state=state)
        self.dashboard_button.configure(state=state)
        self.diagnostics_button.configure(state=state)
        self.search_modes_button.configure(state=state)
        self.autocomplete_button.configure(state=state)
        self.real_form_diagnostic_button.configure(
            state="normal" if navigation_enabled and snapshot.state == SessionState.CONNECTED else "disabled"
        )

    def _poll_session(self) -> None:
        self._refresh_session_status()
        self._consume_session_operation()
        if (
            self._pending_preparation_requested
            and self.session_manager.snapshot.state == SessionState.CONNECTED
            and self.session_manager.is_running
            and (self._pending_session_operation is None or self._pending_session_operation.done())
        ):
            self._pending_preparation_requested = False
            self._open_enterprise_search()
        elif self._pending_preparation_requested and self.session_manager.snapshot.state in (
            SessionState.ERROR, SessionState.SESSION_EXPIRED,
        ):
            self._pending_preparation_requested = False
            self.footer.configure(text="Session non vérifiée. Réessayez après avoir ouvert Sidjilcom et vérifié la session.")
        self._consume_controlled_search()
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


class _ResultsWindow:
    """Liste des lignes collectées; lecture et affichage des fiches entreprise."""

    MAX_BATCH = 100

    def __init__(self, app: SidjilyApp, search_id: str) -> None:
        self.app = app
        self.search_id = search_id
        self._results: dict[str, dict[str, Any]] = {}
        self.window = tk.Toplevel(app.root)
        self.window.title("Résultats et fiches entreprise")
        self.window.geometry("1100x700")
        self.window.minsize(760, 480)
        frame = ttk.Frame(self.window, padding=10)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=3)
        frame.rowconfigure(3, weight=2)
        self.summary = ttk.Label(frame, text="", wraplength=1050)
        self.summary.grid(row=0, column=0, sticky="w", pady=(0, 6))

        table_frame = ttk.Frame(frame)
        table_frame.grid(row=1, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(table_frame, show="headings", selectmode="extended")
        self.table.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.table.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.table.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.table.bind("<<TreeviewSelect>>", lambda _event: self._show_detail())

        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, sticky="ew", pady=6)
        self.read_selected_button = ttk.Button(
            buttons, text="Lire les fiches sélectionnées", command=self._read_selected
        )
        self.read_selected_button.pack(side="left")
        self.read_missing_button = ttk.Button(
            buttons, text=f"Lire les fiches manquantes (max {self.MAX_BATCH})", command=self._read_missing
        )
        self.read_missing_button.pack(side="left", padx=8)
        ttk.Button(
            buttons, text="Exporter CSV", command=lambda: app._export_search_csv(search_id, self.window)
        ).pack(side="left")
        ttk.Button(buttons, text="Fermer", command=self.window.destroy).pack(side="right")
        ttk.Label(
            frame,
            text="La lecture des fiches ouvre chaque page de détail dans Chromium SIDJILY (une fiche toutes les ~1,5 s).",
            foreground="#555555",
        ).grid(row=4, column=0, sticky="w", pady=(4, 0))

        detail_frame = ttk.Frame(frame)
        detail_frame.grid(row=3, column=0, sticky="nsew")
        detail_frame.columnconfigure(0, weight=1)
        detail_frame.rowconfigure(0, weight=1)
        self.detail_text = tk.Text(detail_frame, height=10, wrap="word", state="disabled", font=("Consolas", 9))
        self.detail_text.grid(row=0, column=0, sticky="nsew")
        detail_scroll = ttk.Scrollbar(detail_frame, orient="vertical", command=self.detail_text.yview)
        detail_scroll.grid(row=0, column=1, sticky="ns")
        self.detail_text.configure(yscrollcommand=detail_scroll.set)
        self.reload()

    @staticmethod
    def _detail_state(result: dict[str, Any]) -> str:
        detail = result.get("detail")
        if detail:
            return "lue" if detail.get("fields") else "lue (aucun champ reconnu)"
        return "à lire" if result.get("detail_url") else "pas de lien"

    def reload(self) -> None:
        results = self.app.manager.get_search_results(self.search_id)
        self._results = {result["key"]: result for result in results}
        columns = [str(column) for column in (results[0].get("columns", []) if results else [])] + ["Fiche"]
        identifiers = [f"c{index}" for index in range(len(columns))]
        selected = set(self.table.selection())
        self.table.delete(*self.table.get_children())
        self.table.configure(columns=identifiers)
        for identifier, label in zip(identifiers, columns):
            self.table.heading(identifier, text=label)
            self.table.column(identifier, width=110 if label == "Fiche" else 160, stretch=True)
        for result in results:
            values = [str(value) for value in result.get("values", [])]
            values = (values + [""] * (len(columns) - 1))[: len(columns) - 1]
            self.table.insert("", "end", iid=result["key"], values=(*values, self._detail_state(result)))
        restore = [key for key in selected if key in self._results]
        if restore:
            self.table.selection_set(restore)
        read = sum(1 for result in results if result.get("detail"))
        linked = sum(1 for result in results if result.get("detail_url"))
        self.summary.configure(
            text=f"{len(results)} ligne(s) collectée(s) · {linked} avec lien de fiche · {read} fiche(s) lue(s)."
            + ("" if linked else " Le portail n'expose pas de lien de fiche exploitable dans le tableau.")
        )
        self._show_detail()

    def set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        self.read_selected_button.configure(state=state)
        self.read_missing_button.configure(state=state)

    def _show_detail(self) -> None:
        selection = self.table.selection()
        lines: list[str] = []
        if len(selection) == 1 and selection[0] in self._results:
            result = self._results[selection[0]]
            detail = result.get("detail")
            if detail:
                lines.append(detail.get("title") or "Fiche entreprise")
                lines.append(f"Lue le {detail.get('read_at', '')[:19]} (UTC)")
                lines.append("")
                width = max((len(label) for label, _value in detail.get("fields", [])), default=0)
                lines.extend(f"{label.ljust(width)} : {value}" for label, value in detail.get("fields", []))
                if not detail.get("fields"):
                    lines.append("Aucune paire libellé/valeur reconnue sur la page de détail.")
            elif result.get("detail_url"):
                lines.append("Fiche non lue. Cliquez sur « Lire les fiches sélectionnées ».")
            else:
                lines.append("Aucun lien de fiche n'a été trouvé sur cette ligne du tableau de résultats.")
        elif len(selection) > 1:
            lines.append(f"{len(selection)} lignes sélectionnées.")
        else:
            lines.append("Sélectionnez une ligne pour afficher sa fiche.")
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("1.0", "\n".join(lines))
        self.detail_text.configure(state="disabled")

    def _request(self, keys: list[str]) -> None:
        items = [
            (key, str(self._results[key]["detail_url"]))
            for key in keys
            if key in self._results and self._results[key].get("detail_url")
        ][: self.MAX_BATCH]
        if not items:
            messagebox.showinfo("Fiches", "Aucune ligne avec un lien de fiche à lire.", parent=self.window)
            return
        if self.app._request_enterprise_details(self.search_id, items, self):
            self.set_busy(True)
            self.summary.configure(text=f"Lecture de {len(items)} fiche(s) en cours…")

    def _read_selected(self) -> None:
        self._request(list(self.table.selection()))

    def _read_missing(self) -> None:
        self._request([key for key, result in self._results.items() if not result.get("detail")])


class _NewSearchDialog:
    """Formulaire de préparation locale; aucune action réseau pendant la saisie."""

    _COMMON_KEYS = (
        "commune_wilaya", "activite", "secteur_activite", "date_inscription_du",
        "date_inscription_au", "conformite_rc", "etat_commercant",
    )
    _SPECIFIC_KEYS = {
        "Personne physique": (
            "nom", "prenom", "nationalite", "nom_commercial", "date_naissance", "presume",
        ),
        "Personne morale": (
            "raison_sociale", "forme_juridique", "nom_prenom_dirigeant",
            "date_naissance_dirigeant", "nationalite", "presume", "qualite",
        ),
    }
    _MODE_BY_LABEL = {
        "Personne physique": SearchMode.PERSONNE_PHYSIQUE,
        "Personne morale": SearchMode.PERSONNE_MORALE,
    }
    _DATE_FIELDS = frozenset({
        "date_inscription_du", "date_inscription_au", "date_naissance", "date_naissance_dirigeant",
    })

    def __init__(
        self,
        parent: tk.Tk,
        *,
        launch_mode: bool = False,
        initial_search: Search | None = None,
    ):
        self.launch_mode = launch_mode
        self.initial_search = initial_search
        self.draft_id = initial_search.id if initial_search is not None else None
        self.result: tuple[str, SearchCriteria] | None = None
        self.launch_confirmed = False
        self.window = tk.Toplevel(parent)
        self.window.title(
            "Recherche réelle — confirmation requise"
            if launch_mode else "Préparer une recherche"
        )
        self.window.transient(parent)
        self.window.grab_set()
        self.window.geometry("980x790")
        self.window.minsize(770, 610)

        self.name = tk.StringVar(
            value=(
                initial_search.name if initial_search is not None
                else "Recherche contrôlée" if launch_mode
                else "Nouvelle recherche"
            )
        )
        self.mode = tk.StringVar(value="Personne morale" if launch_mode else "")
        self._variables = {
            key: tk.StringVar()
            for key in set(self._COMMON_KEYS).union(*map(set, self._SPECIFIC_KEYS.values()))
        }
        self._number_variables = {f"nrc{index}": tk.StringVar() for index in range(1, 6)}
        if initial_search is None:
            # Par défaut, chaque recherche cible les commerçants conformes et actifs.
            self._variables["conformite_rc"].set("Oui")
            self._variables["etat_commercant"].set("Actif")
        self._watched: set[str] = set()
        self._advanced_visible = False
        self._restoring = False

        frame = ttk.Frame(self.window, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(3, weight=1)
        ttk.Label(frame, text="Préparer une recherche", font=("Segoe UI", 16, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 3)
        )
        ttk.Label(
            frame,
            text=(
                "Choisissez obligatoirement une personne physique ou morale. "
                "Wilaya et Commune utilisent le critère conjoint Sidjilcom confirmé; aucun endpoint ni liste séparée n'est supposé. "
                "Dates au format AAAA-MM-JJ; les listes non encore capturées depuis le portail restent grisées."
            ),
            wraplength=930,
            justify="left",
        ).grid(row=1, column=0, sticky="ew", pady=(0, 9))

        identity = ttk.Frame(frame)
        identity.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        identity.columnconfigure(1, weight=1)
        ttk.Label(identity, text="Nom du brouillon").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(identity, textvariable=self.name, width=38).grid(
            row=0, column=1, sticky="ew", padx=(0, 18)
        )
        ttk.Label(identity, text="Type de recherche (obligatoire)").grid(row=0, column=2, sticky="w", padx=(0, 8))
        self.mode_box = ttk.Combobox(
            identity,
            textvariable=self.mode,
            values=("Personne physique", "Personne morale"),
            state="readonly",
            width=22,
        )
        self.mode_box.grid(row=0, column=3, sticky="w")

        criteria_area = ttk.Frame(frame)
        criteria_area.grid(row=3, column=0, sticky="nsew", pady=(0, 8))
        criteria_area.columnconfigure(0, weight=1)
        criteria_area.rowconfigure(0, weight=1)
        self.primary_frame = ttk.LabelFrame(criteria_area, text="Critères principaux", padding=9)
        self.primary_frame.grid(row=0, column=0, sticky="new")
        for column in (1, 3):
            self.primary_frame.columnconfigure(column, weight=1)
        self.mode_hint = ttk.Label(
            self.primary_frame,
            text="Choisissez Personne physique ou Personne morale pour afficher les critères prioritaires.",
            wraplength=900,
        )
        self.mode_hint.grid(row=0, column=0, columnspan=4, sticky="w")

        self.advanced_toggle = ttk.Button(
            criteria_area,
            text="Critères avancés  ▸",
            command=self._toggle_advanced,
            state="disabled",
        )
        self.advanced_toggle.grid(row=1, column=0, sticky="w", pady=(6, 4))
        self.advanced_frame = ttk.LabelFrame(criteria_area, text="Critères avancés", padding=9)
        self.advanced_frame.columnconfigure(1, weight=1)
        self.advanced_frame.columnconfigure(3, weight=1)

        summary_frame = ttk.LabelFrame(
            frame,
            text="Récapitulatif — préparation locale uniquement",
            padding=8,
        )
        summary_frame.grid(row=4, column=0, sticky="ew", pady=(0, 7))
        summary_frame.columnconfigure(0, weight=1)
        self.count_label = ttk.Label(summary_frame, text="Critères renseignés : 0")
        self.count_label.pack(anchor="w", pady=(0, 4))
        self.preview = tk.Text(
            summary_frame, height=6, wrap="word", state="disabled", font=("Consolas", 9)
        )
        self.preview.pack(fill="both", expand=True)
        self.status = ttk.Label(
            frame,
            text="Choisissez un mode, saisissez vos critères puis préparez le récapitulatif.",
            wraplength=930,
        )
        self.status.grid(row=5, column=0, sticky="w", pady=(0, 6))

        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=0, sticky="e")
        ttk.Button(buttons, text="Annuler", command=self.window.destroy).pack(side="right")
        self.save_button = ttk.Button(
            buttons,
            text="Lancer la recherche" if launch_mode else "Enregistrer le brouillon",
            command=self._launch_search if launch_mode else self._save_draft,
            state="disabled",
        )
        self.save_button.pack(side="right", padx=8)
        self.launch_button: ttk.Button | None = None
        if not launch_mode:
            self.launch_button = ttk.Button(
                buttons, text="Lancer la recherche", command=self._launch_search, state="disabled"
            )
            self.launch_button.pack(side="left")
        ttk.Button(
            buttons,
            text="Préparer le récapitulatif",
            command=self._prepare_preview,
        ).pack(side="right")

        for variable in (*self._variables.values(), *self._number_variables.values(), self.name):
            self._watch(variable)
        self.mode.trace_add("write", self._on_mode_change)
        self._load_initial_values(initial_search)
        if launch_mode and initial_search is None:
            pass
        if self.mode.get():
            self._render_mode_fields()
        self.window.bind("<Return>", lambda _event: self._prepare_preview())
        self.window.bind("<Escape>", lambda _event: self.window.destroy())
        if initial_search is not None:
            self._prepare_preview(show_errors=False)
            self.status.configure(
                text=(
                    f"Préparation restaurée — créée le {initial_search.created_at} · "
                    f"état : {initial_search.status.value}. Modifiez les critères puis enregistrez ou confirmez explicitement le lancement."
                )
            )

    def _load_initial_values(self, search: Search | None) -> None:
        if search is None:
            return
        mapping = search.criteria
        raw_mode = mapping.get("mode")
        mode = next((label for label, value in self._MODE_BY_LABEL.items() if value.value == raw_mode), "")
        if not mode:
            return
        self._restoring = True
        self.mode.set(mode)
        for key in self._COMMON_KEYS:
            value = mapping.get(key)
            if isinstance(value, str):
                self._variables[key].set(value)
        number = mapping.get("numero_inscription", {})
        if isinstance(number, dict):
            for key, variable in self._number_variables.items():
                value = number.get(key)
                if isinstance(value, str):
                    variable.set(value)
        for key in self._SPECIFIC_KEYS[mode]:
            value = mapping.get(key)
            if isinstance(value, str):
                self._variables[key].set(value)
        self._restoring = False

    def _watch(self, variable: tk.StringVar) -> None:
        key = str(variable)
        if key not in self._watched:
            variable.trace_add("write", self._invalidate_preview)
            self._watched.add(key)

    def _on_mode_change(self, *_args: object) -> None:
        self._render_mode_fields()
        self._invalidate_preview()

    def _render_mode_fields(self) -> None:
        for child in self.primary_frame.winfo_children():
            child.destroy()
        self._advanced_visible = False
        self.advanced_frame.grid_remove()
        self.advanced_toggle.configure(text="Critères avancés  ▸")
        mode = self._MODE_BY_LABEL.get(self.mode.get())
        if mode is None:
            self.mode_hint = ttk.Label(
                self.primary_frame,
                text="Choisissez Personne physique ou Personne morale pour afficher les critères prioritaires.",
                wraplength=900,
            )
            self.mode_hint.grid(row=0, column=0, columnspan=4, sticky="w")
            self.advanced_toggle.configure(state="disabled")
            return
        self.advanced_toggle.configure(state="normal")
        self.mode_hint = ttk.Label(
            self.primary_frame,
            text=(
                "Champs prioritaires — saisie locale. Les suggestions confirmées (Activité, Wilaya/Commune, "
                "Nationalité) ne sont ni interrogées ni sélectionnées ici."
            ),
            wraplength=900,
        )
        self.mode_hint.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 4))
        keys = PRIMARY_FIELDS[mode]
        for index, key in enumerate(keys):
            self._add_field(self.primary_frame, key, row=1 + index // 2, column=(index % 2) * 2)
        for child in self.advanced_frame.winfo_children():
            child.destroy()
        advanced_keys = ADVANCED_FIELDS[mode]
        field_index = 0
        for key in advanced_keys:
            if key == "numero_inscription":
                self._add_number_components(self.advanced_frame, (field_index + 1) // 2)
                continue
            self._add_field(
                self.advanced_frame,
                key,
                row=field_index // 2,
                column=(field_index % 2) * 2,
            )
            field_index += 1

    def _criterion_path(self, key: str) -> str:
        if key in self._COMMON_KEYS:
            return key
        mode = self._MODE_BY_LABEL.get(self.mode.get())
        prefix = "physique" if mode is SearchMode.PERSONNE_PHYSIQUE else "morale"
        return f"{prefix}.{key}"

    @staticmethod
    def _mapping_kind(path: str) -> str:
        mapping = SIDJILCOM_CONTROL_MAP.get(path)
        if mapping is None:
            return "text"
        if mapping.component_type in (ComponentType.SELECT, ComponentType.RADIO):
            return "select" if mapping.options and mapping.input_semantics_confirmed else "select_unknown"
        if mapping.component_type is ComponentType.UNCONFIRMED or not mapping.input_semantics_confirmed:
            return "unconfirmed"
        return "text"

    def _field_kind(self, key: str) -> str:
        if key in self._DATE_FIELDS:
            return "date"
        if key in CONFIRMED_AUTOCOMPLETE_FIELDS or key in {"activite", "commune_wilaya", "nationalite"}:
            return "autocomplete"
        return self._mapping_kind(self._criterion_path(key))

    @staticmethod
    def _input_widget(parent: tk.Misc, variable: tk.StringVar, kind: str, path: str, width: int) -> tk.Widget:
        if kind == "select":
            options = SIDJILCOM_CONTROL_MAP[path].options
            return ttk.Combobox(parent, textvariable=variable, values=("", *options), state="readonly", width=width)
        if kind in ("select_unknown", "unconfirmed"):
            return ttk.Entry(parent, textvariable=variable, width=width, state="disabled")
        return ttk.Entry(parent, textvariable=variable, width=width)

    def _add_field(self, parent: ttk.LabelFrame, key: str, *, row: int, column: int) -> None:
        label = FIELD_LABELS.get(key, key.replace("_", " ").capitalize())
        kind = self._field_kind(key)
        if kind == "autocomplete":
            label += " · saisie locale"
        elif kind == "select_unknown":
            label += " · liste à capturer"
        elif kind == "unconfirmed":
            label += " · non confirmé"
        ttk.Label(parent, text=label).grid(
            row=row, column=column, sticky="w", padx=(2, 7), pady=4
        )
        widget = self._input_widget(parent, self._variables[key], kind, self._criterion_path(key), 40)
        widget.grid(row=row, column=column + 1, sticky="ew", padx=(0, 14), pady=4)

    def _add_number_components(self, parent: ttk.LabelFrame, row: int) -> None:
        box = ttk.LabelFrame(parent, text=FIELD_LABELS["numero_inscription"], padding=6)
        box.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(5, 3))
        disabled = 0
        for index in range(1, 6):
            key = f"nrc{index}"
            path = f"numero_inscription.{key}"
            kind = self._mapping_kind(path)
            disabled += kind in ("select_unknown", "unconfirmed")
            ttk.Label(box, text=key).grid(row=0, column=(index - 1) * 2, padx=(2, 3), sticky="e")
            self._input_widget(box, self._number_variables[key], kind, path, 12).grid(
                row=0, column=(index - 1) * 2 + 1, padx=(0, 8), sticky="ew"
            )
        note = "Saisissez chaque composant tel qu'il apparaît sur le portail."
        if disabled:
            note = ("Composants grisés : type non encore observé sur le portail. "
                    "Utilisez « Capturer les listes du portail » pour les activer.")
        ttk.Label(box, text=note, wraplength=850, foreground="#555555").grid(
            row=1, column=0, columnspan=10, sticky="w", pady=(5, 0)
        )

    def _toggle_advanced(self) -> None:
        if self._advanced_visible:
            self.advanced_frame.grid_remove()
            self.advanced_toggle.configure(text="Critères avancés  ▸")
        else:
            self.advanced_frame.grid(row=2, column=0, sticky="ew", pady=(0, 4))
            self.advanced_toggle.configure(text="Critères avancés  ▾")
        self._advanced_visible = not self._advanced_visible

    def _invalidate_preview(self, *_args: object) -> None:
        if self._restoring:
            return
        if hasattr(self, "save_button"):
            self.save_button.configure(state="disabled")
            if self.launch_button is not None:
                self.launch_button.configure(state="disabled")
            self.status.configure(text="Les critères ont changé; préparez à nouveau le récapitulatif.")
            self._set_preview("")
            self.count_label.configure(text="Critères renseignés : —")

    def _build_criteria(self) -> SearchCriteria:
        mode = self._MODE_BY_LABEL.get(self.mode.get())
        if mode is None:
            raise CriteriaValidationError("Le mode de recherche est obligatoire.")
        payload: dict[str, Any] = {"mode": mode.value}
        for key in self._COMMON_KEYS:
            value = self._variables[key].get().strip()
            if value:
                payload[key] = value
        number = {
            key: variable.get().strip()
            for key, variable in self._number_variables.items()
            if variable.get().strip()
        }
        if number:
            payload["numero_inscription"] = number
        for key in self._SPECIFIC_KEYS[self.mode.get()]:
            value = self._variables[key].get().strip()
            if value:
                payload[key] = value
        return criteria_from_mapping(payload)

    def _prepare_preview(self, *, show_errors: bool = True) -> None:
        if not self.name.get().strip():
            if show_errors:
                messagebox.showwarning("Nom requis", "Saisissez un nom pour ce brouillon.", parent=self.window)
            self.save_button.configure(state="disabled")
            if self.launch_button is not None:
                self.launch_button.configure(state="disabled")
            self.status.configure(text="Le nom du brouillon est obligatoire.")
            return
        try:
            criteria = self._build_criteria()
            preview_text = format_preparation_summary(criteria)
        except CriteriaValidationError as exc:
            self.save_button.configure(state="disabled")
            if self.launch_button is not None:
                self.launch_button.configure(state="disabled")
            self.status.configure(text="Les critères ne sont pas valides.")
            if show_errors:
                messagebox.showwarning("Critères invalides", str(exc), parent=self.window)
            return
        launch_allowed = True
        launch_hint = ""
        try:
            validate_first_controlled_search(criteria)
        except SearchExecutionError as exc:
            launch_allowed = False
            launch_hint = str(exc)
        if launch_allowed:
            preview_text += (
                "\n\nLancement réel autorisé. "
                "Toute soumission exige une confirmation explicite."
            )
        else:
            preview_text += (
                f"\n\nLancement non autorisé : {launch_hint}"
            )
        self.count_label.configure(text=f"Critères renseignés : {count_filled_criteria(criteria)}")
        self._set_preview(preview_text)
        if self.launch_mode and not launch_allowed:
            self.status.configure(text="Récapitulatif prêt, mais cette combinaison n'est pas autorisée pour ce flux réel.")
            self.save_button.configure(state="disabled")
        else:
            self.status.configure(
                text="Récapitulatif prêt. Aucune requête réseau ni soumission n'a été déclenchée."
            )
            self.save_button.configure(state="normal")
        if self.launch_button is not None:
            self.launch_button.configure(state="normal" if launch_allowed else "disabled")

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
        if str(self.save_button.cget("state")) == "disabled":
            messagebox.showinfo(
                "Récapitulatif requis",
                "Préparez d'abord le récapitulatif; aucun critère n'est enregistré avant sa validation locale.",
                parent=self.window,
            )
            return
        try:
            criteria = self._build_criteria()
        except CriteriaValidationError as exc:
            self.save_button.configure(state="disabled")
            messagebox.showwarning("Critères invalides", str(exc), parent=self.window)
            return
        self.result = name, criteria
        self.window.destroy()

    def _launch_search(self) -> None:
        """Demande une confirmation explicite avant de retourner l'ordre de lancement."""
        name = self.name.get().strip()
        launch_control = self.save_button if self.launch_mode else self.launch_button
        if launch_control is None or str(launch_control.cget("state")) == "disabled":
            messagebox.showinfo(
                "Récapitulatif requis",
                "Préparez d'abord un récapitulatif valide pour le périmètre autorisé. Aucune recherche n'a été soumise.",
                parent=self.window,
            )
            return
        if not name:
            messagebox.showwarning("Nom requis", "Saisissez un nom pour cette recherche.", parent=self.window)
            return
        try:
            criteria = self._build_criteria()
            validate_first_controlled_search(criteria)
            summary = format_criteria_preview(criteria)
        except (CriteriaValidationError, SearchExecutionError) as exc:
            self.save_button.configure(state="disabled")
            messagebox.showwarning("Recherche non autorisée", str(exc), parent=self.window)
            return
        confirmed = messagebox.askyesno(
            "Confirmation explicite avant recherche réelle",
            "RÉCAPITULATIF\n\n"
            f"Nom : {name}\n{summary}\n\n"
            "En choisissant Oui, vous autorisez SIDJILY à utiliser le formulaire visible, "
            "à sélectionner les suggestions exactes puis à cliquer une seule fois sur le vrai bouton Rechercher. "
            "Les résultats structurels seront collectés et pourront être exportés.\n\n"
            "Lancer cette recherche réelle maintenant ?",
            parent=self.window,
        )
        if not confirmed:
            return
        self.result = name, criteria
        self.launch_confirmed = True
        self.window.destroy()


class _AutocompleteTestDialog:
    """Outil manuel en trois temps: observer, choisir explicitement, puis arrêter."""

    def __init__(self, parent: tk.Tk, session_manager: SidjilcomSessionManager):
        self.session_manager = session_manager
        self.window = tk.Toplevel(parent)
        self.window.title("Test contrôlé d'une autocomplétion")
        self.window.transient(parent)
        self.window.grab_set()
        self.window.geometry("940x700")
        self.window.minsize(780, 560)
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)

        self._field_by_label = {label: key for key, label in AUTOCOMPLETE_FIELD_LABELS.items()}
        self.field = tk.StringVar(value="")
        self.query = tk.StringVar()
        self._future: Any = None
        self._operation: str | None = None
        self._token: str | None = None
        self._suggestions: tuple[Any, ...] = ()
        self._selection_done = False
        self._close_after_reset = False

        frame = ttk.Frame(self.window, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(3, weight=1)
        ttk.Label(frame, text="Tester une autocomplétion", font=("Segoe UI", 15, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
        ttk.Label(
            frame,
            text=(
                "Restez sur la page de recherche. Le champ ciblé doit être vide. "
                "Le test tape seulement la valeur choisie et observe le composant. "
                "Après une sélection, il s'arrête: aucun clic sur Rechercher, aucune collecte."
            ),
            wraplength=880,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 12))

        controls = ttk.Frame(frame)
        controls.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        ttk.Label(controls, text="Champ").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.field_box = ttk.Combobox(
            controls,
            textvariable=self.field,
            values=tuple(AUTOCOMPLETE_FIELD_LABELS.values()),
            state="readonly",
            width=38,
        )
        self.field_box.grid(row=0, column=1, sticky="w", padx=(0, 18))
        ttk.Label(controls, text="Valeur de test").grid(row=0, column=2, sticky="w", padx=(0, 8))
        self.query_entry = ttk.Entry(controls, textvariable=self.query, width=32)
        self.query_entry.grid(row=0, column=3, sticky="ew", padx=(0, 10))
        controls.columnconfigure(3, weight=1)
        self.test_button = ttk.Button(controls, text="Tester une autocomplétion", command=self._prepare)
        self.test_button.grid(row=0, column=4, sticky="e")

        result_frame = ttk.LabelFrame(frame, text="Suggestions observées — choisissez-en une pour la sélectionner", padding=8)
        result_frame.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=(0, 10))
        result_frame.columnconfigure(0, weight=1)
        result_frame.rowconfigure(0, weight=1)
        self.suggestion_list = tk.Listbox(result_frame, height=8, exportselection=False)
        self.suggestion_list.grid(row=0, column=0, sticky="nsew")
        self.suggestion_list.bind("<<ListboxSelect>>", lambda _event: self._update_buttons())
        suggestion_scroll = ttk.Scrollbar(result_frame, orient="vertical", command=self.suggestion_list.yview)
        suggestion_scroll.grid(row=0, column=1, sticky="ns")
        self.suggestion_list.configure(yscrollcommand=suggestion_scroll.set)

        details_frame = ttk.LabelFrame(frame, text="Observation structurelle (sans valeur de champ ni secret)", padding=8)
        details_frame.grid(row=4, column=0, columnspan=2, sticky="nsew", pady=(0, 8))
        details_frame.columnconfigure(0, weight=1)
        self.details = tk.Text(details_frame, height=10, wrap="word", state="disabled", font=("Consolas", 9))
        self.details.pack(fill="both", expand=True)
        self.status = ttk.Label(frame, text="Choisissez un champ et une valeur de test.", wraplength=880)
        self.status.grid(row=5, column=0, columnspan=2, sticky="w", pady=(0, 8))

        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=0, columnspan=2, sticky="e")
        self.select_button = ttk.Button(
            buttons, text="Sélectionner la suggestion choisie", command=self._select
        )
        self.select_button.pack(side="right", padx=(8, 0))
        self.reset_button = ttk.Button(
            buttons, text="Effacer le champ de test", command=self._reset, state="disabled"
        )
        self.reset_button.pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Fermer", command=self._on_close).pack(side="right")
        self._update_buttons()

    def _prepare(self) -> None:
        field_id = self._field_by_label.get(self.field.get())
        query = self.query.get()
        if field_id is None:
            self.status.configure(text="Choisissez l'un des trois champs autocomplete connus.")
            return
        if not query.strip():
            self.status.configure(text="Saisissez une valeur de test non vide.")
            return
        self._run_operation("prepare", self.session_manager.prepare_autocomplete_test(field_id, query))

    def _select(self) -> None:
        selected = self.suggestion_list.curselection()
        if not self._token or not selected:
            return
        index = int(selected[0])
        if index >= len(self._suggestions) or not self._suggestions[index].safe_to_select:
            self.status.configure(text="Cette suggestion n'est pas sûre à cliquer selon sa structure DOM.")
            return
        self._run_operation(
            "select",
            self.session_manager.select_autocomplete_suggestion(self._token, index),
        )

    def _reset(self) -> None:
        if self._token:
            self._run_operation("reset", self.session_manager.reset_autocomplete_test(self._token))

    def _run_operation(self, operation: str, future: Any) -> None:
        self._operation = operation
        self._future = future
        self.status.configure(text="Test en cours… aucune recherche n'est soumise.")
        self._update_buttons()
        self.window.after(100, self._poll_operation)

    def _poll_operation(self) -> None:
        if self._future is None or not self._future.done():
            if self._future is not None:
                self.window.after(100, self._poll_operation)
            return
        operation = self._operation
        future = self._future
        self._operation = None
        self._future = None
        try:
            result = future.result()
        except Exception as exc:
            self.status.configure(text=str(exc))
            if self._close_after_reset:
                self._close_after_reset = False
                if messagebox.askyesno(
                    "Fermeture sans réinitialisation",
                    "Le champ n'a pas pu être effacé. Fermer quand même? Vérifiez le champ dans le navigateur avant toute autre action.",
                    parent=self.window,
                ):
                    self.window.destroy()
                    return
            self._update_buttons()
            return

        if operation == "prepare":
            self._show_observation(result)
        elif operation == "select":
            self._show_selection(result)
        elif operation == "reset":
            self._token = None
            self._suggestions = ()
            self._selection_done = False
            self._fill_suggestions(())
            self.status.configure(text="Champ de test réinitialisé à vide. Aucun bouton de recherche n'a été activé.")
            if self._close_after_reset:
                self.window.destroy()
                return
        self._update_buttons()

    def _show_observation(self, observation: AutocompleteObservation) -> None:
        self._token = observation.token
        self._suggestions = observation.suggestions
        self._selection_done = False
        self._fill_suggestions(observation.suggestions)
        self._set_details(self._format_observation(observation))
        if observation.status is AutocompleteTestStatus.SUGGESTIONS:
            self.status.configure(
                text=f"{len(observation.suggestions)} suggestion(s) observée(s). Sélectionnez-en une, puis le test s'arrêtera."
            )
        elif observation.status is AutocompleteTestStatus.NO_SUGGESTIONS:
            self.status.configure(text="Le conteneur de suggestions est apparu sans option visible.")
        else:
            self.status.configure(
                text="Délai dépassé sans liste visible; valeur inexistante ou suggestion plus lente que le délai. Champ effacé."
            )

    def _show_selection(self, result: AutocompleteSelectionResult) -> None:
        self._selection_done = True
        state = "acceptée" if result.accepted else "non confirmée par le composant"
        self.status.configure(
            text=f"STOP — suggestion {state}. Aucune autre action n'est lancée; effacez le champ pour annuler le test."
        )
        detail = [
            f"Suggestion choisie : {result.suggestion_text}",
            f"Sélection acceptée : {'oui' if result.accepted else 'non'}",
            f"Valeur du champ égale au texte choisi : {'oui' if result.input_matches_suggestion else 'non'}",
            f"Liste de suggestions disparue : {'oui' if result.suggestions_disappeared else 'non'}",
            "Aucun clic sur Rechercher; aucune requête de recherche ni collecte de résultats.",
        ]
        if result.nearby_controls_before or result.nearby_controls_after:
            detail.append("Contrôles voisins avant sélection :")
            detail.extend(f"  {item}" for item in result.nearby_controls_before)
            detail.append("Contrôles voisins après sélection :")
            detail.extend(f"  {item}" for item in result.nearby_controls_after)
            detail.append("Une différence structurelle peut indiquer une mise à jour dépendante; aucune valeur de ces contrôles n'est lue.")
        self._set_details("\n".join(detail))

    @staticmethod
    def _format_observation(observation: AutocompleteObservation) -> str:
        control = observation.control
        lines = [
            f"Mapping : {control.label} · suffixe {control.suffix}",
            f"Contrôle : <{control.tag_name}> · id={control.element_id or 'absent'} · classes={', '.join(control.class_names) or 'aucune'}",
            f"ARIA : role={control.role or 'absent'} · autocomplete={control.aria_autocomplete or 'absent'} · haspopup={control.aria_haspopup or 'absent'} · controls={control.aria_controls or 'absent'} · owns={control.aria_owns or 'absent'} · active={control.aria_activedescendant or 'absent'} · expanded={control.aria_expanded or 'absent'}",
            f"Attribut autocomplete={control.autocomplete_attribute or 'absent'} · frame={control.frame_label} · window.YUI disponible={'oui' if control.yui_global_available else 'non'} (cela ne confirme pas à lui seul l'attachement du composant)",
            "Saisie de test : frappes clavier séquentielles Playwright; pas d'endpoint direct. Les écouteurs privés du composant ne sont pas introspectés.",
            f"Conteneur(s) visible(s) : {len(observation.containers)}",
        ]
        for index, container in enumerate(observation.containers, 1):
            lines.append(
                f"  {index}. <{container.tag_name}> id={container.element_id or 'absent'} "
                f"classes={','.join(container.class_names) or 'aucune'} role={container.role or 'absent'} "
                f"aria-expanded={container.aria_expanded or 'absent'} "
                f"visible={'oui' if container.visible else 'non'} relation={container.relation}"
            )
        lines.append(f"Suggestions visibles : {len(observation.suggestions)}")
        for suggestion in observation.suggestions:
            lines.append(
                f"  {suggestion.index + 1}. <{suggestion.tag_name}> role={suggestion.role or 'absent'} "
                f"classes={','.join(suggestion.class_names) or 'aucune'} "
                f"aria-selected={suggestion.aria_selected or 'absent'} "
                f"data-attrs={','.join(suggestion.data_attribute_names) or 'aucun'} "
                f"sélectionnable={'oui' if suggestion.safe_to_select else 'non'}"
            )
        if observation.nearby_controls:
            lines.append("Contrôles voisins (structure/attributs, jamais leur valeur) :")
            lines.extend(f"  {item}" for item in observation.nearby_controls)
        lines.append("Après sélection, le test s'arrête. La fermeture/réinitialisation est une action explicite distincte.")
        return "\n".join(lines)

    def _fill_suggestions(self, suggestions: tuple[Any, ...]) -> None:
        self.suggestion_list.delete(0, "end")
        for suggestion in suggestions:
            suffix = "" if suggestion.safe_to_select else " [observation seule — non cliquable]"
            self.suggestion_list.insert("end", f"{suggestion.text}{suffix}")

    def _set_details(self, text: str) -> None:
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", text)
        self.details.configure(state="disabled")

    def _update_buttons(self) -> None:
        busy = self._future is not None
        selected = self.suggestion_list.curselection()
        selection_safe = bool(
            selected
            and int(selected[0]) < len(self._suggestions)
            and self._suggestions[int(selected[0])].safe_to_select
        )
        self.test_button.configure(state="disabled" if busy or self._token else "normal")
        self.field_box.configure(state="disabled" if busy or self._token else "readonly")
        self.query_entry.configure(state="disabled" if busy or self._token else "normal")
        self.select_button.configure(
            state="normal" if not busy and self._token and selection_safe and not self._selection_done else "disabled"
        )
        self.reset_button.configure(state="normal" if not busy and self._token else "disabled")

    def _on_close(self) -> None:
        if self._future is not None:
            self.status.configure(text="Attendez la fin de l'opération en cours; aucun bouton de recherche ne sera activé.")
            return
        if self._token:
            if not messagebox.askyesno(
                "Réinitialiser le test",
                "Une valeur temporaire est encore dans le champ. L'effacer avant de fermer ?",
                parent=self.window,
            ):
                return
            self._close_after_reset = True
            self._reset()
            return
        self.window.destroy()
